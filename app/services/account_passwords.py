"""Salted scrypt passwords for individual browser accounts."""
import hashlib
import secrets

def hash_password(password: str) -> str:
    if not 10 <= len(password) <= 128:
        raise ValueError('Use a password between 10 and 128 characters.')
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${digest}'

def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split('$')
        if algorithm != 'scrypt' or len(password) > 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
