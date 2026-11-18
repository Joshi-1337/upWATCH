"""Read-only incident summaries; UTC timestamps and elapsed seconds."""
import time

def summary(conn, *, now=None, days=30, monitor_id=None):
    if not isinstance(days,int) or not 1 <= days <= 365: raise ValueError('Ungültiges Zeitfenster')
    now=time.time() if now is None else now
    start=now-days*86400
    where='WHERE i.started < ? AND (i.ended IS NULL OR i.ended > ?)'
    args=[now,start]
    if monitor_id:
        where+=' AND i.monitor_id=?';args.append(monitor_id)
    rows=conn.execute('SELECT i.id,i.monitor_id,i.started,i.ended,i.reason,m.alias FROM incidents i LEFT JOIN monitors m ON m.id=i.monitor_id '+where+' ORDER BY i.started DESC',(args)).fetchall()
    data=[];durations=[];open_count=0
    for r in rows:
        d=dict(r)
        d['duration_sec']=round(max(0,min(now,d['ended'] if d['ended'] is not None else now)-max(start,d['started'])),2)
        d['open']=d['ended'] is None
        if d['open']: open_count+=1
        durations.append(d['duration_sec']);data.append(d)
    return {'period_days':days,'from':start,'to':now,'total':len(data),'open':open_count,'closed':len(data)-open_count,
        'downtime_sec':round(sum(durations),2),'mttr_sec':round(sum(d['duration_sec'] for d in data if not d['open'])/max(1,len(data)-open_count),2) if len(data)>open_count else None,
        'longest_sec':round(max(durations,default=0),2),'incidents':data[:200]}
