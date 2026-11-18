"""Durable SMTP outbox. SMTP I/O never occurs inside database transactions."""
import json, smtplib, ssl, time, uuid, email.message
from storage import db, event
from vault import decrypt

MAX_ATTEMPTS=5
BACKOFF=(30,120,600,1800,3600)

def smtp_public(c):
    row=c.execute("SELECT value FROM settings WHERE key='smtp'").fetchone()
    if not row:return dict(enabled=False,host='',port=587,security='starttls',username='',sender='',recipient='',has_password=False)
    data=json.loads(row['value'])
    return {k:v for k,v in data.items() if k!='password_enc'} | {'has_password':bool(data.get('password_enc'))}

def config(c):
    row=c.execute("SELECT value FROM settings WHERE key='smtp'").fetchone()
    return json.loads(row['value']) if row else {}

def enqueue(c, monitor, state, now, detail, suppressed=False):
    if suppressed:return False
    if not ((state=='down' and monitor['notify_down']) or (state=='up' and monitor['notify_up'])):
        return False
    smtp=config(c)
    if not smtp.get('enabled'):return False
    # Administrator-managed recipients are scoped by current group/tag grants.
    # Subscriptions never expand a user's visibility. Global recipient is retained.
    recipients=set()
    if smtp.get('recipient'):recipients.add(smtp['recipient'].strip())
    from access import visible_ids
    for subscription in c.execute('SELECT username,email,notify_down,notify_up FROM alert_subscriptions'):
        if subscription['email'] and subscription['notify_'+state] and monitor['id'] in visible_ids(c,subscription['username']):
            recipients.add(subscription['email'])
    recipients.discard('')
    if not recipients:return False
    # idempotence is guaranteed by caller invoking only on committed transitions
    subject=f"upWATCH! {state.upper()}: {monitor['alias']}"
    msg=f"Monitor: {monitor['alias']}\nZiel: {monitor['target']}\nStatus: {state}\nZeit (UTC): {time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(now))}\nDetails: {detail[:240]}\n"
    for recipient in sorted(recipients):
        c.execute('INSERT INTO mail_outbox(id,created,next_attempt,recipient,subject,body,status) VALUES(?,?,?,?,?,?,?)',
                  (uuid.uuid4().hex,now,now,recipient,subject,msg,'pending'))
    return True

def test_message(c):
    conf=config(c)
    if not conf.get('enabled') or not conf.get('recipient'):raise ValueError('SMTP ist nicht aktiviert oder unvollständig')
    now=time.time()
    c.execute('INSERT INTO mail_outbox(id,created,next_attempt,recipient,subject,body,status) VALUES(?,?,?,?,?,?,?)',
              (uuid.uuid4().hex,now,now,conf['recipient'],'upWATCH! SMTP-Test','Dies ist eine Testnachricht von upWATCH!.','pending'))

def send(conf, recipient, subject, body, smtp_factory=None):
    if not conf.get('enabled'):raise ValueError('SMTP deaktiviert')
    host=conf['host'];port=int(conf['port']);mode=conf['security']
    if mode not in ('ssl','starttls'):raise ValueError('Ungültiger TLS-Modus')
    from security import resolve, connect_entries
    # Policy-enforced DNS resolution, then pin selected numeric address; validated hostname retained for TLS.
    entries=resolve(host,port)
    # SMTP library cannot accept a pre-connected socket via public API: use an adapter and replace socket
    # before SMTP greeting; no second uncontrolled resolution.
    class PinnedSMTP(smtplib.SMTP):
        def _get_socket(self, host, port, timeout):
            return connect_entries(entries,timeout)
    class PinnedSMTPSSL(smtplib.SMTP_SSL):
        def _get_socket(self, host, port, timeout):
            raw=connect_entries(entries,timeout)
            try:return self.context.wrap_socket(raw,server_hostname=host)
            except Exception:raw.close();raise
    ctx=ssl.create_default_context()
    message=email.message.EmailMessage()
    message['From']=conf['sender'];message['To']=recipient;message['Subject']=subject
    message.set_content(body)
    cls=PinnedSMTPSSL if mode=='ssl' else PinnedSMTP
    with cls(host,port,timeout=10,context=ctx) if mode=='ssl' else cls(host,port,timeout=10) as smtp:
        if mode=='starttls':
            smtp.ehlo();smtp.starttls(context=ctx);smtp.ehlo()
        if conf.get('username'):
            if not conf.get('password_enc'):raise ValueError('SMTP-Passwort fehlt')
            smtp.login(conf['username'],decrypt(conf['password_enc']))
        smtp.send_message(message)

def drain(limit=10, sender=send):
    now=time.time()
    ids=[]
    with db() as c:
        # 'sending' is a lease; expired claims become retryable after crash.
        rows=c.execute("SELECT id FROM mail_outbox WHERE ((status='pending' AND next_attempt<=?) OR (status='sending' AND locked_until<?)) AND attempts<? ORDER BY created LIMIT ?",(now,now,MAX_ATTEMPTS,limit)).fetchall()
        for r in rows:
            updated=c.execute("UPDATE mail_outbox SET status='sending', locked_until=? WHERE id=? AND ((status='pending' AND next_attempt<=?) OR (status='sending' AND locked_until<?))",(now+60,r['id'],now,now)).rowcount
            if updated:ids.append(r['id'])
    for mid in ids:
        with db() as c:
            row=c.execute('SELECT * FROM mail_outbox WHERE id=?',(mid,)).fetchone()
            conf=config(c)
        if not row:continue
        try:
            sender(conf,row['recipient'],row['subject'],row['body'])
        except Exception as exc:
            attempts=row['attempts']+1
            # No authentication secrets or message body included in error logs.
            reason=type(exc).__name__
            with db() as c:
                c.execute('UPDATE mail_outbox SET status=?,attempts=?,next_attempt=?,locked_until=NULL,last_error=? WHERE id=?',
                          ('failed' if attempts>=MAX_ATTEMPTS else 'pending',attempts,time.time()+BACKOFF[min(attempts-1,len(BACKOFF)-1)],reason,mid))
                event(c,None,'notification.failed','warning',f'SMTP {reason} (Versuch {attempts})')
        else:
            with db() as c:
                c.execute("UPDATE mail_outbox SET status='sent',attempts=attempts+1,sent_at=?,locked_until=NULL,last_error=NULL WHERE id=?",(time.time(),mid))
                event(c,None,'notification.sent','info','SMTP-Nachricht versendet')
