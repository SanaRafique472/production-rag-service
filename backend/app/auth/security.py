"""Password hashing (Argon2 via pwdlib) and JWT access tokens (PyJWT).

* Passwords are never stored: only an Argon2 hash, which is slow to brute-force.
* A JWT is a signed token containing the user id (``sub``) and an expiry
  (``exp``). The server verifies the signature with ``JWT_SECRET_KEY``, so a
  token cannot be forged or changed without the secret.
"""

from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.config import Settings
from app.errors import AuthenticationError

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Return an Argon2 hash of ``password``."""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Check a password against its stored hash."""
    return _hasher.verify(password, hashed)


def create_access_token(user_id: str, settings: Settings) -> tuple[str, int]:
    """Return a signed JWT for ``user_id`` and its lifetime in seconds."""
    expires = timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "exp": datetime.now(UTC) + expires, "iat": datetime.now(UTC)}
    token = jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)
    return token, int(expires.total_seconds())


def decode_access_token(token: str, settings: Settings) -> str:
    """Verify a JWT and return the user id, or raise ``AuthenticationError``."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key.get_secret_value(), algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Your session has expired. Please log in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token.") from exc
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise AuthenticationError("Invalid authentication token.")
    return sub
