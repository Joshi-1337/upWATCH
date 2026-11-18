"""Bounded, read-only SNMPv3 ifName / ifDescr discovery via GETNEXT."""
import asyncio, ipaddress
from probes.snmp import profile
from storage import db
from vault import decrypt
import security

IF_NAME='1.3.6.1.2.1.31.1.1.1.1'
IF_DESCR='1.3.6.1.2.1.2.2.1.2'
MAX_INTERFACES=64

def parse_interfaces(rows):
    """Parse bounded OID/value tuples; ignore other subtrees and invalid indices."""
    found={}
    for oid,value in rows:
        root=IF_NAME if str(oid).startswith(IF_NAME+'.') else IF_DESCR if str(oid).startswith(IF_DESCR+'.') else None
        if not root:continue
        suffix=str(oid)[len(root)+1:]
        if not suffix.isdigit() or not 1<=int(suffix)<=2147483647:continue
        val=str(value)[:120].replace('\x00','')
        item=found.setdefault(int(suffix),{'if_index':int(suffix),'name':'','description':''})
        item['name' if root==IF_NAME else 'description']=val
    return sorted(found.values(),key=lambda x:x['if_index'])[:MAX_INTERFACES]

async def _walk(ip,port,timeout,p):
    from pysnmp.hlapi.v3arch.asyncio import (SnmpEngine,UsmUserData,UdpTransportTarget,Udp6TransportTarget,ContextData,ObjectType,ObjectIdentity,walk_cmd,
        USM_AUTH_HMAC96_SHA,USM_AUTH_HMAC192_SHA256,USM_PRIV_CFB128_AES)
    engine=SnmpEngine()
    try:
        auth={'sha1':USM_AUTH_HMAC96_SHA,'sha256':USM_AUTH_HMAC192_SHA256}[p['auth_protocol']]
        credentials=UsmUserData(p['username'],authKey=decrypt(p['auth_enc']),privKey=decrypt(p['priv_enc']),authProtocol=auth,privProtocol=USM_PRIV_CFB128_AES)
        cls=Udp6TransportTarget if ipaddress.ip_address(ip).version==6 else UdpTransportTarget
        transport=await cls.create((ip,port),timeout=timeout,retries=0)
        rows=[]
        for root in (IF_NAME,IF_DESCR):
            count=0
            async for indication,status,index,values in walk_cmd(engine,credentials,transport,ContextData(),ObjectType(ObjectIdentity(root)),lookupMib=False,lexicographicMode=False):
                if indication or status:raise RuntimeError('SNMP_DISCOVERY_FAILED')
                if not values: break
                oid,value=values[0];oid=str(oid)
                if not oid.startswith(root+'.'):break
                rows.append((oid,value.prettyPrint()))
                count+=1
                if count>=MAX_INTERFACES:break
        return rows
    finally:engine.close_dispatcher()

def discover(target,profile_id,port=161,timeout=5):
    if not 1<=int(port)<=65535 or not 1<=int(timeout)<=10:raise ValueError('Ungültiger Port oder Timeout')
    # Use security policy for every address and pin the chosen transport IP.
    entries=security.resolve(target,int(port));ip=entries[0][4][0]
    with db() as c:p=profile(c,profile_id)
    rows=asyncio.run(asyncio.wait_for(_walk(ip,int(port),int(timeout),p),timeout=12))
    return parse_interfaces(rows)
