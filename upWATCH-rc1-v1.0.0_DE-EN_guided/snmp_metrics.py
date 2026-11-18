"""Safe SNMP counter-delta computation with reset and wrap rejection."""
def rate(previous,current,elapsed,bits=64,uptime_before=None,uptime_after=None):
    if elapsed<=0 or current<0 or previous<0 or bits not in (32,64):return None
    if uptime_before is not None and uptime_after is not None and uptime_after<uptime_before:return None
    modulus=1<<bits
    if max(previous,current)>=modulus:return None
    delta=(current-previous)%modulus
    # A delta larger than half of a counter range is ambiguous (reset/multiple wraps).
    if delta>=modulus//2:return None
    return round(delta*8/elapsed,2)
