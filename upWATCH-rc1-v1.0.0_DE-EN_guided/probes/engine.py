"""Network checks with SSRF-restricted connections and bounded responses."""
import json, re, socket, ssl, subprocess, time, urllib.parse, urllib.request, ipaddress
import security

class ProbeFailure(Exception):
    def __init__(self,code,detail,metrics=None):super().__init__(detail);self.code=code;self.metrics=metrics or {}

def check(m):
    start=time.monotonic();status='down';code=None;metrics={};detail='';error=None
    cfg=json.loads(m.get('config') or '{}');timeout=float(m['timeout']);kind=m['kind'];target=m['target']
    try:
        if kind=='ping':
            addr=security.resolve(target,0)[0][4][0]
            p=subprocess.run(['ping','-n','-c','1','-W',str(max(1,int(timeout))),'--',addr],capture_output=True,timeout=timeout+1)
            if p.returncode:raise ProbeFailure('PING_FAILED','Keine ICMP-Antwort')
            detail='ICMP-Antwort empfangen'
        elif kind=='tcp':
            with security.connect(target,int(cfg.get('port',443)),timeout):pass
            detail='TCP-Verbindung hergestellt'
        elif kind=='udp':
            from probes.udp import run,UDPFailure
            try:
                detail=run(target,cfg,timeout)
            except UDPFailure as exc:
                if exc.indeterminate:status='warning'
                error=exc.code
                detail=str(exc)
                if not exc.indeterminate:raise ProbeFailure(exc.code,detail)
        elif kind=='http':
            request=urllib.request.Request(target,headers={'User-Agent':'upWATCH/2.0','Accept-Encoding':'identity'})
            with security.HTTPClient().open(request,timeout) as r:
                code=r.status
                cap=262144; body=r.response.read(cap+1)
                if len(body)>cap:raise ProbeFailure('HTTP_BODY_TOO_LARGE','Antwort größer als 256 KiB')
                if code!=int(cfg.get('expected',200)):raise ProbeFailure('HTTP_STATUS_MISMATCH',f'HTTP {code}, erwartet {cfg.get("expected",200)}')
                text=body.decode('utf-8','replace')
                mode=cfg.get('body_mode','none');value=str(cfg.get('body_value',''))
                if mode=='contains' and value not in text:raise ProbeFailure('HTTP_BODY_MISMATCH','Erwarteter Text fehlt')
                if mode=='not_contains' and value in text:raise ProbeFailure('HTTP_BODY_MISMATCH','Verbotener Text gefunden')
                if mode=='json_key':
                    try:
                        item=json.loads(text)
                        for key in str(cfg.get('json_path','')).strip('$.').split('.'):
                            if not key:continue
                            item=item[int(key)] if isinstance(item,list) else item[key]
                    except (ValueError,KeyError,TypeError,IndexError,json.JSONDecodeError):raise ProbeFailure('HTTP_JSON_MISMATCH','JSON-Pfad nicht gefunden')
                    if item!=cfg.get('json_expected'):raise ProbeFailure('HTTP_JSON_MISMATCH','JSON-Wert stimmt nicht überein')
                detail=f'HTTP {code} · Inhaltsprüfung OK'
        elif kind=='tls':
            parts=urllib.parse.urlsplit(target if '://' in target else 'tls://'+target)
            host=parts.hostname;port=parts.port or 443
            if not host:raise ProbeFailure('TLS_TARGET','Ungültiger TLS-Host')
            with security.connect(host,port,timeout) as raw:
                with ssl.create_default_context().wrap_socket(raw,server_hostname=host) as sock:
                    cert=sock.getpeercert();exp=ssl.cert_time_to_seconds(cert['notAfter']);days=(exp-time.time())/86400
                    metrics={'remaining_days':round(days,2),'tls_version':sock.version()}
                    if days<=0:raise ProbeFailure('TLS_EXPIRED','Zertifikat abgelaufen')
                    if days<=int(cfg.get('critical_days',7)):raise ProbeFailure('TLS_EXPIRING',f'Nur {days:.1f} Tage gültig')
                    if days<=int(cfg.get('warning_days',30)):status='warning'
                    detail=f'TLS {sock.version()} · Zertifikat noch {days:.1f} Tage gültig'
        elif kind=='dns':
            typ=str(cfg.get('record_type','A')).upper()
            if typ not in ('A','AAAA','MX','TXT','NS','CNAME'):raise ProbeFailure('DNS_UNSUPPORTED','DNS-Typ unbekannt')
            try:
                import dns.resolver
                import dns.exception
                answers_rr=dns.resolver.resolve(target,typ,lifetime=timeout,raise_on_no_answer=True)
                answers=[]
                for rr in answers_rr:
                    if typ in ('A','AAAA'): value=rr.address
                    elif typ=='MX':value=f'{rr.preference} {str(rr.exchange).rstrip(".")}'
                    elif typ in ('NS','CNAME'):value=str(rr.target).rstrip('.')
                    elif typ=='TXT':value=b''.join(rr.strings).decode('utf-8','replace')
                    else:value=rr.to_text()
                    answers.append(value)
            except dns.resolver.NXDOMAIN:raise ProbeFailure('DNS_NXDOMAIN','DNS-Name existiert nicht')
            except dns.resolver.NoAnswer:raise ProbeFailure('DNS_NO_ANSWER',f'Kein {typ}-Record vorhanden')
            except dns.resolver.NoNameservers:raise ProbeFailure('DNS_SERVFAIL','Kein Nameserver lieferte eine gültige Antwort')
            except dns.exception.Timeout:raise ProbeFailure('DNS_TIMEOUT','DNS-Abfrage hat das Zeitlimit überschritten')
            if not answers:raise ProbeFailure('DNS_NO_ANSWER',f'Kein {typ}-Record vorhanden')
            answers=sorted(set(answers))
            if typ in ('A','AAAA'):
                for address in answers:security.resolve(address,0)
            expected=cfg.get('expected_values',[])
            if expected:
                normalize=lambda v:v.rstrip('.').casefold() if typ in ('NS','CNAME','MX') else v
                actual={normalize(v) for v in answers}
                if not all(normalize(v) in actual for v in expected):
                    raise ProbeFailure('DNS_MISMATCH','DNS-Antwort weicht von den Sollwerten ab')
            metrics={'record_type':typ,'answers':answers[:25],'ttl':answers_rr.rrset.ttl if answers_rr.rrset else None}
            detail=f'DNS {typ}: '+', '.join(answers[:3])
        elif kind=='ddns_bundle':
            from probes.ddns_bundle import run, BundleError
            try:
                metrics, detail = run(target,cfg,timeout)
            except BundleError as exc:
                raise ProbeFailure(exc.code,str(exc),exc.metrics)
        elif kind=='wan_dns':
            from probes.wan_dns import run, WanDnsError
            try:
                metrics,detail=run(target,cfg,timeout)
            except WanDnsError as exc:
                raise ProbeFailure(exc.code,str(exc),exc.metrics)
        elif kind=='ssh':
            with security.connect(target,int(cfg.get('port',22)),timeout) as sock:
                sock.settimeout(timeout);banner=sock.recv(255)
            if not banner.startswith(b'SSH-2.0-'):raise ProbeFailure('SSH_BANNER_INVALID','Ungültiges SSH-Banner')
            detail=banner.decode('ascii','replace').strip()[:150]
        elif kind=='smtp':
            port=int(cfg.get('port',587))
            with security.connect(target,port,timeout) as sock:
                sock.settimeout(timeout)
                banner=sock.recv(512)
                if not banner.startswith(b'220'):raise ProbeFailure('SMTP_GREETING','SMTP 220 Greeting fehlt')
                sock.sendall(b'EHLO upwatch.local\r\n')
                data=b''
                while len(data)<8192:
                    line=b''
                    while not line.endswith(b'\n') and len(line)<1024:
                        byte=sock.recv(1)
                        if not byte:raise ProbeFailure('SMTP_CLOSED','SMTP-Server hat geschlossen')
                        line+=byte
                    data+=line
                    if len(line)<4 or line[3:4]!=b'-':break
                if not data.startswith(b'250'):raise ProbeFailure('SMTP_EHLO','SMTP EHLO fehlgeschlagen')
                if cfg.get('starttls'):
                    if b'STARTTLS' not in data.upper():raise ProbeFailure('SMTP_STARTTLS_FAILED','STARTTLS nicht angeboten')
                    sock.sendall(b'STARTTLS\r\n')
                    reply=sock.recv(512)
                    if not reply.startswith(b'220'):raise ProbeFailure('SMTP_STARTTLS_FAILED','STARTTLS verweigert')
                    with ssl.create_default_context().wrap_socket(sock,server_hostname=target) as tls_sock:
                        detail=f'SMTP STARTTLS · {tls_sock.version()}'
                else:detail='SMTP EHLO erfolgreich'
        elif kind=='snmp':
            from probes.snmp import snmp_get
            cfg['_monitor_id']=m.get('id')
            metrics=snmp_get(target,cfg,timeout)
            detail=(f"SNMPv3 Interface {metrics['interface_index']} · {metrics.get('in_bps','—')} / {metrics.get('out_bps','—')} bit/s" if cfg.get('mode')=='interface' else f"SNMPv3 {metrics['oid']}: {str(metrics['value'])[:100]}")

        else:raise ProbeFailure('UNSUPPORTED_KIND','Prüftyp nicht unterstützt')
        if status!='warning':status='up'
    except ProbeFailure as exc:error=exc.code;detail=str(exc);metrics=exc.metrics or metrics
    except ValueError as exc:
        error='SNMP_VALUE_MISMATCH' if str(exc)=='SNMP_VALUE_MISMATCH' else 'INVALID_PROBE_CONFIG';detail=str(exc)[:180]
    except socket.gaierror as exc:error='DNS_RESOLUTION_FAILED';detail=str(exc)
    except ssl.SSLCertVerificationError as exc:error='TLS_VERIFY_FAILED';detail=str(exc)
    except TimeoutError as exc:error='TIMEOUT';detail=str(exc)
    except Exception as exc:error='PROBE_ERROR';detail=f'{type(exc).__name__}: {exc}'[:240]
    elapsed=round((time.monotonic()-start)*1000,1)
    if status=='up' and m.get('critical_latency_ms') and elapsed>=m['critical_latency_ms']:
        status='down';error='CRITICAL_LATENCY';detail=f'Kritische Latenz: {elapsed} ms'
    elif status=='up' and m.get('warning_latency_ms') and elapsed>=m['warning_latency_ms']:
        status='warning';detail=f'Hohe Latenz: {elapsed} ms'
    return {'checked':time.time(),'status':status,'code':code,'latency':elapsed,'detail':detail[:240],'error_code':error,'metrics':metrics}
