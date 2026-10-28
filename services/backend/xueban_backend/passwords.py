"""Versioned memory-hard password storage; no plaintext or password normalization."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import stat

N, R, P = 2**17, 8, 1
DUMMY_HASH = 'scrypt$v1$131072$8$1$' + '00' * 16 + '$' + '00' * 32


def validate_password(password: str):
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise ValueError('Password must contain 15 to 128 characters')
    try:
        if len(password.encode('utf-8')) > 512:
            raise ValueError('Password exceeds byte limit')
    except UnicodeError:
        raise ValueError('Password encoding is invalid') from None


def validate_student_password(password: str):
    if not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9]{3,15}', password):
        raise ValueError('Student password must contain 3 to 15 ASCII letters or digits')


def validate_login_password(password: str):
    # Authentication must accept both existing long passwords and student-set
    # short passwords. New-password policies are enforced separately by role.
    if not isinstance(password, str) or not 3 <= len(password) <= 128:
        raise ValueError('Password must contain 3 to 128 characters')
    try:
        if len(password.encode('utf-8')) > 512:
            raise ValueError('Password exceeds byte limit')
    except UnicodeError:
        raise ValueError('Password encoding is invalid') from None


def hash_password(password: str, *, validator=validate_password) -> str:
    validator(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=N, r=R, p=P, dklen=32, maxmem=256 * 1024 * 1024)
    return f'scrypt$v1${N}${R}${P}${salt.hex()}${derived.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        # Reject malformed/attacker-selected cost parameters before allocating.
        algorithm, version, n, r, p, salt, expected = encoded.split('$')
        if (algorithm, version, n, r, p) != ('scrypt', 'v1', str(N), str(R), str(P)) or len(salt) != 32 or len(expected) != 64:
            return False
        if not isinstance(password, str) or len(password) > 128 or len(password.encode('utf-8')) > 512:
            return False
        result = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=N, r=R, p=P, dklen=32, maxmem=256 * 1024 * 1024)
        return hmac.compare_digest(result, bytes.fromhex(expected))
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return False


def read_password_file(path: Path) -> str:
    # O_NOFOLLOW and fstat avoid a symlink/permission-check race.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_size > 1024:
            raise ValueError('Password file must be owned by this user with mode 0600')
        try:
            value = file.read(1025).decode('utf-8')
        except UnicodeError:
            raise ValueError('Password file must be UTF-8') from None
    if value.endswith('\n'):
        value = value[:-1]
    validate_password(value)
    return value
