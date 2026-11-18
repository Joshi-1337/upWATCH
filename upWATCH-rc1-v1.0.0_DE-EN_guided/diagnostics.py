"""Authenticated operational overview. No stored credentials or raw SMTP bodies."""
import time, os


def snapshot(c, now=None):
    now=time.time() if now is None else now
    hb=c.execute("SELECT updated FROM worker_heartbeat WHERE name='worker'").fetchone()
    completed=c.execute("SELECT updated FROM worker_heartbeat WHERE name='last_completed'").fetchone()
    last_error=c.execute("SELECT updated FROM worker_heartbeat WHERE name='last_error'").fetchone()
    overdue=c.execute('SELECT COUNT(*) FROM monitors WHERE next_check<?',(now-30,)).fetchone()[0]
    due=c.execute('SELECT COUNT(*) FROM monitors').fetchone()[0]
    age=round(max(0,now-hb['updated']),1) if hb else None
    queues={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) AS n FROM mail_outbox GROUP BY status')}
    statuses={str(r['status'] or 'pending'):r['n'] for r in c.execute('SELECT status,COUNT(*) AS n FROM monitors GROUP BY status')}
    latest=c.execute('SELECT MAX(checked) FROM checks').fetchone()[0]
    errors=[dict(r) for r in c.execute("SELECT created,monitor_id,event_type,severity,detail FROM events WHERE severity IN ('critical','warning') ORDER BY created DESC LIMIT 20")]
    try: db_bytes=c.execute('SELECT pg_database_size(current_database())').fetchone()[0]
    except Exception:db_bytes=None
    return dict(worker_online=age is not None and age<15,worker_age_sec=age,
                worker_completed_at=completed['updated'] if completed else None,
                worker_last_error_at=last_error['updated'] if last_error else None,
                overdue_monitors=overdue,
                worker_stalled=bool(due and age is not None and age<15 and ((completed and now-completed['updated']>300) or (not completed and latest is None and overdue>0))),
                last_probe_at=latest,monitors=statuses,mail_outbox=queues,
                open_incidents=c.execute('SELECT COUNT(*) FROM incidents WHERE ended IS NULL').fetchone()[0],
                deferred_incidents=c.execute('SELECT COUNT(*) FROM incidents WHERE ended IS NULL AND alert_deferred=1 AND alert_queued=0').fetchone()[0],
                db_bytes=db_bytes,recent_warnings=errors)
