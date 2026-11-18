"""RFC 6238 TOTP, one-use recovery codes, enforced replay protection."""
import base64,hashlib,hmac,secrets,struct,time,urllib.parse,io
from vault import encrypt,decrypt

def secret():return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
def code(secret_value,step):
    key=base64.b32decode(secret_value+'='*((-len(secret_value))%8),casefold=True)
    mac=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest();offset=mac[-1]&15
    return f'{(struct.unpack(">I",mac[offset:offset+4])[0]&0x7fffffff)%1000000:06d}'
def matching_step(secret_value,otp,now=None,last=-1):
    if not isinstance(otp,str) or len(otp)!=6 or not otp.isascii() or not otp.isdigit():return None
    step=int((time.time() if now is None else now)//30)
    for candidate in (step-1,step,step+1):
        if candidate>last and hmac.compare_digest(code(secret_value,candidate),otp):return candidate
    return None
def recovery_codes():return [secrets.token_hex(5).upper() for _ in range(10)]
def recovery_digest(value):return hashlib.sha256(value.strip().upper().encode('ascii')).hexdigest()
def verify_factor(c,user,token,now=None):
    row=c.execute('SELECT secret_enc,last_step FROM user_mfa WHERE username=?',(user,)).fetchone()
    if not row:return False
    step=matching_step(decrypt(row['secret_enc']),token,now,row['last_step'])
    if step is not None:
        return bool(c.execute('UPDATE user_mfa SET last_step=? WHERE username=? AND last_step<?',(step,user,step)).rowcount)
    if not isinstance(token,str):return False
    try:hashval=recovery_digest(token)
    except (UnicodeError,ValueError):return False
    return bool(c.execute('DELETE FROM mfa_recovery WHERE username=? AND code_hash=?',(user,hashval)).rowcount)
def uri(user,secret_value):
    return 'otpauth://totp/'+urllib.parse.quote('upWATCH!:'+user,safe='')+'?'+urllib.parse.urlencode({'secret':secret_value,'issuer':'upWATCH!','algorithm':'SHA1','digits':6,'period':30})
def replace_codes(c,user):
    values=recovery_codes()
    c.execute('DELETE FROM mfa_recovery WHERE username=?',(user,))
    c.executemany('INSERT INTO mfa_recovery(username,code_hash) VALUES(?,?)',[(user,recovery_digest(x)) for x in values])
    return values


def qr_svg_data_uri(otpauth_uri):
    """Create TOTP QR image on our server; never send the secret to a QR API."""
    import qrcode
    from qrcode.image.svg import SvgPathImage
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(otpauth_uri)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return 'data:image/svg+xml;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
