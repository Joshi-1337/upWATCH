"""SNMPv3 authPriv GET: one numeric OID, pinned address, no untrusted DNS on UDP transport."""
import asyncio, ipaddress, re
from vault import decrypt
from storage import db
import security

OID_RE=re.compile(r'^[0-9]+(?:\.[0-9]+){1,63}$')

def validate_oid(oid):
    if not OID_RE.fullmatch(str(oid)):raise ValueError('Ungültige numerische OID')
    return str(oid)

def profile(c, ident):
    row=c.execute('SELECT * FROM snmp_profiles WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('SNMP-Profil nicht gefunden')
    return dict(row)

async def _get(target,port,timeout,oid,p):
    from pysnmp.hlapi.v3arch.asyncio import (SnmpEngine, UsmUserData, UdpTransportTarget, Udp6TransportTarget,
         ContextData, ObjectType, ObjectIdentity, get_cmd, USM_AUTH_HMAC96_SHA, USM_AUTH_HMAC192_SHA256,
         USM_PRIV_CFB128_AES)
    auth={'sha1':USM_AUTH_HMAC96_SHA,'sha256':USM_AUTH_HMAC192_SHA256}[p['auth_protocol']]
    transport=Udp6TransportTarget if ipaddress.ip_address(target).version==6 else UdpTransportTarget
    engine=SnmpEngine()
    try:
        credentials=UsmUserData(p['username'],authKey=decrypt(p['auth_enc']),privKey=decrypt(p['priv_enc']),authProtocol=auth,privProtocol=USM_PRIV_CFB128_AES)
        response=await asyncio.wait_for(get_cmd(engine,credentials,await transport.create((target,port),timeout=timeout,retries=0),ContextData(),ObjectType(ObjectIdentity(oid)),lookupMib=False),timeout=timeout+1)
        indication,status,index,values=response
        if indication:raise RuntimeError('SNMP_TRANSPORT_OR_AUTH_ERROR')
        if status:raise RuntimeError('SNMP_AGENT_ERROR')
        if not values:raise RuntimeError('SNMP_EMPTY_RESPONSE')
        return values[0][1].prettyPrint()
    finally:
        engine.close_dispatcher()

def parse_uint(value):
    if not str(value).isdigit():raise ValueError('SNMP_COUNTER_INVALID')
    return int(value)

def snmp_get(target,cfg,timeout):
    interface=cfg.get('mode')=='interface'
    index=int(cfg.get('if_index',0)) if interface else 0
    if interface and not 1<=index<=2147483647:raise ValueError('Ungültiger Interface-Index')
    oid=validate_oid(f'1.3.6.1.2.1.31.1.1.1.6.{index}' if interface else cfg.get('oid',''))
    port=int(cfg.get('port',161))
    if not 1<=port<=65535:raise ValueError('Ungültiger SNMP-Port')
    # security.resolve() validates ALL addresses; operate only on selected numeric IP.
    entries=security.resolve(target,port)
    ip=entries[0][4][0]
    with db() as c:p=profile(c,cfg.get('profile_id'))
    value=asyncio.run(_get(ip,port,timeout,oid,p))
    if interface:
        in_value=parse_uint(value)
        out_oid=f'1.3.6.1.2.1.31.1.1.1.10.{index}'
        out_value=parse_uint(asyncio.run(_get(ip,port,timeout,out_oid,p)))
        # sysUpTime lets us reject counter-rate spikes after device reboots.
        try:uptime=parse_uint(asyncio.run(_get(ip,port,timeout,'1.3.6.1.2.1.1.3.0',p)))
        except Exception:uptime=None  # Counters still work; resets become less observable.
        # Persist baseline and compute rates only once the first sample exists.
        import time
        from snmp_metrics import rate
        now=time.time();metrics={'interface_index':index,'in_octets':in_value,'out_octets':out_value,'sys_uptime_ticks':uptime}
        mid=cfg.get('_monitor_id')
        if mid:
            with db() as c:
                for name,numeric,the_oid in [('in',in_value,oid),('out',out_value,out_oid)]:
                    previous=c.execute('SELECT value,observed,uptime FROM snmp_counters WHERE monitor_id=? AND oid=?',(mid,the_oid)).fetchone()
                    if previous:
                        val=rate(int(previous['value']),numeric,now-previous['observed'],uptime_before=previous['uptime'],uptime_after=uptime)
                        if val is not None:metrics[name+'_bps']=val
                    c.execute('INSERT INTO snmp_counters(monitor_id,oid,value,observed,uptime) VALUES(?,?,?,?,?) ON CONFLICT(monitor_id,oid) DO UPDATE SET value=excluded.value, observed=excluded.observed,uptime=excluded.uptime',(mid,the_oid,numeric,now,uptime))
        return metrics
    expected=cfg.get('expected_value')
    if expected is not None and str(value)!=str(expected):raise ValueError('SNMP_VALUE_MISMATCH')
    return {'oid':oid,'value':value,'profile_id':p['id']}
