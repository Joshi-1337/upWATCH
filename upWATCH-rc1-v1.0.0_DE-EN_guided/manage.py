"""Provision initial administrator and database."""
import getpass, hashlib, secrets, sys
from storage import db, initialize

def digest(password,salt):return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
def main():
    if sys.argv[1:]!=['init']:raise SystemExit('Usage: python manage.py init')
    initialize()
    with db() as c:
        if c.execute('SELECT 1 FROM users').fetchone():raise SystemExit('Users already exist; refusing to overwrite them.')
    username=input('Administrator username: ').strip()
    if not username or len(username)>80:raise SystemExit('Invalid username.')
    password=getpass.getpass('Administrator password (minimum 14 characters): ')
    if len(password)<14 or password!=getpass.getpass('Repeat password: '):raise SystemExit('Invalid password or passwords do not match.')
    salt=secrets.token_hex(16)
    with db() as c:c.execute('INSERT INTO users(username,password_hash,salt,role) VALUES(?,?,?,?)',(username,digest(password,salt),salt,'admin'))
    print('Administrator account created.')
if __name__=='__main__':main()
