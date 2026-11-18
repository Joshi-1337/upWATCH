"""Network boundary and browser-origin protections; no external dependencies."""
import http.client
import ipaddress
import os
import re
import smtplib
import socket
import ssl
import time
import urllib.parse

DOMAIN=os.environ.get('UPWATCH_DOMAIN','').strip().lower()
if DOMAIN and (len(DOMAIN)>253 or '.' not in DOMAIN or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',part) for part in DOMAIN.split('.'))):
    raise RuntimeError('UPWATCH_DOMAIN muss ein Subdomainname ohne https://, Port oder Pfad sein.')
# A configured subdomain automatically enables the complete public security boundary.
PUBLIC = bool(DOMAIN) or os.environ.get('PUBLIC_MODE','false').lower()=='true'
ORIGIN = 'https://'+DOMAIN if DOMAIN else os.environ.get('PUBLIC_ORIGIN','').rstrip('/')
TRUST_PROXY = bool(DOMAIN) or os.environ.get('TRUST_PROXY','false').lower()=='true'
COOKIE_SECURE = bool(DOMAIN) or os.environ.get('COOKIE_SECURE','false').lower()=='true'
COOKIE_NAME = '__Host-upwatch' if COOKIE_SECURE else 'upwatch_session'
ALLOWED_HOSTS = {x.strip().lower() for x in os.environ.get('ALLOWED_HOSTS','localhost,127.0.0.1,[::1]').split(',') if x.strip()}
TRUSTED_PROXIES=tuple(ipaddress.ip_network(x.strip()) for x in os.environ.get('TRUSTED_PROXY_CIDRS','').split(',') if x.strip())
# Opt-in direct HTTP access for a designated LAN address; proxy HTTPS stays isolated.
LAN_DIRECT_HOST=os.environ.get('UPWATCH_LAN_HOST','').strip()
LAN_CLIENT_CIDRS=tuple(ipaddress.ip_network(x.strip(),strict=False) for x in os.environ.get('UPWATCH_LAN_CLIENT_CIDRS','').split(',') if x.strip())
if LAN_DIRECT_HOST:
    try:
        lan_ip=ipaddress.ip_address(LAN_DIRECT_HOST)
        if not lan_ip.is_private or lan_ip.is_loopback or lan_ip.is_link_local:
            raise ValueError()
        if not LAN_CLIENT_CIDRS:
            raise ValueError('UPWATCH_LAN_CLIENT_CIDRS erforderlich')
    except ValueError as exc:
        raise RuntimeError('UPWATCH_LAN_HOST muss eine private LAN-IP sein; UPWATCH_LAN_CLIENT_CIDRS setzen.') from exc
LAN_COOKIE_NAME='upwatch_lan_session'
LAN_PORT=8080

def direct_lan_request(env):
    if not LAN_DIRECT_HOST:
        return False
    host=env.get('HTTP_HOST','')
    if host != f'{LAN_DIRECT_HOST}:{LAN_PORT}':
        return False
    try:
        peer=ipaddress.ip_address(env.get('REMOTE_ADDR',''))
        if getattr(peer,'ipv4_mapped',None): peer=peer.ipv4_mapped
        return any(peer in network for network in LAN_CLIENT_CIDRS if peer.version==network.version)
    except ValueError:
        return False

def cookie_name(env):
    return LAN_COOKIE_NAME if direct_lan_request(env) else COOKIE_NAME

ALLOW_CIDRS = tuple(ipaddress.ip_network(x.strip()) for x in os.environ.get('MONITOR_ALLOWED_CIDRS','').split(',') if x.strip())
BLOCKED_TARGETS={ipaddress.ip_address(x) for x in ('168.63.129.16','100.100.100.200','192.0.0.192','fd00:ec2::254','fd20:ce::254')}
PRIVATE_NETS = tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7'))
if PUBLIC:
    parsed=urllib.parse.urlsplit(ORIGIN)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None,443):
        raise RuntimeError('PUBLIC_MODE benötigt PUBLIC_ORIGIN=https://deine-subdomain.example ohne Pfad.')
    if not COOKIE_SECURE or not TRUST_PROXY:
        raise RuntimeError('PUBLIC_MODE benötigt COOKIE_SECURE=true und TRUST_PROXY=true hinter einem isolierten HTTPS-Proxy.')
    if not TRUSTED_PROXIES:
        raise RuntimeError('Öffentlicher Betrieb benötigt TRUSTED_PROXY_CIDRS mit der IP des Reverse Proxys, z. B. 192.168.1.10/32.')
    ORIGIN='https://'+parsed.hostname.lower()
    ALLOWED_HOSTS={parsed.hostname.lower()}


def trusted_peer(env):
    try:
        ip=ipaddress.ip_address(env.get('REMOTE_ADDR','127.0.0.1'))
        if getattr(ip,'ipv4_mapped',None): ip=ip.ipv4_mapped
        return any(ip in net for net in TRUSTED_PROXIES if net.version==ip.version)
    except ValueError:
        return False


def client_key(env):
    # Only enable TRUST_PROXY when every request is from an isolated, sanitizing proxy.
    raw=env.get('HTTP_X_REAL_IP','') if TRUST_PROXY and trusted_peer(env) else env.get('REMOTE_ADDR','127.0.0.1')
    try:
        address=ipaddress.ip_address(raw)
        if address.version==6:
            return str(ipaddress.ip_network(str(address)+'/64',strict=False))
        return str(address)
    except ValueError:
        return 'invalid-client'


def request_guard(env):
    """Return (status, message) or None. Health is non-sensitive and proxy-independent."""
    path=env.get('PATH_INFO','/')
    if path=='/healthz': return None
    lan=direct_lan_request(env)
    # A proxy must never be able to impersonate the direct-LAN origin.
    if lan and trusted_peer(env):
        return 403,'LAN-Host über Reverse Proxy nicht erlaubt.'
    if PUBLIC and not (trusted_peer(env) or lan):
        print('Untrusted proxy peer '+repr(env.get('REMOTE_ADDR','unbekannt')),flush=True)
        return 403,'Direkter Zugriff nicht erlaubt. Verbindung muss vom konfigurierten Reverse Proxy kommen.'
    host=env.get('HTTP_HOST',env.get('SERVER_NAME','localhost')).lower()
    try:
        parsed=urllib.parse.urlsplit('//'+host)
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
            return 400,'Ungültiger Host.'
        port=parsed.port
        name='['+parsed.hostname+']' if ':' in parsed.hostname else parsed.hostname
    except ValueError:
        return 400,'Ungültiger Host.'
    if not lan and (name not in ALLOWED_HOSTS or (PUBLIC and port not in (None,443))):
        return 421,'Host nicht zugelassen.'
    if PUBLIC and not lan and env.get('HTTP_X_FORWARDED_PROTO')!='https':
        return 400,'HTTPS-Proxy erforderlich.'
    if env.get('HTTP_SEC_FETCH_SITE')=='cross-site':
        # Allow only top-level, read-only document navigation from external sites.
        # Cross-site subresource and state-changing requests remain blocked.
        is_navigation = (
            env.get('REQUEST_METHOD') == 'GET'
            and env.get('HTTP_SEC_FETCH_MODE') == 'navigate'
            and env.get('HTTP_SEC_FETCH_DEST') == 'document'
        )
        if not is_navigation:
            return 403,'Fremder Ursprung nicht erlaubt.'
    if env.get('REQUEST_METHOD') in ('POST','PUT','DELETE','PATCH'):
        expected=('http://'+LAN_DIRECT_HOST+':'+str(LAN_PORT)) if lan else ORIGIN if PUBLIC else ('https' if COOKIE_SECURE else 'http')+'://'+host
        origin=env.get('HTTP_ORIGIN')
        if ((PUBLIC or lan) and not origin) or (origin and origin!=expected):
            return 403,'Fremder oder fehlender Ursprung.'
    return None


def permitted(address):
    ip=ipaddress.ip_address(address)
    if getattr(ip,'ipv4_mapped',None): ip=ip.ipv4_mapped
    # Never permit metadata/link-local, multicast, unspecified or reserved targets.
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved or getattr(ip,'is_site_local',False) or getattr(ip,'sixtofour',None) or getattr(ip,'teredo',None) or ip in BLOCKED_TARGETS:
        return False
    if any(ip in net for net in ALLOW_CIDRS if net.version==ip.version):
        return True
    if ip.is_loopback: return False
    if ip.is_global: return True
    return not PUBLIC and any(ip in net for net in PRIVATE_NETS if net.version==ip.version)


def resolve(host,port):
    if '%' in host or any(ord(ch)<33 for ch in host):
        raise ValueError('Zieladresse nicht erlaubt.')
    entries=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    if not entries or any(not permitted(e[4][0]) for e in entries):
        raise ValueError('Ziel-IP durch Netzwerkregel gesperrt. MONITOR_ALLOWED_CIDRS prüfen.')
    return entries


def connect_entries(entries,timeout):
    error=None
    deadline=time.monotonic()+timeout
    for family,kind,proto,_,address in entries:
        remaining=deadline-time.monotonic()
        if remaining<=0: raise TimeoutError('Verbindungs-Timeout.')
        sock=socket.socket(family,kind,proto)
        try:
            sock.settimeout(remaining)
            sock.connect(address) # Numeric, validated sockaddr: no second DNS resolution.
            return sock
        except OSError as exc:
            error=exc;sock.close()
    raise error or OSError('Keine Zieladresse.')


def connect(host,port,timeout):
    return connect_entries(resolve(host,port),timeout)


class PinnedResponse:
    def __init__(self,response,connection):
        self.status=response.status;self.response=response;self.connection=connection
    def close(self):
        self.response.close();self.connection.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class HTTPClient:
    def open(self,request,timeout):
        u=urllib.parse.urlsplit(request.full_url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError('Ungültige HTTP-Zieladresse.')
        host=u.hostname.encode('idna').decode('ascii')
        port=u.port or (443 if u.scheme=='https' else 80)
        addresses=resolve(host,port)
        cls=http.client.HTTPSConnection if u.scheme=='https' else http.client.HTTPConnection
        conn=cls(host,port,timeout=timeout)
        # HTTPSConnection retains the original hostname for certificate checks and SNI.
        conn._create_connection=lambda address,timeout,*args,**kwargs: connect_entries(addresses,timeout)
        try:
            path=urllib.parse.urlunsplit(('', '', u.path or '/',u.query,''))
            conn.request('GET',path,headers=dict(request.header_items()))
            return PinnedResponse(conn.getresponse(),conn)
        except BaseException:
            conn.close();raise


class SMTPClient(smtplib.SMTP):
    def _get_socket(self,host,port,timeout):
        return connect(host,port,timeout)


class SMTPSSLClient(smtplib.SMTP_SSL):
    def _get_socket(self,host,port,timeout):
        sock=connect(host,port,timeout)
        try:return self.context.wrap_socket(sock,server_hostname=self._host)
        except BaseException:
            sock.close();raise
