"""Bounded CNAME group checks with optional egress-address comparison."""
import ipaddress
import socket
import time
import urllib.request
import dns.resolver
import dns.exception
import security

RESOLVER_IPS = {'system': None, 'cloudflare': '1.1.1.1', 'google': '8.8.8.8'}

def canonical(name):
    return str(name).rstrip('.').lower()

def resolver_for(which):
    if which not in RESOLVER_IPS:
        raise ValueError('DNS-Resolver unbekannt')
    ip = RESOLVER_IPS[which]
    if ip:
        if not security.permitted(ip):
            raise ValueError('DNS-Resolver nicht freigegeben')
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [ip]
        return resolver
    return dns.resolver.Resolver()

def public_ip(timeout):
    # This measures the LXC's egress IPv4, not necessarily the router's WAN address.
    request = urllib.request.Request('https://api.ipify.org/',headers={'User-Agent': 'upWATCH/2.0', 'Accept-Encoding': 'identity'})
    with security.HTTPClient().open(request,timeout) as response:
        if response.status != 200:
            raise RuntimeError('WAN-IP-Dienst liefert keinen Erfolg')
        raw = response.response.read(65)
    ip = ipaddress.ip_address(raw.decode('ascii').strip())
    if ip.version != 4 or not ip.is_global:
        raise RuntimeError('WAN-IP-Dienst liefert keine öffentliche IPv4')
    return str(ip)

def run(target, config, timeout, *, resolver=None, wan_lookup=None):
    """Return (metrics, summary) or raise BundleError; target is DDNS hostname."""
    start=time.monotonic()
    resolver=resolver or resolver_for(config.get('resolver','system'))
    wan_lookup=wan_lookup or public_ip
    subs=config['subdomains']
    expected=canonical(target)
    failures=[]; results=[]
    # Reserve time for all parts while honoring the shared timeout.
    def lookup(name,typ):
        remain=timeout-(time.monotonic()-start)
        if remain<=0: raise TimeoutError('Gesamtzeitlimit überschritten')
        return resolver.resolve(name,typ,lifetime=min(remain,3.0),raise_on_no_answer=True)
    try:
        ddns_ips=sorted({str(rr.address) for rr in lookup(target,'A')})
    except (dns.exception.DNSException,TimeoutError) as e:
        raise BundleError('DDNS_RESOLUTION_FAILED',f'DDNS-A-Auflösung fehlgeschlagen: {type(e).__name__}') from e
    if not ddns_ips:
        raise BundleError('DDNS_NO_ADDRESS','DDNS-Hostname liefert keine IPv4-Adresse')
    for name in subs:
        entry={'name':name,'status':'up'}
        try:
            records=lookup(name,'CNAME')
            targets=sorted({canonical(rr.target) for rr in records})
            entry['cname']=targets
            if targets != [expected]:
                entry['status']='down';entry['error']='CNAME_MISMATCH'
                failures.append(f'{name}: CNAME-Abweichung')
        except (dns.exception.DNSException,TimeoutError) as e:
            entry['status']='down';entry['error']='CNAME_LOOKUP_FAILED'
            failures.append(f'{name}: {type(e).__name__}')
        results.append(entry)
    metrics={'ddns_host':target,'ddns_ipv4':ddns_ips,'resolver':config.get('resolver','system'),'subdomains':results,'checked_count':len(results),'failed_count':len(failures)}
    if config.get('wan_compare'):
        remain=timeout-(time.monotonic()-start)
        if remain<=0:
            failures.append('WAN-IP: Gesamtzeitlimit überschritten')
        else:
            try:
                wan=wan_lookup(min(remain,3.0))
                metrics['egress_ipv4']=wan
                if wan not in ddns_ips:
                    failures.append('DDNS-IP weicht von der öffentlichen Egress-IP ab')
            except Exception as e:
                failures.append('WAN-IP-Abfrage fehlgeschlagen: '+type(e).__name__)
    if failures:
        metrics['failures']=failures[:30]
        raise BundleError('DDNS_BUNDLE_MISMATCH', '; '.join(failures[:2]) + (f' (+{len(failures)-2} weitere)' if len(failures)>2 else ''),metrics)
    return metrics, f'{len(subs)} CNAME(s) korrekt · DDNS IPv4: {", ".join(ddns_ips[:2])}'

class BundleError(Exception):
    def __init__(self,code,detail,metrics=None):
        super().__init__(detail)
        self.code=code
        self.metrics=metrics or {}
