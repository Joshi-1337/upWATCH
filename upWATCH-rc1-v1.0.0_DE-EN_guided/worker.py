"""Independent monitoring service; only one worker instance is supported."""
import json, time, threading, signal, os
from concurrent.futures import ThreadPoolExecutor
from storage import db, initialize, event, maintenance_active, dependency_blocked
from probes.engine import check
from notifications import enqueue, drain
from operational import retention_days
STOP=threading.Event()

def record(mid, revision, result):
    with db() as c:
        # Transaction started by PostgreSQL connection
        m=c.execute('SELECT * FROM monitors WHERE id=? AND revision=? FOR UPDATE',(mid,revision)).fetchone()
        if not m:return False
        now=result['checked']; ok=result['status']=='up';warn=result['status']=='warning'
        fails=0 if ok else m['failure_count']+1
        rec=m['recovery_count']+1 if ok else 0
        previous=m['status']
        new=previous
        if ok:
            if previous not in ('up',None) and rec<int(m['recovery_threshold']):new=previous
            else:new='up'
        elif fails>=int(m['failure_threshold']):new='warning' if warn else 'down'
        if previous is None and ok:new='up'
        # Mark initial failure as pending; unconfirmed failures don't trigger notifications.
        c.execute('UPDATE monitors SET status=?,checked=?,latency=?,code=?,detail=?,failure_count=?,recovery_count=?,next_check=? WHERE id=?',
                  (new,now,result['latency'],result['code'],result['detail'],fails,rec,now+m['interval'],mid))
        c.execute('INSERT INTO checks(monitor_id,checked,status,latency,code,error_code,detail,metrics) VALUES(?,?,?,?,?,?,?,?)',
                  (mid,now,result['status'],result['latency'],result['code'],result['error_code'],result['detail'],json.dumps(result['metrics'])))
        for period,span in [('hour',3600),('day',86400)]:
            bucket=int(now//span*span)
            c.execute('''INSERT INTO rollups(monitor_id,period,bucket,total,success,latency_sum,latency_count,latency_min,latency_max)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(monitor_id,period,bucket) DO UPDATE SET
            total=rollups.total+excluded.total, success=rollups.success+excluded.success,
            latency_sum=rollups.latency_sum+excluded.latency_sum,latency_count=rollups.latency_count+excluded.latency_count,
            latency_min=LEAST(rollups.latency_min,excluded.latency_min),latency_max=GREATEST(rollups.latency_max,excluded.latency_max)''',
            (mid,period,bucket,1,int(ok),result['latency'],1,result['latency'],result['latency']))
        # DDNS changes are informational, never outages merely because an IP changed.
        if m['kind'] in ('ddns_bundle','wan_dns') and result['error_code'] is None:
            old=c.execute('SELECT metrics FROM checks WHERE monitor_id=? AND checked<? ORDER BY checked DESC,id DESC LIMIT 1',(mid,now)).fetchone()
            if old:
                try:
                    before_metrics=json.loads(old['metrics'])
                    before=sorted(before_metrics.get('ddns_ipv4',[]) or ([before_metrics['egress_ipv4']] if before_metrics.get('egress_ipv4') else []))
                    after=sorted(result['metrics'].get('ddns_ipv4',[]) or ([result['metrics']['egress_ipv4']] if result['metrics'].get('egress_ipv4') else []))
                    if before and after and before!=after:
                        event(c,mid,'dns.ip_changed','info',f'DDNS IPv4 geändert: {", ".join(before)} -> {", ".join(after)}')
                except (ValueError,TypeError,KeyError):pass
        # Incident lifecycle and notification lifecycle are independent. A child may
        # stay DOWN while its parent recovers: queue its delayed DOWN alarm only
        # after a fresh child probe confirms the outage remains.
        if new != previous and new is not None:
            event(c,mid,'monitor.'+new,'info' if new=='up' else 'warning' if new=='warning' else 'critical',result['detail'])
        if new == 'down':
            if previous != 'down':
                c.execute('INSERT INTO incidents(monitor_id,started,reason) VALUES(?,?,?) ON CONFLICT DO NOTHING',
                          (mid,now,result['error_code'] or result['detail']))
            incident=c.execute('SELECT id,alert_queued,alert_deferred FROM incidents WHERE monitor_id=? AND ended IS NULL',(mid,)).fetchone()
            if incident and not incident['alert_queued'] and m['notify_down']:
                blocked=maintenance_active(c,mid,now) or dependency_blocked(c,mid)
                if blocked:
                    if not incident['alert_deferred']:
                        c.execute('UPDATE incidents SET alert_deferred=1 WHERE id=?',(incident['id'],))
                        event(c,mid,'monitor.alert_deferred','info','DOWN-Benachrichtigung während Wartung oder Elternausfall zurückgestellt')
                elif enqueue(c,m,'down',now,result['detail']):
                    c.execute('UPDATE incidents SET alert_queued=1 WHERE id=?',(incident['id'],))
                    if incident['alert_deferred']:
                        event(c,mid,'monitor.alert_released','warning','Nach frischer Prüfung: weiterhin DOWN; Alarm jetzt in SMTP-Outbox')
        elif new == 'up':
            # Close an open DOWN incident even after DOWN -> WARNING -> UP.
            incident=c.execute('SELECT id,alert_queued FROM incidents WHERE monitor_id=? AND ended IS NULL',(mid,)).fetchone()
            if incident:
                c.execute('UPDATE incidents SET ended=? WHERE id=?',(now,incident['id']))
                # Only send recovery if a DOWN alarm was actually queued.
                if incident['alert_queued'] and not (maintenance_active(c,mid,now) or dependency_blocked(c,mid)):
                    enqueue(c,m,'up',now,result['detail'])
        return True

def run_one(m):
    try:
        result=check(dict(m))
        if record(m['id'],m['revision'],result):
            with db() as c:
                c.execute('INSERT INTO worker_heartbeat(name,updated) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET updated=excluded.updated',('last_completed',time.time()))
    except Exception as exc:
        try:
            with db() as c:
                event(c,m['id'],'scheduler.error','critical',f'{type(exc).__name__}: {str(exc)[:150]}')
                c.execute('INSERT INTO worker_heartbeat(name,updated) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET updated=excluded.updated',('last_error',time.time()))
        except Exception as store_error:
            print('worker: cannot persist probe error:',repr(store_error),flush=True)
        print('worker probe failed:',repr(exc),flush=True)

def tick(pool, running):
    now=time.time()
    with db() as c:
        c.execute('INSERT INTO worker_heartbeat(name,updated) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET updated=excluded.updated',('worker',now))
        rows=c.execute('SELECT * FROM monitors WHERE next_check<=? ORDER BY next_check,position LIMIT 100',(now,)).fetchall()
    # Remove completed jobs before capacity calculation to prevent starvation.
    for mid,fut in list(running.items()):
        if fut.done():running.pop(mid,None)
    # Never enqueue beyond the executor capacity; keep overdue jobs visible.
    available=max(0,8-len(running))
    for m in rows:
        if m['id'] in running or available<=0:continue
        with db() as c:
            # Claim before scheduling; crash recovery sees the next slot, not a tight loop.
            claimed=c.execute('UPDATE monitors SET next_check=? WHERE id=? AND revision=? AND next_check<=?',
                              (now+m['interval'],m['id'],m['revision'],now)).rowcount
        if not claimed:continue
        fut=pool.submit(run_one,m);running[m['id']]=fut;available-=1

def retention():
    with db() as c:
        c.execute("DELETE FROM mail_outbox WHERE status='sent' AND sent_at<?",(time.time()-retention_days('UPWATCH_SENT_MAIL_RETENTION_DAYS',90,7,3650)*86400,))
        c.execute("DELETE FROM mail_outbox WHERE status='failed' AND created<?",(time.time()-retention_days('UPWATCH_FAILED_MAIL_RETENTION_DAYS',180,30,3650)*86400,))
        c.execute('DELETE FROM checks WHERE checked<?',(time.time()-retention_days('UPWATCH_CHECK_RETENTION_DAYS',30,7,365)*86400,))
        c.execute("DELETE FROM rollups WHERE (period='hour' AND bucket<?) OR (period='day' AND bucket<?)",
                  (time.time()-retention_days('UPWATCH_HOURLY_RETENTION_DAYS',365,30,1095)*86400,time.time()-retention_days('UPWATCH_DAILY_RETENTION_DAYS',1095,365,3650)*86400))
        c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        c.execute('DELETE FROM audit WHERE created<?',(time.time()-retention_days('UPWATCH_AUDIT_RETENTION_DAYS',365,30,3650)*86400,))
        c.execute('DELETE FROM events WHERE created<?',(time.time()-retention_days('UPWATCH_EVENT_RETENTION_DAYS',365,30,3650)*86400,))

def main():
    initialize()
    signal.signal(signal.SIGTERM,lambda *_:STOP.set())
    signal.signal(signal.SIGINT,lambda *_:STOP.set())
    running={};last_cleanup=0;last_mail=0
    with ThreadPoolExecutor(max_workers=8,thread_name_prefix='upwatch-probe') as pool:
        while not STOP.is_set():
            try:
                tick(pool,running)
                if time.time()-last_mail>=5:
                    drain();last_mail=time.time()
                if time.time()-last_cleanup>3600:retention();last_cleanup=time.time()
            except Exception as exc:print('worker error:',repr(exc),flush=True)
            STOP.wait(1)
if __name__=='__main__':main()
