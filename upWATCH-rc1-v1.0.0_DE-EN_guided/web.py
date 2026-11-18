"""Minimal authenticated WSGI API. No scheduler in the HTTP process."""
import base64, hashlib, hmac, json, os, re, secrets, time, uuid, urllib.parse
from http import HTTPStatus
from pathlib import Path
from wsgiref.util import FileWrapper
import security
from storage import db, initialize, audit, add_dependency, remove_dependency, event, maintenance_active, dependency_blocked
from manage import digest
from vault import encrypt, decrypt
import mfa
import access
import operational
from maintenance import validate as validate_maintenance
from notifications import smtp_public, config as smtp_config, test_message
ROOT=Path(__file__).resolve().parent/'static'
VERSION='1.0.0-rc1'
MAX_BODY=65536
KINDS={'ping','tcp','udp','http','dns','ddns_bundle','wan_dns','tls','smtp','ssh','snmp'}
class APIError(Exception):
    def __init__(self,status,message):self.status=status;self.message=message

def response(start,status,data,headers=()):
    payload=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
    start(f'{status} {HTTPStatus(status).phrase}',[('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(payload))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),*headers]);return [payload]

def body_json(env):
    try:
        length=int(env.get('CONTENT_LENGTH') or 0)
        if length<0 or length>MAX_BODY:raise APIError(413,'Anfrage zu groß')
        return json.loads(env['wsgi.input'].read(length) or b'{}')
    except (ValueError,json.JSONDecodeError):raise APIError(400,'Ungültiges JSON')

# A normal login has a 12-hour server-side lifetime. Opt-in persistent login
# adds a 30-day cookie lifetime, still backed by a revocable DB session.
NORMAL_SESSION_SECONDS = 12 * 3600
REMEMBER_SESSION_SECONDS = 30 * 86400

def new_session(c, username, remember):
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)
    duration = REMEMBER_SESSION_SECONDS if remember else NORMAL_SESSION_SECONDS
    c.execute('INSERT INTO sessions(token_hash,username,csrf,expires) VALUES(?,?,?,?)',
              (hashlib.sha256(token.encode()).hexdigest(), username, csrf, time.time() + duration))
    return token

def session_cookie(token, remember=False, clear=False, env=None):
    local = env is not None and security.direct_lan_request(env)
    name = security.LAN_COOKIE_NAME if local else security.COOKIE_NAME
    value = f'{name}={token}; Path=/; HttpOnly; SameSite=Strict'
    if security.COOKIE_SECURE and not local:
        value += '; Secure'
    if clear:
        value += '; Max-Age=0'
    elif remember:
        value += f'; Max-Age={REMEMBER_SESSION_SECONDS}'
    return value

def user_session(env):
    from http.cookies import SimpleCookie
    try:
        cookies=SimpleCookie();cookies.load(env.get('HTTP_COOKIE',''))
        token=cookies[security.cookie_name(env)].value
    except (KeyError,ValueError):return None
    key=hashlib.sha256(token.encode()).hexdigest()
    with db() as c:return c.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',(key,time.time())).fetchone()

def validate_endpoint(payload):
    if not isinstance(payload,dict):raise APIError(400,'Ungültiges Endpunkt-Objekt')
    kind=str(payload.get('kind',''))
    if kind not in KINDS:raise APIError(400,'Prüftyp noch nicht unterstützt')
    alias=str(payload.get('alias','')).strip();target=str(payload.get('target','')).strip()
    if not 1<=len(alias)<=80 or not 1<=len(target)<=2048 or any(ord(ch)<32 or ord(ch)==127 for ch in alias+target):raise APIError(400,'Alias oder Ziel ungültig')
    try:
        interval=int(payload.get('interval',60));timeout=int(payload.get('timeout',5));failure=int(payload.get('failure_threshold',3));recovery=int(payload.get('recovery_threshold',2))
        port=int(payload.get('port',161 if kind=='snmp' else 53 if kind=='udp' else 22 if kind=='ssh' else 443));expected=int(payload.get('expected',200))
        warning=float(payload['warning_latency_ms']) if payload.get('warning_latency_ms') not in ('',None) else None
        critical=float(payload['critical_latency_ms']) if payload.get('critical_latency_ms') not in ('',None) else None
    except (ValueError,TypeError,OverflowError):raise APIError(400,'Numerischer Wert ungültig')
    if not 10<=interval<=86400 or not 1<=timeout<interval or not 1<=failure<=20 or not 1<=recovery<=20 or not 1<=port<=65535 or not 100<=expected<=599:raise APIError(400,'Intervall, Timeout, Port oder Schwelle ungültig')
    if any(x is not None and not 1<=x<=3600000 for x in (warning,critical)) or warning and critical and warning>=critical:raise APIError(400,'Latenzgrenzen ungültig')
    cfg={'port':port,'expected':expected}
    if kind=='http':
        parsed=urllib.parse.urlsplit(target)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:raise APIError(400,'Ungültige HTTP-URL')
        mode=str(payload.get('body_mode','none'))
        if mode not in ('none','contains','not_contains','json_key'):raise APIError(400,'Unzulässiger Body-Modus')
        cfg.update(body_mode=mode,body_value=str(payload.get('body_value',''))[:256],json_path=str(payload.get('json_path',''))[:128],json_expected=payload.get('json_expected',''))
    elif kind=='dns':
        rt=str(payload.get('record_type','A')).upper()
        if rt not in ('A','AAAA','MX','TXT','NS','CNAME'):raise APIError(400,'Ungültiger DNS-Typ')
        if not re.fullmatch(r'(?=.{1,253}\.?$)[A-Za-z0-9_*-]+(?:\.[A-Za-z0-9_*-]+)*\.?',target):
            raise APIError(400,'DNS-Ziel ungültig')
        raw=payload.get('dns_expected','')
        if not isinstance(raw,str) or len(raw)>1024:raise APIError(400,'Erwartete DNS-Werte zu lang')
        expected_values=[v.strip() for v in raw.splitlines() if v.strip()]
        if len(expected_values)>12:raise APIError(400,'Zu viele erwartete DNS-Werte')
        cfg.update(record_type=rt,expected_values=expected_values)
    elif kind=='ddns_bundle':
        # Validation must not import the probe (and its optional DNS runtime dependency).
        resolver_names={'system', 'cloudflare', 'google'}
        hostname_pattern=r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*'
        if not re.fullmatch(hostname_pattern,target.rstrip('.')):
            raise APIError(400,'Ungültiger DDNS-Hostname')
        raw=payload.get('ddns_subdomains','')
        if not isinstance(raw,str) or len(raw)>4096:raise APIError(400,'Subdomain-Liste zu lang')
        domains=[x.strip().rstrip('.').lower() for x in raw.splitlines() if x.strip()]
        if not 1<=len(domains)<=30 or len(set(domains))!=len(domains) or any(not re.fullmatch(hostname_pattern,x) for x in domains):
            raise APIError(400,'1 bis 30 eindeutige, gültige Subdomains benötigt')
        if target.rstrip('.').lower() in domains:raise APIError(400,'DDNS-Hostname darf keine eigene Subdomain sein')
        resolver=str(payload.get('ddns_resolver','system'))
        if resolver not in resolver_names:raise APIError(400,'DNS-Resolver ungültig')
        wan=payload.get('wan_compare',False)
        if not isinstance(wan,bool):raise APIError(400,'WAN-IP-Vergleich ungültig')
        cfg.update(subdomains=domains,resolver=resolver,wan_compare=wan)
    elif kind=='wan_dns':
        # Supports either a wildcard zone (*.example.com) or one concrete A hostname.
        hostname_pattern=r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*'
        host=target.rstrip('.').lower()
        base=host[2:] if host.startswith('*.') else host
        if not re.fullmatch(hostname_pattern,base) or '.' not in base or '*' in base:
            raise APIError(400,'Ziel muss *.domain.tld oder ein gültiger DNS-Hostname sein')
        raw=payload.get('ddns_subdomains','')
        if not isinstance(raw,str) or len(raw)>4096:raise APIError(400,'Subdomain-Liste zu lang')
        domains=[x.strip().rstrip('.').lower() for x in raw.splitlines() if x.strip()]
        if len(domains)>30 or len(set(domains))!=len(domains) or any(not re.fullmatch(hostname_pattern,x) or '.' not in x for x in domains):
            raise APIError(400,'Maximal 30 eindeutige, gültige DNS-Namen erlaubt')
        if host in domains:raise APIError(400,'Ziel ist bereits in der Subdomain-Liste')
        resolver=str(payload.get('ddns_resolver','system'))
        if resolver not in {'system','cloudflare','google'}:raise APIError(400,'DNS-Resolver ungültig')
        cfg.update(subdomains=domains,resolver=resolver)
    elif kind=='snmp':
        from probes.snmp import validate_oid
        try:
            profile_id=str(payload.get('profile_id',''))
            if not re.fullmatch(r'[a-f0-9]{32}',profile_id):raise ValueError('SNMP-Profil fehlt')
            oid=validate_oid(payload.get('oid') or '1.3.6.1.2.1.1.3.0')
            port=int(payload.get('port',161))
            if not 1<=port<=65535:raise ValueError('SNMP-Port ungültig')
        except (ValueError,TypeError) as exc:raise APIError(400,str(exc))
        value=payload.get('expected_value')
        if value is not None and len(str(value))>200:raise APIError(400,'SNMP-Vergleichswert zu lang')
        mode=str(payload.get('snmp_mode','value'))
        if mode not in ('value','interface'):raise APIError(400,'SNMP-Modus ungültig')
        try:if_index=int(payload.get('if_index',1))
        except (TypeError,ValueError):raise APIError(400,'Interface-Index ungültig')
        if not 1<=if_index<=2147483647:raise APIError(400,'Interface-Index ungültig')
        cfg={'profile_id':profile_id,'oid':oid,'port':port,'expected_value':value,'mode':mode,'snmp_mode':mode,'if_index':if_index}
    elif kind=='udp':
        if '://' in target or '/' in target or any(c.isspace() for c in target):
            raise APIError(400,'UDP benötigt eine IP oder einen Hostnamen')
        profile=str(payload.get('udp_profile','dns'))
        if profile not in ('dns','ntp','custom'):raise APIError(400,'UDP-Profil ungültig')
        cfg.update(udp_profile=profile)
        if profile=='dns':
            name=str(payload.get('udp_dns_name','example.com')).strip().rstrip('.')
            if not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+',name):
                raise APIError(400,'Ungültiger UDP-DNS-Testname')
            cfg['udp_dns_name']=name
        if profile=='custom':
            a=str(payload.get('udp_payload_hex','')).strip();b=str(payload.get('udp_expect_hex','')).strip()
            if not a or not b or len(a)>1024 or len(b)>1024 or len(a)%2 or len(b)%2:
                raise APIError(400,'UDP-Hexdaten erforderlich (max. 512 Bytes)')
            try:bytes.fromhex(a);bytes.fromhex(b)
            except ValueError:raise APIError(400,'UDP-Hexdaten ungültig')
            cfg.update(udp_payload_hex=a,udp_expect_hex=b)
    elif kind=='smtp':cfg.update(starttls=bool(payload.get('starttls',False)))
    elif kind=='tls':
        cfg.update(warning_days=30,critical_days=7)
    elif kind=='ping' and '://' in target:raise APIError(400,'Ping benötigt einen Hostnamen oder eine IP')
    return dict(alias=alias,kind=kind,target=target,config=json.dumps(cfg),interval=interval,timeout=timeout,
                failure_threshold=failure,recovery_threshold=recovery,warning_latency_ms=warning,critical_latency_ms=critical,
                notify_down=int(bool(payload.get('notify_down',False))),notify_up=int(bool(payload.get('notify_up',False))),maintenance=int(bool(payload.get('maintenance',False))))

def monitor_json(c,m):
    d=dict(m);cfg=json.loads(d.pop('config'))
    d.update(cfg);d['port']=cfg.get('port',443);d['expected']=cfg.get('expected',200)
    for key in ('notify_down','notify_up','maintenance'):d[key]=bool(d[key])
    d['maintenance_active']=maintenance_active(c,d['id'],time.time())
    d['dependency_blocked']=dependency_blocked(c,d['id'])
    d['history']=[dict(x) for x in reversed(c.execute('SELECT checked,status,latency,code FROM checks WHERE monitor_id=? ORDER BY checked DESC LIMIT 60',(d['id'],)).fetchall())]
    last_check=c.execute('SELECT error_code FROM checks WHERE monitor_id=? ORDER BY id DESC LIMIT 1',(d['id'],)).fetchone()
    d['last_error_code']=last_check['error_code'] if last_check else None
    d['checking']=False
    place=c.execute('SELECT group_id,position FROM widget_placement WHERE monitor_id=?',(d['id'],)).fetchone()
    d['group_id']=place['group_id'] if place else None
    d['widget_position']=place['position'] if place else d['position']
    d['tags']=[dict(x) for x in c.execute('SELECT t.id,t.name,t.color FROM widget_tags t JOIN widget_tag_links l ON l.tag_id=t.id WHERE l.monitor_id=? ORDER BY t.name',(d['id'],))]
    return d

def csv_export(c,which,query,allowed_ids=None):
    import csv,io
    start=float(query.get('from',[time.time()-86400])[0]);end=float(query.get('to',[time.time()])[0])
    if end<start or end-start>366*86400:raise APIError(400,'Ungültiger Zeitraum')
    if allowed_ids is not None and which=='audit':raise APIError(403,'Nur Administratoren')
    scope=''
    params=[start,end]
    if allowed_ids is not None:
        if allowed_ids:scope=' AND monitor_id IN ('+','.join('?' for _ in allowed_ids)+')'
        else:scope=' AND 1=0'
        params.extend(sorted(allowed_ids))
    if which=='checks':
        names=['id','monitor_id','checked','status','latency','code','error_code','detail']
        rows=c.execute('SELECT '+','.join(names)+' FROM checks WHERE checked BETWEEN ? AND ?'+scope+' ORDER BY checked DESC LIMIT 10000',params)
    elif which=='events':
        names=['id','monitor_id','created','event_type','severity','detail']
        rows=c.execute('SELECT '+','.join(names)+' FROM events WHERE created BETWEEN ? AND ?'+scope+' ORDER BY created DESC LIMIT 10000',params)
    elif which=='audit':
        names=['id','created','actor','action','object_id','client_ip','success','detail']
        rows=c.execute('SELECT '+','.join(names)+' FROM audit WHERE created BETWEEN ? AND ? ORDER BY created DESC LIMIT 10000',(start,end))
    else:raise APIError(404,'Export nicht vorhanden')
    out=io.StringIO();writer=csv.writer(out);writer.writerow(names)
    for row in rows:
        if allowed_ids is not None and row['monitor_id'] not in allowed_ids:continue
        writer.writerow([("'"+str(v) if str(v).lstrip().startswith(('=','+','-','@','\t','\r')) else v) if isinstance(v,str) else v for v in row])
    return out.getvalue().encode('utf-8-sig')

def _save_user_grants(c,username,data):
    groups=data.get('group_ids',[]);tags=data.get('tag_ids',[])
    if not isinstance(groups,list) or not isinstance(tags,list) or len(groups)>100 or len(tags)>100 or any(not isinstance(x,str) for x in groups+tags) or len(set(groups))!=len(groups) or len(set(tags))!=len(tags):
        raise APIError(400,'Ungültige Gruppen- oder Tag-Freigaben')
    for ids,table,column in ((groups,'widget_groups','group_id'),(tags,'widget_tags','tag_id')):
        if ids and c.execute('SELECT COUNT(*) FROM '+table+' WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchone()[0]!=len(ids):
            raise APIError(400,'Gruppe oder Tag nicht vorhanden')
    c.execute('DELETE FROM user_group_grants WHERE username=?',(username,))
    c.execute('DELETE FROM user_tag_grants WHERE username=?',(username,))
    c.executemany('INSERT INTO user_group_grants(username,group_id) VALUES(?,?)',[(username,g) for g in groups])
    c.executemany('INSERT INTO user_tag_grants(username,tag_id) VALUES(?,?)',[(username,t) for t in tags])

def application(env,start_response):
    try:return dispatch(env,start_response)
    except APIError as exc:return response(start_response,exc.status,{'error':exc.message})
    except Exception as exc:
        print('web error:',type(exc).__name__,str(exc)[:150],flush=True)
        return response(start_response,500,{'error':'Interner Serverfehler'})

def dispatch(env,start):
    path=env.get('PATH_INFO','/');method=env.get('REQUEST_METHOD','GET')
    guard=security.request_guard(env)
    if guard:raise APIError(*guard)
    if path=='/healthz':
        with db() as c:
            hb=c.execute("SELECT updated FROM worker_heartbeat WHERE name='worker'").fetchone()
        return response(start,200,{'web':'ok','worker':operational.worker_healthy(hb['updated'] if hb else None)})
    if method=='GET' and path in ('/','/index.html','/style.css','/app.js','/i18n.js'):
        fp=ROOT/('index.html' if path=='/' else path[1:]);kind='text/html' if fp.suffix=='.html' else 'text/css' if fp.suffix=='.css' else 'application/javascript'
        content=fp.read_bytes();start('200 OK',[('Content-Type',kind+'; charset=utf-8'),('Content-Length',str(len(content))),('X-Content-Type-Options','nosniff'),('Cache-Control','no-cache')]);return [content]
    if path=='/api/appearance' and method=='GET':
        with db() as c:
            row=c.execute("SELECT value FROM settings WHERE key='appearance'").fetchone()
        return response(start,200,json.loads(row['value']) if row else {'accent':'#a3baa5','scheme':'slate','logo':None})
    if not path.startswith('/api/'):raise APIError(404,'Nicht gefunden')
    data=body_json(env) if method in ('POST','PUT','PATCH','DELETE') else {}
    ip=security.client_key(env)
    if path=='/api/login' and method=='POST':
        username=str(data.get('username',''));password=str(data.get('password',''));remember=(data.get('remember') is True)
        with db() as c:
            count=c.execute('SELECT COUNT(*) FROM audit WHERE action=? AND client_ip=? AND created>? AND success=0',('login',ip,time.time()-900)).fetchone()[0]
            if count>=10:raise APIError(429,'Zu viele Loginversuche; später erneut versuchen')
            user=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            # Same-cost hash even on missing username
            salt=user['salt'] if user else '00'*16
            verified=hmac.compare_digest(digest(password,salt),user['password_hash'] if user else '00'*64)
            audit(c,username[:80] or '?','login',ip=ip,success=verified)
            if not verified:
                c.commit()  # Persist failed login for rate limiting.
                raise APIError(401,'Benutzername oder Passwort falsch')
            if c.execute('SELECT 1 FROM user_mfa WHERE username=?',(username,)).fetchone():
                challenge=secrets.token_urlsafe(32)
                c.execute('INSERT INTO auth_challenges(token_hash,username,expires,client_ip) VALUES(?,?,?,?)',
                          (hashlib.sha256(challenge.encode()).hexdigest(),username,time.time()+180,ip))
                return response(start,200,{'mfa_required':True,'challenge':challenge})
            token=new_session(c,username,remember)
        cookie=session_cookie(token,remember,env=env)
        return response(start,200,{'ok':True},[('Set-Cookie',cookie)])
    if path=='/api/login/mfa' and method=='POST':
        challenge=str(data.get('challenge',''));token=str(data.get('code',''))
        with db() as c:
            row=c.execute('SELECT * FROM auth_challenges WHERE token_hash=? AND expires>? AND client_ip=?',
                          (hashlib.sha256(challenge.encode()).hexdigest(),time.time(),ip)).fetchone()
            if not row or row['attempts']>=5:raise APIError(401,'Anmeldung abgelaufen oder Code ungültig')
            c.execute('UPDATE auth_challenges SET attempts=attempts+1 WHERE token_hash=?',(row['token_hash'],))
            if not mfa.verify_factor(c,row['username'],token):
                audit(c,row['username'],'mfa.login',ip=ip,success=False)
                c.commit()  # Persist challenge attempts; API errors roll back otherwise.
                raise APIError(401,'Code ungültig oder bereits benutzt')
            c.execute('DELETE FROM auth_challenges WHERE username=?',(row['username'],))
            remember=(data.get('remember') is True)
            session_token=new_session(c,row['username'],remember)
            audit(c,row['username'],'mfa.login',ip=ip)
        cookie=session_cookie(session_token,remember,env=env)
        return response(start,200,{'ok':True},[('Set-Cookie',cookie)])
    session=user_session(env)
    if not session:raise APIError(401,'Nicht angemeldet')
    if method not in ('GET','HEAD'):
        if not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),session['csrf']):raise APIError(403,'CSRF-Token fehlt oder ungültig')
    actor=session['username']
    with db() as c:
        role_name=access.role(c,actor)
        is_admin=role_name=='admin'
        allowed=access.visible_ids(c,actor,role_name)
        # Fail closed: unknown routes cannot inherit permissions accidentally.
        if not is_admin:
            self_service=path.startswith('/api/mfa/') or path in ('/api/account','/api/logout','/api/state')
            read_only=(method=='GET' and (path in ('/api/events','/api/incidents','/api/incidents/stats','/api/dependencies','/api/maintenance-windows','/api/reports/availability') or
                       re.fullmatch(r'/api/monitors/[a-f0-9]{32}/(history|trends|diagnostics)',path) or path.startswith('/api/exports/')))
            management=(role_name=='manager' and (path.startswith('/api/endpoints') or path in ('/api/dependencies/bulk','/api/check','/api/widgets/assign','/api/widgets/move','/api/notifications/preview','/api/dependencies','/api/maintenance-windows') or re.fullmatch(r'/api/maintenance-windows/[a-f0-9]{32}',path)))
            collapse=(path=='/api/widget-groups/collapse' and method=='PUT')
            if not (self_service or read_only or management or collapse):raise APIError(403,'Nicht berechtigt')
            # Deny reference to any unassigned monitor before accessing DB or network.
            ref=re.fullmatch(r'/api/monitors/([a-f0-9]{32})/(?:history|trends|diagnostics)',path)
            if ref and ref.group(1) not in allowed:raise APIError(404,'Monitor nicht gefunden')
            endpoint=re.fullmatch(r'/api/endpoints/([a-f0-9]{32})(?:/maintenance)?',path)
            if endpoint and endpoint.group(1) not in allowed:raise APIError(404,'Monitor nicht gefunden')
            if path in ('/api/check','/api/widgets/assign','/api/widgets/move','/api/notifications/preview'):
                mid=str(data.get('monitor_id',''))
                if mid not in allowed:raise APIError(404,'Monitor nicht gefunden')
            if path=='/api/widgets/move' and data.get('before_id') and data['before_id'] not in allowed:
                raise APIError(404,'Zielwidget nicht gefunden')
            if path=='/api/dependencies' and method in ('POST','DELETE'):
                if str(data.get('parent_id','')) not in allowed or str(data.get('child_id','')) not in allowed:raise APIError(403,'Monitor nicht freigegeben')
            if path=='/api/maintenance-windows' and method=='POST' and str(data.get('monitor_id','')) not in allowed:
                raise APIError(403,'Monitor nicht freigegeben')
            if re.fullmatch(r'/api/maintenance-windows/[a-f0-9]{32}',path) and method=='DELETE':
                win=c.execute('SELECT monitor_id FROM maintenance_windows WHERE id=?',(path.rsplit('/',1)[-1],)).fetchone()
                if not win or win['monitor_id'] not in allowed:raise APIError(404,'Wartungsfenster unbekannt')
            if path=='/api/widget-groups/collapse':
                group_id=str(data.get('id',''))
                if not allowed:raise APIError(403,'Gruppe nicht freigegeben')
                linked=c.execute('SELECT 1 FROM widget_placement WHERE group_id=? AND monitor_id IN ('+','.join('?' for _ in allowed)+') LIMIT 1',(group_id,*allowed)).fetchone()
                if not linked:raise APIError(403,'Gruppe nicht freigegeben')
        if path=='/api/appearance' and method=='PUT':
            accent=str(data.get('accent',''))
            if not re.fullmatch(r'#[0-9a-fA-F]{6}',accent):raise APIError(400,'Farbe muss ein HEX-Wert sein')
            logo=data.get('logo')
            if logo is not None:
                if not isinstance(logo,str) or len(logo)>46000:raise APIError(400,'Logo zu groß')
                match=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})',logo)
                if not match:raise APIError(400,'Nur PNG, JPEG oder WebP erlaubt')
                try:raw=base64.b64decode(match.group(2),validate=True)
                except (ValueError,base64.binascii.Error):raise APIError(400,'Bilddaten ungültig')
                if not raw or len(raw)>32768:raise APIError(400,'Logo maximal 32 KiB')
                mime=match.group(1)
                if not (mime=='png' and raw.startswith(b'\x89PNG\r\n\x1a\n') or
                        mime=='jpeg' and raw.startswith(b'\xff\xd8\xff') or
                        mime=='webp' and raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'):
                    raise APIError(400,'Dateiinhalt entspricht nicht dem Bildformat')
            scheme=str(data.get('scheme','slate'))
            if scheme not in ('slate','navy','graphite'):raise APIError(400,'Farbschema ungültig')
            saved={'accent':accent.lower(),'scheme':scheme,'logo':logo}
            c.execute("INSERT INTO settings(key,value) VALUES('appearance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(saved),))
            audit(c,actor,'appearance.changed',ip=ip)
            return response(start,200,saved)
        if path=='/api/mfa/status' and method=='GET':
            enabled=bool(c.execute('SELECT 1 FROM user_mfa WHERE username=?',(actor,)).fetchone())
            return response(start,200,{'enabled':enabled})
        if path=='/api/mfa/setup' and method=='POST':
            account=c.execute('SELECT password_hash,salt FROM users WHERE username=?',(actor,)).fetchone()
            if not account or not hmac.compare_digest(digest(str(data.get('current_password','')),account['salt']),account['password_hash']):raise APIError(403,'Passwort falsch')
            if c.execute('SELECT 1 FROM user_mfa WHERE username=?',(actor,)).fetchone():raise APIError(409,'2FA bereits aktiv')
            sec=mfa.secret()
            c.execute('INSERT INTO mfa_pending(username,secret_enc,expires) VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET secret_enc=excluded.secret_enc,expires=excluded.expires',(actor,encrypt(sec),time.time()+600))
            audit(c,actor,'mfa.setup.begin',ip=ip)
            return response(start,200,{'secret':sec,'uri':mfa.uri(actor,sec),'qr_svg':mfa.qr_svg_data_uri(mfa.uri(actor,sec))})
        if path=='/api/mfa/confirm' and method=='POST':
            row=c.execute('SELECT * FROM mfa_pending WHERE username=? AND expires>?',(actor,time.time())).fetchone()
            if not row:raise APIError(400,'Einrichtung abgelaufen')
            sec=decrypt(row['secret_enc']);step=mfa.matching_step(sec,str(data.get('mfa_code','')))
            if step is None:raise APIError(403,'Authenticator-Code ungültig')
            c.execute('INSERT INTO user_mfa(username,secret_enc,last_step) VALUES(?,?,?)',(actor,row['secret_enc'],step))
            codes=mfa.replace_codes(c,actor)
            c.execute('DELETE FROM mfa_pending WHERE username=?',(actor,))
            c.execute('DELETE FROM sessions WHERE username=?',(actor,))
            audit(c,actor,'mfa.enabled',ip=ip)
            return response(start,200,{'recovery_codes':codes})
        if path in ('/api/mfa/disable','/api/mfa/regenerate') and method=='POST':
            account=c.execute('SELECT password_hash,salt FROM users WHERE username=?',(actor,)).fetchone()
            if not account or not hmac.compare_digest(digest(str(data.get('current_password','')),account['salt']),account['password_hash']):raise APIError(403,'Passwort falsch')
            if not mfa.verify_factor(c,actor,str(data.get('mfa_code',''))):raise APIError(403,'2FA-Code falsch oder bereits verbraucht')
            if path.endswith('/disable'):
                c.execute('DELETE FROM user_mfa WHERE username=?',(actor,))
                c.execute('DELETE FROM mfa_recovery WHERE username=?',(actor,))
                audit(c,actor,'mfa.disabled',ip=ip);result={'ok':True}
            else:
                result={'recovery_codes':mfa.replace_codes(c,actor)}
                audit(c,actor,'mfa.recovery.regenerate',ip=ip)
            c.execute('DELETE FROM sessions WHERE username=?',(actor,))
            return response(start,200,result)
        # User management: always admin-only. No password hashes or MFA secrets in responses.
        if path=='/api/users' and method=='GET':
            if not is_admin:raise APIError(403,'Nur Administratoren')
            result=[]
            for u in c.execute('SELECT username,role FROM users ORDER BY username'):
                g,t=access.grants(c,u['username'])
                result.append({'username':u['username'],'role':u['role'],'group_ids':sorted(g),'tag_ids':sorted(t)})
            return response(start,200,{'users':result})
        if path=='/api/users' and method=='POST':
            if not is_admin:raise APIError(403,'Nur Administratoren')
            username=str(data.get('username','')).strip();password=str(data.get('password',''))
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',username) or len(password)<14 or len(password)>1024:
                raise APIError(400,'Benutzername oder Passwort ungültig (mindestens 14 Zeichen)')
            try:target_role=access.checked_role(str(data.get('role','viewer')))
            except ValueError as exc:raise APIError(400,str(exc))
            salt=secrets.token_hex(16)
            try:c.execute('INSERT INTO users(username,password_hash,salt,role) VALUES(?,?,?,?)',(username,digest(password,salt),salt,target_role))
            except __import__('psycopg').IntegrityError:raise APIError(409,'Benutzer existiert bereits')
            _save_user_grants(c,username,data)
            audit(c,actor,'user.create',username,ip)
            return response(start,201,{'ok':True})
        user_match=re.fullmatch(r'/api/users/([A-Za-z0-9_.-]{1,80})',path)
        if user_match and method in ('PUT','DELETE'):
            if not is_admin:raise APIError(403,'Nur Administratoren')
            who=user_match.group(1)
            target=c.execute('SELECT username,role FROM users WHERE username=? FOR UPDATE',(who,)).fetchone()
            if not target:raise APIError(404,'Benutzer nicht gefunden')
            if method=='DELETE':
                if who==actor:raise APIError(400,'Eigenes Administratorkonto nicht löschen')
                if target['role']=='admin' and c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]<=1:raise APIError(409,'Letzter Administrator')
                c.execute('DELETE FROM users WHERE username=?',(who,))
                c.execute('DELETE FROM sessions WHERE username=?',(who,))
                c.execute('DELETE FROM auth_challenges WHERE username=?',(who,))
                audit(c,actor,'user.delete',who,ip)
            else:
                try:target_role=access.checked_role(str(data.get('role',target['role'])))
                except ValueError as exc:raise APIError(400,str(exc))
                if who==actor and target_role!='admin':raise APIError(400,'Eigene Adminrolle nicht entfernen')
                if target['role']=='admin' and target_role!='admin' and c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]<=1:
                    raise APIError(409,'Letzter Administrator')
                c.execute('UPDATE users SET role=? WHERE username=?',(target_role,who))
                new_password=data.get('password')
                if new_password not in (None,''):
                    if not isinstance(new_password,str) or not 14<=len(new_password)<=1024:raise APIError(400,'Passwort mindestens 14 Zeichen')
                    salt=secrets.token_hex(16)
                    c.execute('UPDATE users SET password_hash=?,salt=? WHERE username=?',(digest(new_password,salt),salt,who))
                _save_user_grants(c,who,data)
                c.execute('DELETE FROM sessions WHERE username=?',(who,))
                c.execute('DELETE FROM auth_challenges WHERE username=?',(who,))
                audit(c,actor,'user.update',who,ip)
            return response(start,200,{'ok':True})
        if path=='/api/state' and method=='GET':
            monitors=[monitor_json(c,r) for r in c.execute('SELECT * FROM monitors ORDER BY position,alias') if r['id'] in allowed]
            groups=[dict(x) for x in c.execute('SELECT * FROM widget_groups ORDER BY position,name') if is_admin or x['id'] in access.grants(c,actor)[0] or any(m['group_id']==x['id'] for m in monitors)]
            prefs={x['group_id']:bool(x['collapsed']) for x in c.execute('SELECT group_id,collapsed FROM widget_group_preferences WHERE username=?',(actor,))}
            tags=[dict(x) for x in c.execute('SELECT * FROM widget_tags ORDER BY name') if is_admin or x['id'] in access.grants(c,actor)[1] or any(t['id']==x['id'] for m in monitors for t in m['tags'])]
            hb=c.execute("SELECT updated FROM worker_heartbeat WHERE name='worker'").fetchone()
            worker_ok=operational.worker_healthy(hb['updated'] if hb else None)
            for item in monitors:
                item['stale']=operational.monitor_stale(item,worker_ok=worker_ok)
            return response(start,200,{'worker_healthy':worker_ok,'stale_count':sum(m['stale'] for m in monitors),'endpoints':monitors,'groups':groups,'group_preferences':prefs,'tags':tags,'user':actor,'role':role_name,'grants':{'groups':sorted(access.grants(c,actor)[0]),'tags':sorted(access.grants(c,actor)[1])} if not is_admin else None,'csrf':session['csrf'],'now':time.time(),'version':VERSION})
        if path=='/api/logout' and method=='POST':
            cookie=env.get('HTTP_COOKIE','')
            from http.cookies import SimpleCookie
            parsed=SimpleCookie();parsed.load(cookie)
            if security.cookie_name(env) in parsed:
                key=hashlib.sha256(parsed[security.cookie_name(env)].value.encode()).hexdigest()
                c.execute('DELETE FROM sessions WHERE token_hash=?',(key,))
            audit(c,actor,'logout',ip=ip)
            return response(start,200,{'ok':True},[('Set-Cookie',session_cookie('',clear=True,env=env))])
        # Dashboard-only grouping and tags. These never change probe scheduling.
        if path=='/api/widget-groups' and method=='POST':
            name=str(data.get('name','')).strip();description=str(data.get('description','')).strip()
            if not 1<=len(name)<=60 or len(description)>200:raise APIError(400,'Ungültiger Gruppenname/Beschreibung')
            gid=uuid.uuid4().hex
            try:c.execute('INSERT INTO widget_groups(id,name,description,position) VALUES(?,?,?,COALESCE((SELECT MAX(position)+1 FROM widget_groups),0))',(gid,name,description))
            except __import__('psycopg').IntegrityError:raise APIError(409,'Gruppenname existiert bereits')
            audit(c,actor,'widget.group.create',gid,ip)
            return response(start,201,{'id':gid})
        m=re.fullmatch(r'/api/widget-groups/([a-f0-9]{32})',path)
        if m and method in ('PUT','DELETE'):
            gid=m.group(1)
            if not c.execute('SELECT 1 FROM widget_groups WHERE id=?',(gid,)).fetchone():raise APIError(404,'Gruppe nicht gefunden')
            if method=='DELETE':
                c.execute('DELETE FROM widget_groups WHERE id=?',(gid,));audit(c,actor,'widget.group.delete',gid,ip)
            else:
                name=str(data.get('name','')).strip();description=str(data.get('description','')).strip()
                if not 1<=len(name)<=60 or len(description)>200:raise APIError(400,'Ungültiger Gruppenname/Beschreibung')
                try:c.execute('UPDATE widget_groups SET name=?,description=? WHERE id=?',(name,description,gid))
                except __import__('psycopg').IntegrityError:raise APIError(409,'Gruppenname existiert bereits')
                audit(c,actor,'widget.group.update',gid,ip)
            return response(start,200,{'ok':True})
        if path=='/api/widget-groups/order' and method=='PUT':
            ids=data.get('ids');actual=[x[0] for x in c.execute('SELECT id FROM widget_groups')]
            if not isinstance(ids,list) or len(ids)!=len(actual) or len(set(ids))!=len(ids) or set(ids)!=set(actual):raise APIError(400,'Ungültige Gruppensortierung')
            for pos,gid in enumerate(ids):c.execute('UPDATE widget_groups SET position=? WHERE id=?',(pos,gid))
            audit(c,actor,'widget.group.reorder',ip=ip)
            return response(start,200,{'ok':True})
        if path=='/api/widget-groups/collapse' and method=='PUT':
            gid=str(data.get('id',''));collapsed=bool(data.get('collapsed'))
            if not c.execute('SELECT 1 FROM widget_groups WHERE id=?',(gid,)).fetchone():raise APIError(404,'Gruppe nicht gefunden')
            c.execute('INSERT INTO widget_group_preferences(username,group_id,collapsed) VALUES(?,?,?) ON CONFLICT(username,group_id) DO UPDATE SET collapsed=excluded.collapsed',(actor,gid,int(collapsed)))
            return response(start,200,{'ok':True})
        if path=='/api/widget-tags' and method=='POST':
            name=str(data.get('name','')).strip();color=str(data.get('color','')).lower()
            if not 1<=len(name)<=40 or not re.fullmatch(r'#[0-9a-f]{6}',color):raise APIError(400,'Ungültiger Tag-Name oder Farbe')
            tid=uuid.uuid4().hex
            try:c.execute('INSERT INTO widget_tags(id,name,color) VALUES(?,?,?)',(tid,name,color))
            except __import__('psycopg').IntegrityError:raise APIError(409,'Tag existiert bereits')
            audit(c,actor,'widget.tag.create',tid,ip)
            return response(start,201,{'id':tid})
        m=re.fullmatch(r'/api/widget-tags/([a-f0-9]{32}|tag-produktiv)',path)
        if m and method in ('PUT','DELETE'):
            tid=m.group(1)
            if not c.execute('SELECT 1 FROM widget_tags WHERE id=?',(tid,)).fetchone():raise APIError(404,'Tag nicht gefunden')
            if method=='DELETE':c.execute('DELETE FROM widget_tags WHERE id=?',(tid,));audit(c,actor,'widget.tag.delete',tid,ip)
            else:
                name=str(data.get('name','')).strip();color=str(data.get('color','')).lower()
                if not 1<=len(name)<=40 or not re.fullmatch(r'#[0-9a-f]{6}',color):raise APIError(400,'Ungültiger Tag-Name oder Farbe')
                try:c.execute('UPDATE widget_tags SET name=?,color=? WHERE id=?',(name,color,tid))
                except __import__('psycopg').IntegrityError:raise APIError(409,'Tag existiert bereits')
                audit(c,actor,'widget.tag.update',tid,ip)
            return response(start,200,{'ok':True})
        if path=='/api/widgets/assign' and method=='PUT':
            mid=str(data.get('monitor_id',''));gid=data.get('group_id') or None;tags=data.get('tag_ids',[])
            if not c.execute('SELECT 1 FROM monitors WHERE id=?',(mid,)).fetchone():raise APIError(404,'Monitor nicht gefunden')
            if gid is not None and not c.execute('SELECT 1 FROM widget_groups WHERE id=?',(gid,)).fetchone():raise APIError(400,'Gruppe nicht vorhanden')
            if role_name=='manager' and not access.can_assign(c,actor,gid,tags,role_name):raise APIError(403,'Zuweisung außerhalb der Freigabe')
            if not isinstance(tags,list) or len(tags)>20 or len(set(tags))!=len(tags) or any(not isinstance(x,str) for x in tags):raise APIError(400,'Ungültige Tags')
            if tags and c.execute('SELECT COUNT(*) FROM widget_tags WHERE id IN ('+','.join('?' for _ in tags)+')',tags).fetchone()[0]!=len(tags):raise APIError(400,'Tag nicht vorhanden')
            if role_name=='manager':
                authorized_tags=access.grants(c,actor)[1]
                existing={r['tag_id'] for r in c.execute('SELECT tag_id FROM widget_tag_links WHERE monitor_id=?',(mid,))}
                # Preserve tags managed by another location; the manager may only change own tags.
                tags=sorted(set(tags) | (existing-authorized_tags))
            pos=c.execute('SELECT COALESCE(MAX(position)+1,0) FROM widget_placement WHERE group_id IS NOT DISTINCT FROM ?', (gid,)).fetchone()[0]
            c.execute('INSERT INTO widget_placement(monitor_id,group_id,position) VALUES(?,?,?) ON CONFLICT(monitor_id) DO UPDATE SET group_id=excluded.group_id,position=excluded.position',(mid,gid,pos))
            c.execute('DELETE FROM widget_tag_links WHERE monitor_id=?',(mid,))
            c.executemany('INSERT INTO widget_tag_links(monitor_id,tag_id) VALUES(?,?)',[(mid,tid) for tid in tags])
            audit(c,actor,'widget.assign',mid,ip)
            return response(start,200,{'ok':True})
        if path=='/api/widgets/move' and method=='PUT':
            if not is_admin:raise APIError(403,'Nur Administratoren können die globale Widget-Reihenfolge ändern')
            mid=str(data.get('monitor_id',''));gid=data.get('group_id') or None;before=data.get('before_id') or None
            if not c.execute('SELECT 1 FROM monitors WHERE id=?',(mid,)).fetchone():raise APIError(404,'Monitor nicht gefunden')
            if gid is not None and not c.execute('SELECT 1 FROM widget_groups WHERE id=?',(gid,)).fetchone():raise APIError(400,'Gruppe nicht gefunden')
            if role_name=='manager' and gid not in access.grants(c,actor)[0]:raise APIError(403,'Gruppe nicht freigegeben')
            if before is not None:
                p=c.execute('SELECT group_id FROM widget_placement WHERE monitor_id=?',(before,)).fetchone()
                if not p or p['group_id']!=gid or before==mid:raise APIError(400,'Zielwidget nicht in dieser Gruppe')
            c.execute('INSERT INTO widget_placement(monitor_id,group_id,position) VALUES(?,?,0) ON CONFLICT(monitor_id) DO UPDATE SET group_id=excluded.group_id',(mid,gid))
            ids=[r[0] for r in c.execute('SELECT m.id FROM monitors m LEFT JOIN widget_placement p ON p.monitor_id=m.id WHERE p.group_id IS NOT DISTINCT FROM ? AND m.id<>? ORDER BY COALESCE(p.position,m.position),m.alias',(gid,mid))]
            if before:ids.insert(ids.index(before),mid)
            else:ids.append(mid)
            for i,x in enumerate(ids):c.execute('INSERT INTO widget_placement(monitor_id,group_id,position) VALUES(?,?,?) ON CONFLICT(monitor_id) DO UPDATE SET group_id=excluded.group_id,position=excluded.position',(x,gid,i))
            audit(c,actor,'widget.move',mid,ip)
            return response(start,200,{'ok':True})
        if path=='/api/endpoints' and method=='POST':
            if c.execute('SELECT COUNT(*) FROM monitors').fetchone()[0]>=100:raise APIError(400,'Maximal 100 Monitore')
            clean=validate_endpoint(data);mid=uuid.uuid4().hex
            if role_name=='manager' and not access.can_assign(c,actor,data.get('group_id'),data.get('tag_ids',[]),role_name):raise APIError(403,'Neue Monitore benötigen eine freigegebene Gruppe oder ein freigegebenes Tag')
            if clean['kind']=='snmp' and not c.execute('SELECT 1 FROM snmp_profiles WHERE id=?',(json.loads(clean['config'])['profile_id'],)).fetchone():raise APIError(400,'SNMP-Profil unbekannt')
            cols=list(clean);c.execute('INSERT INTO monitors(id,'+','.join(cols)+',position) VALUES(?,'+','.join('?' for _ in cols)+',?)',[mid,*clean.values(),c.execute('SELECT COUNT(*) FROM monitors').fetchone()[0]])
            if role_name=='manager':
                gid=data.get('group_id') or None;tag_ids=data.get('tag_ids',[])
                if not isinstance(tag_ids,list):raise APIError(400,'Ungültige Tags')
                c.execute('INSERT INTO widget_placement(monitor_id,group_id,position) VALUES(?,?,0)',(mid,gid))
                c.executemany('INSERT INTO widget_tag_links(monitor_id,tag_id) VALUES(?,?)',[(mid,t) for t in tag_ids])
            audit(c,actor,'monitor.create',mid,ip)
            return response(start,201,{'id':mid})
        if path.startswith('/api/endpoints/'):
            tail=path.removeprefix('/api/endpoints/');mid=tail.split('/')[0]
            old=c.execute('SELECT * FROM monitors WHERE id=?',(mid,)).fetchone()
            if not old:raise APIError(404,'Monitor nicht gefunden')
            if tail.endswith('/maintenance') and method=='POST':
                enabled=int(bool(data.get('enabled')))
                c.execute('UPDATE monitors SET maintenance=?,next_check=0,revision=revision+1 WHERE id=?',(enabled,mid))
                event(c,mid,'maintenance.started' if enabled else 'maintenance.ended','info','Manuelle Wartung geändert')
                audit(c,actor,'maintenance.update',mid,ip)
                return response(start,200,{'ok':True})
            if tail==mid and method=='PUT':
                clean=validate_endpoint(data)
                if clean['kind']=='snmp' and not c.execute('SELECT 1 FROM snmp_profiles WHERE id=?',(json.loads(clean['config'])['profile_id'],)).fetchone():raise APIError(400,'SNMP-Profil unbekannt')
                c.execute('UPDATE monitors SET '+','.join(x+'=?' for x in clean)+',revision=revision+1,next_check=0 WHERE id=?',[*clean.values(),mid])
                audit(c,actor,'monitor.update',mid,ip)
                return response(start,200,{'ok':True})
            if tail==mid and method=='DELETE':
                c.execute('DELETE FROM monitors WHERE id=?',(mid,));audit(c,actor,'monitor.delete',mid,ip)
                return response(start,200,{'ok':True})
        if path=='/api/order' and method=='PUT':
            ids=data.get('ids',[])
            actual=[x[0] for x in c.execute('SELECT id FROM monitors')]
            if not isinstance(ids,list) or len(ids)!=len(actual) or set(ids)!=set(actual):raise APIError(400,'Unvollständige Sortierung')
            for i,mid in enumerate(ids):c.execute('UPDATE monitors SET position=? WHERE id=?',(i,mid))
            audit(c,actor,'monitor.reorder',ip=ip)
            return response(start,200,{'ok':True})
        if path=='/api/check' and method=='POST':
            if is_admin:
                c.execute('UPDATE monitors SET next_check=0 WHERE maintenance=0')
            elif str(data.get('monitor_id','')) in allowed:
                c.execute('UPDATE monitors SET next_check=0 WHERE maintenance=0 AND id=?',(str(data['monitor_id']),))
            else:
                raise APIError(403,'Keine freigegebenen Monitore')
            audit(c,actor,'monitor.check_all',ip=ip)
            return response(start,200,{'ok':True})
        if path=='/api/snmp-profiles' and method=='GET':
            rows=c.execute('SELECT id,label,username,auth_protocol,created FROM snmp_profiles ORDER BY label').fetchall()
            return response(start,200,{'profiles':[dict(r) for r in rows]})
        if path=='/api/snmp-profiles' and method=='POST':
            label=str(data.get('label','')).strip();username=str(data.get('username','')).strip()
            auth=str(data.get('auth_protocol','sha256')).lower();a=str(data.get('auth_password',''));v=str(data.get('privacy_password',''))
            if not 1<=len(label)<=80 or not 1<=len(username)<=64 or auth not in ('sha1','sha256') or not 8<=len(a)<=256 or not 8<=len(v)<=256:
                raise APIError(400,'SNMP-Profil: Name, Benutzer oder Passwörter ungültig (mindestens 8 Zeichen)')
            ident=uuid.uuid4().hex
            try:c.execute('INSERT INTO snmp_profiles(id,label,username,auth_protocol,auth_enc,priv_enc,created) VALUES(?,?,?,?,?,?,?)',
                      (ident,label,username,auth,encrypt(a),encrypt(v),time.time()))
            except Exception as exc:
                if isinstance(exc, __import__('psycopg').IntegrityError):raise APIError(409,'Profilname bereits vergeben')
                raise
            audit(c,actor,'snmp.profile.create',ident,ip)
            return response(start,201,{'id':ident})
        if re.fullmatch(r'/api/snmp-profiles/[a-f0-9]{32}',path) and method=='DELETE':
            ident=path.rsplit('/',1)[-1]
            if any(json.loads(r['config']).get('profile_id')==ident for r in c.execute("SELECT config FROM monitors WHERE kind='snmp'")):
                raise APIError(409,'Profil wird von einem Monitor verwendet')
            if not c.execute('DELETE FROM snmp_profiles WHERE id=?',(ident,)).rowcount:raise APIError(404,'Profil fehlt')
            audit(c,actor,'snmp.profile.delete',ident,ip)
            return response(start,200,{'ok':True})
        if path=='/api/smtp' and method=='GET':
            return response(start,200,smtp_public(c))
        if path=='/api/smtp' and method=='PUT':
            # Re-authenticate before replacing mail credentials.
            account=c.execute('SELECT password_hash,salt FROM users WHERE username=?',(actor,)).fetchone()
            if not account or not hmac.compare_digest(digest(str(data.get('current_password','')),account['salt']),account['password_hash']):
                raise APIError(403,'Aktuelles Kontopasswort falsch')
            if c.execute('SELECT 1 FROM user_mfa WHERE username=?',(actor,)).fetchone() and not mfa.verify_factor(c,actor,str(data.get('mfa_code',''))):
                raise APIError(403,'2FA-Code für SMTP-Änderung erforderlich')
            from security import resolve
            current=smtp_config(c)
            fields={k:str(data.get(k,'')).strip() for k in ('host','username','sender','recipient')}
            mode=str(data.get('security','starttls'))
            try:port=int(data.get('port',587))
            except (ValueError,TypeError):raise APIError(400,'SMTP-Port ungültig')
            enabled=bool(data.get('enabled'))
            if mode not in ('starttls','ssl') or not 1<=port<=65535:raise APIError(400,'SMTP-Sicherheitsmodus oder Port ungültig')
            if any(len(fields[k])>254 for k in fields) or len(fields['host'])>253 or len(fields['username'])>254:raise APIError(400,'SMTP-Feld zu lang')
            if any(ord(ch)<32 or ord(ch)==127 for value in fields.values() for ch in value):raise APIError(400,'Steuerzeichen in SMTP-Konfiguration')
            if enabled:
                if not fields['host'] or not fields['sender'] or not fields['recipient'] or '@' not in fields['sender'] or '@' not in fields['recipient']:
                    raise APIError(400,'SMTP-Adresse oder Server fehlt')
                try:resolve(fields['host'],port)
                except Exception:raise APIError(400,'SMTP-Server durch Netzwerkregel gesperrt oder nicht auflösbar')
            password_enc=None if data.get('clear_password') else current.get('password_enc')
            if data.get('password'):password_enc=encrypt(str(data['password']))
            value=dict(enabled=enabled,port=port,security=mode,password_enc=password_enc,**fields)
            c.execute("INSERT INTO settings(key,value) VALUES('smtp',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(value),))
            audit(c,actor,'smtp.config.update',ip=ip)
            return response(start,200,{'ok':True})
        if path=='/api/smtp/test' and method=='POST':
            try:test_message(c)
            except ValueError as exc:raise APIError(400,str(exc))
            audit(c,actor,'smtp.test',ip=ip)
            return response(start,202,{'queued':True})
        if path=='/api/smtp/delivery' and method=='GET':
            rows=c.execute('SELECT status,COUNT(*) AS count FROM mail_outbox GROUP BY status').fetchall()
            latest=[dict(r) for r in c.execute('SELECT id,created,status,attempts,sent_at,last_error,recipient,subject FROM mail_outbox ORDER BY created DESC LIMIT 50')]
            return response(start,200,{'counts':{r['status']:r['count'] for r in rows},'latest':latest})
        if path=='/api/smtp/retry' and method=='POST':
            num=c.execute("UPDATE mail_outbox SET status='pending',attempts=0,next_attempt=?,last_error=NULL WHERE status='failed'",(time.time(),)).rowcount
            audit(c,actor,'smtp.retry',ip=ip)
            return response(start,200,{'queued':num})
        if path=='/api/events' and method=='GET':
            if is_admin:rows=c.execute('SELECT * FROM events ORDER BY created DESC LIMIT 200')
            elif allowed:rows=c.execute('SELECT * FROM events WHERE monitor_id IN ('+','.join('?' for _ in allowed)+') ORDER BY created DESC LIMIT 200',sorted(allowed))
            else:rows=[]
            return response(start,200,{'events':[dict(r) for r in rows]})
        match=re.fullmatch(r'/api/monitors/([a-f0-9]{32})/diagnostics',path)
        if match and method=='GET':
            mid=match.group(1)
            m=c.execute('SELECT id,alias,kind,status,detail,failure_count,failure_threshold,checked FROM monitors WHERE id=?',(mid,)).fetchone()
            if not m:raise APIError(404,'Monitor nicht gefunden')
            rows=c.execute('SELECT checked,status,latency,error_code,detail,metrics FROM checks WHERE monitor_id=? ORDER BY id DESC LIMIT 12',(mid,)).fetchall()
            checks=[]
            for r in rows:
                item=dict(r)
                try: item['metrics']=json.loads(item['metrics'])
                except (ValueError,TypeError):item['metrics']={}
                checks.append(item)
            related=[dict(x) for x in c.execute('SELECT created,event_type,severity,detail FROM events WHERE monitor_id=? ORDER BY id DESC LIMIT 20',(mid,))]
            return response(start,200,{'monitor':dict(m),'checks':checks,'events':related})
        if path=='/api/notifications/preview' and method=='POST':
            mid=str(data.get('monitor_id',''))
            m=c.execute('SELECT id,alias,target,notify_down,notify_up,status FROM monitors WHERE id=?',(mid,)).fetchone()
            if not m:raise APIError(404,'Monitor nicht gefunden')
            conf=smtp_config(c)
            now=time.time()
            blocked=bool(maintenance_active(c,mid,now) or dependency_blocked(c,mid))
            preview=dict(monitor=m['alias'],smtp_enabled=bool(conf.get('enabled')),
                         recipient_configured=bool(conf.get('recipient')),
                         notify_down=bool(m['notify_down']),notify_up=bool(m['notify_up']),
                         suppressed=blocked,queued=False,sent=False,
                         message='Nur Simulation: Es wurden keine Alarme oder E-Mails ausgelöst.')
            audit(c,actor,'notification.preview',mid,ip)
            return response(start,200,preview)
        if path=='/api/diagnostics' and method=='GET':
            from diagnostics import snapshot
            return response(start,200,snapshot(c))
        if path=='/api/incidents' and method=='GET':
            if is_admin:rows=c.execute('SELECT * FROM incidents ORDER BY started DESC LIMIT 200')
            elif allowed:rows=c.execute('SELECT * FROM incidents WHERE monitor_id IN ('+','.join('?' for _ in allowed)+') ORDER BY started DESC LIMIT 200',sorted(allowed))
            else:rows=[]
            return response(start,200,{'incidents':[dict(r) for r in rows]})
        if path=='/api/reports/availability' and method=='GET':
            query=urllib.parse.parse_qs(env.get('QUERY_STRING',''))
            try:days=int(query.get('days',['30'])[0])
            except (ValueError,TypeError):raise APIError(400,'Ungültiger Zeitraum')
            if days not in (7,30,90,365):raise APIError(400,'Zeitraum muss 7, 30, 90 oder 365 Tage sein')
            since=time.time()-days*86400
            result=[]
            for mid in sorted(allowed):
                m=c.execute('SELECT id,alias,kind,checked,status FROM monitors WHERE id=?',(mid,)).fetchone()
                if not m:continue
                r=c.execute('SELECT COALESCE(SUM(total),0) AS total,COALESCE(SUM(success),0) AS success,COALESCE(SUM(latency_sum),0) AS latency_sum,COALESCE(SUM(latency_count),0) AS latency_count FROM rollups WHERE monitor_id=? AND period=\'hour\' AND bucket>=?',(mid,since)).fetchone()
                i=c.execute('SELECT started,ended FROM incidents WHERE monitor_id=? AND started<=? AND (ended IS NULL OR ended>=?)',(mid,time.time(),since)).fetchall()
                seconds=sum(max(0,min(time.time(),x['ended'] if x['ended'] is not None else time.time())-max(since,x['started'])) for x in i)
                result.append({'monitor_id':mid,'alias':m['alias'],'kind':m['kind'],'checks':int(r['total']),'success':int(r['success']),
                  'probe_success_pct':round(100.0*int(r['success'])/int(r['total']),2) if r['total'] else None,
                  'avg_latency_ms':round(float(r['latency_sum'])/float(r['latency_count']),2) if r['latency_count'] else None,
                  'incident_downtime_sec':round(seconds,1),'incident_count':len(i)})
            return response(start,200,{'days':days,'generated':time.time(),'method':'Anteil erfolgreicher Einzelprüfungen; Ausfallzeit aus bestätigten Incidents. Keine zeitgewichtete SLA-Verfügbarkeit.', 'monitors':result})
        if path=='/api/alert-subscriptions' and method=='GET':
            if not is_admin:raise APIError(403,'Nur Administratoren')
            return response(start,200,{'subscriptions':[dict(r) for r in c.execute('SELECT username,email,notify_down,notify_up FROM alert_subscriptions ORDER BY username')]})
        if path=='/api/alert-subscriptions' and method=='PUT':
            if not is_admin:raise APIError(403,'Nur Administratoren')
            username=str(data.get('username',''))
            email=str(data.get('email','')).strip()
            if not c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():raise APIError(404,'Benutzer unbekannt')
            if email and (len(email)>254 or not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+',email)):
                raise APIError(400,'Ungültige E-Mail-Adresse')
            down=data.get('notify_down') is True;up=data.get('notify_up') is True
            if (down or up) and not email:raise APIError(400,'Alarmempfänger benötigt eine E-Mail-Adresse')
            if email:
                c.execute('INSERT INTO alert_subscriptions(username,email,notify_down,notify_up) VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET email=excluded.email,notify_down=excluded.notify_down,notify_up=excluded.notify_up',(username,email,int(down),int(up)))
            else:c.execute('DELETE FROM alert_subscriptions WHERE username=?',(username,))
            audit(c,actor,'alert.subscription.update',username,ip)
            return response(start,200,{'ok':True})
        if path=='/api/audit' and method=='GET':
            if not is_admin:raise APIError(403,'Nur Administratoren')
            return response(start,200,{'audit':[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY created DESC LIMIT 200')]})
        if path=='/api/incidents/stats' and method=='GET':
            from incidents import summary
            query=urllib.parse.parse_qs(env.get('QUERY_STRING',''))
            try:days=int(query.get('days',['30'])[0])
            except (TypeError,ValueError):raise APIError(400,'Ungültiges Zeitfenster')
            mid=query.get('monitor_id',[None])[0]
            if mid and not re.fullmatch(r'[a-f0-9]{32}',mid):raise APIError(400,'Monitor-ID ungültig')
            if mid and mid not in allowed:raise APIError(404,'Monitor unbekannt')
            if not is_admin and not mid:raise APIError(400,'Bitte einen freigegebenen Monitor auswählen')
            try:result=summary(c,days=days,monitor_id=mid)
            except ValueError as exc:raise APIError(400,str(exc))
            return response(start,200,result)
        if path=='/api/snmp/discover' and method=='POST':
            from snmp_discovery import discover
            recent=c.execute("SELECT COUNT(*) FROM audit WHERE actor=? AND action IN ('snmp.discovery','snmp.discovery.failed') AND created>?",(actor,time.time()-60)).fetchone()[0]
            if recent>=5:raise APIError(429,'Maximal fünf Interface-Abfragen pro Minute')
            target=str(data.get('target','')).strip()
            profile_id=str(data.get('profile_id',''))
            if not 1<=len(target)<=253 or not re.fullmatch(r'[a-f0-9]{32}',profile_id):raise APIError(400,'Ziel oder Profil ungültig')
            try:port=int(data.get('port',161));timeout=int(data.get('timeout',3))
            except (TypeError,ValueError):raise APIError(400,'Port oder Timeout ungültig')
            if not 1<=port<=65535 or not 1<=timeout<=5:raise APIError(400,'Port oder Timeout ungültig')
            # Intentionally never reveal underlying network/credential errors in API.
            try:items=discover(target,profile_id,port,timeout)
            except Exception as exc:
                audit(c,actor,'snmp.discovery.failed',ip=ip,detail=type(exc).__name__,success=False)
                c.commit()  # Failed discovery must count toward request throttle.
                raise APIError(502,'SNMP-Interface-Abfrage fehlgeschlagen')
            audit(c,actor,'snmp.discovery',ip=ip,detail='Interface-Anzahl: '+str(len(items)))
            return response(start,200,{'interfaces':items,'truncated':len(items)>=64})
        if path=='/api/dependencies' and method=='GET':
            return response(start,200,{'dependencies':[dict(r) for r in c.execute('SELECT * FROM dependencies') if r['parent_id'] in allowed and r['child_id'] in allowed]})
        if path=='/api/dependencies/bulk' and method=='POST':
            parent=str(data.get('parent_id',''));children=data.get('child_ids',[])
            if not isinstance(children,list) or not 1<=len(children)<=100 or any(not isinstance(mid,str) or not re.fullmatch(r'[a-f0-9]{32}',mid) for mid in children) or len(set(children))!=len(children):
                raise APIError(400,'1 bis 100 eindeutige Monitore auswählen')
            if not is_admin and (role_name!='manager' or parent not in allowed or any(mid not in allowed for mid in children)):
                raise APIError(403,'Abhängigkeit außerhalb der Freigabe')
            try:
                for child in children:add_dependency(c,parent,child)
            except ValueError as exc:raise APIError(400,str(exc))
            audit(c,actor,'dependency.bulk',parent,ip,detail=str(len(children)))
            return response(start,201,{'ok':True,'count':len(children)})
        if path=='/api/dependencies' and method=='POST':
            try:add_dependency(c,str(data.get('parent_id','')),str(data.get('child_id','')))
            except ValueError as exc:raise APIError(400,str(exc))
            audit(c,actor,'dependency.create',str(data.get('child_id','')),ip)
            return response(start,201,{'ok':True})
        if path=='/api/dependencies' and method=='DELETE':
            parent=str(data.get('parent_id',''));child=str(data.get('child_id',''))
            if not re.fullmatch(r'[a-f0-9]{32}',parent) or not re.fullmatch(r'[a-f0-9]{32}',child):raise APIError(400,'Ungültige Monitor-ID')
            if not remove_dependency(c,parent,child):raise APIError(404,'Abhängigkeit nicht vorhanden')
            audit(c,actor,'dependency.delete',child,ip)
            return response(start,200,{'ok':True})
        if path=='/api/maintenance-windows' and method=='POST':
            mid=str(data.get('monitor_id',''))
            try:
                begin=float(data.get('starts'));end=float(data.get('ends'))
                recurrence=validate_maintenance(str(data.get('recurrence','once')),str(data.get('timezone','UTC')),
                  data.get('weekdays',[]),str(data.get('start_local','00:00')),str(data.get('end_local','00:00')))
            except (ValueError,TypeError) as exc:raise APIError(400,str(exc))
            if not begin<end or end-begin>5*365*86400:raise APIError(400,'Ungültiges Zeitfenster')
            ident=uuid.uuid4().hex
            try:c.execute('INSERT INTO maintenance_windows(id,monitor_id,starts,ends,recurrence,timezone,weekdays,start_local,end_local) VALUES(?,?,?,?,?,?,?,?,?)',
                    (ident,mid,begin,end,recurrence['recurrence'],recurrence['timezone'],recurrence['weekdays'],recurrence['start_local'],recurrence['end_local']))
            except Exception:raise APIError(400,'Ungültiger Monitor')
            audit(c,actor,'maintenance.schedule',mid,ip)
            return response(start,201,{'id':ident})
        if re.fullmatch(r'/api/maintenance-windows/[a-f0-9]{32}',path) and method=='DELETE':
            ident=path.rsplit('/',1)[-1]
            if not c.execute('DELETE FROM maintenance_windows WHERE id=?',(ident,)).rowcount:raise APIError(404,'Wartungsfenster unbekannt')
            audit(c,actor,'maintenance.delete',ident,ip)
            return response(start,200,{'ok':True})
        if path=='/api/maintenance-windows' and method=='GET':
            return response(start,200,{'windows':[dict(r) for r in c.execute('SELECT * FROM maintenance_windows ORDER BY starts') if r['monitor_id'] in allowed]})
        if path.startswith('/api/exports/') and method=='GET':
            kind=path.removeprefix('/api/exports/').removesuffix('.csv')
            csv_bytes=csv_export(c,kind,urllib.parse.parse_qs(env.get('QUERY_STRING','')),None if is_admin else allowed)
            start('200 OK',[('Content-Type','text/csv; charset=utf-8'),('Content-Disposition',f'attachment; filename="upwatch-{kind}.csv"'),('Content-Length',str(len(csv_bytes))),('Cache-Control','no-store')]);return [csv_bytes]
        if re.fullmatch(r'/api/monitors/[a-f0-9]{32}/history',path) and method=='GET':
            mid=path.split('/')[3]
            return response(start,200,{'checks':[dict(x) for x in c.execute('SELECT checked,status,latency,code,error_code,detail FROM checks WHERE monitor_id=? ORDER BY checked DESC LIMIT 1000',(mid,))]})
        if re.fullmatch(r'/api/monitors/[a-f0-9]{32}/trends',path) and method=='GET':
            mid=path.split('/')[3]
            if not c.execute('SELECT 1 FROM monitors WHERE id=?',(mid,)).fetchone():raise APIError(404,'Monitor unbekannt')
            query=urllib.parse.parse_qs(env.get('QUERY_STRING',''))
            period=query.get('period',['hour'])[0]
            if period not in ('hour','day'):raise APIError(400,'Ungültige Auflösung')
            limit= min(365,max(1,int(query.get('limit',['72'])[0])))
            series=[dict(r) for r in reversed(c.execute('SELECT bucket,total,success,latency_sum,latency_count,latency_min,latency_max FROM rollups WHERE monitor_id=? AND period=? ORDER BY bucket DESC LIMIT ?',(mid,period,limit)).fetchall())]
            for r in series:
                r['availability_pct']=round(100*r['success']/r['total'],2) if r['total'] else None
                r['latency_avg_ms']=round(r['latency_sum']/r['latency_count'],2) if r['latency_count'] else None
                del r['latency_sum'];del r['latency_count']
            return response(start,200,{'period':period,'points':series})
        if path=='/api/account' and method=='PUT':
            old=c.execute('SELECT * FROM users WHERE username=?',(actor,)).fetchone()
            if not hmac.compare_digest(digest(str(data.get('current_password','')),old['salt']),old['password_hash']):raise APIError(403,'Aktuelles Passwort falsch')
            newname=str(data.get('username','')).strip();password=str(data.get('new_password',''))
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',newname):raise APIError(400,'Neuer Benutzername ungültig')
            if password and (len(password)<16 or len(password)>1024 or password!=data.get('confirm_password')):raise APIError(400,'Neues Passwort ungültig oder nicht identisch')
            if not password and data.get('confirm_password'):raise APIError(400,'Neues Passwort fehlt')
            if newname==actor and not password:raise APIError(400,'Keine Änderung angegeben')
            if newname!=actor and c.execute('SELECT 1 FROM users WHERE username=?',(newname,)).fetchone():raise APIError(409,'Benutzername bereits vergeben')
            if c.execute('SELECT 1 FROM user_mfa WHERE username=?',(actor,)).fetchone():
                if not mfa.verify_factor(c,actor,str(data.get('mfa_code',''))):raise APIError(403,'2FA-Code erforderlich')
            if password:
                salt=secrets.token_hex(16);password_hash=digest(password,salt)
            else:
                salt=old['salt'];password_hash=old['password_hash']
            if newname!=actor:
                # Copy child references while the old account still exists; most
                # RC1 FKs do not declare ON UPDATE CASCADE. All SQL statements are
                # executed in the same request transaction.
                c.execute('INSERT INTO users(username,password_hash,salt,role) VALUES(?,?,?,?)',(newname,password_hash,salt,old['role']))
                for table,columns in (
                    ('user_mfa','secret_enc,last_step'),
                    ('mfa_pending','secret_enc,expires'),
                    ('mfa_recovery','code_hash'),
                    ('widget_group_preferences','group_id,collapsed'),
                    ('user_group_grants','group_id'),
                    ('user_tag_grants','tag_id'),
                    ('alert_subscriptions','email,notify_down,notify_up'),
                ):
                    c.execute(f'INSERT INTO {table}(username,{columns}) SELECT ?,{columns} FROM {table} WHERE username=?',(newname,actor))
                c.execute('DELETE FROM sessions WHERE username IN (?,?)',(actor,newname))
                c.execute('DELETE FROM auth_challenges WHERE username IN (?,?)',(actor,newname))
                c.execute('DELETE FROM users WHERE username=?',(actor,))
                audit(c,actor,'account.rename',mid=newname,ip=ip)
            else:
                c.execute('UPDATE users SET password_hash=?,salt=? WHERE id=?',(password_hash,salt,old['id']))
                c.execute('DELETE FROM sessions WHERE username=?',(actor,))
                c.execute('DELETE FROM auth_challenges WHERE username=?',(actor,))
                audit(c,actor,'account.update',ip=ip)
            return response(start,200,{'ok':True})
    raise APIError(404,'Funktion nicht vorhanden')
