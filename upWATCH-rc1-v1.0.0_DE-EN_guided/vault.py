"""Fernet authenticated encryption. Key is a separate root-provisioned file."""
import os
from cryptography.fernet import Fernet

KEY_FILE_DEFAULT='/etc/upwatch/vault.key'

def _cipher():
    path=os.environ.get('UPWATCH_VAULT_KEY_FILE',KEY_FILE_DEFAULT)
    with open(path,'rb') as fh:
        key=fh.read().strip()
    return Fernet(key)

def encrypt(value):
    return _cipher().encrypt(value.encode('utf-8')).decode('ascii')

def decrypt(value):
    return _cipher().decrypt(value.encode('ascii')).decode('utf-8')
