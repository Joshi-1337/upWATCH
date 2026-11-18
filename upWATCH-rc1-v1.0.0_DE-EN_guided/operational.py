"""Presentation-only freshness and bounded retention settings."""
import os,time

def worker_healthy(updated, now=None, max_age=15):
    now=time.time() if now is None else now
    return updated is not None and 0 <= now-float(updated) <= max_age

def monitor_stale(m, now=None, worker_ok=True):
    now=time.time() if now is None else now
    if not worker_ok:return True
    if m.get('checked') is None:return True
    interval=max(10,int(m.get('interval') or 60))
    return now-float(m['checked']) > max(60,interval*3)

def retention_days(name, default, minimum, maximum):
    try:return max(minimum,min(maximum,int(os.environ.get(name,default))))
    except (ValueError,TypeError):return default
