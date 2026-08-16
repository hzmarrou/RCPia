"""Password hashing and opaque token handling.

Two kinds of secret exist in RCPia's auth: passwords, which are hashed with Argon2id and
verified by the hashing library, and bearer-style tokens (invitations, sessions), which
are random strings stored only as SHA-256 digests. Tokens are high-entropy, so a fast
digest is appropriate; passwords are not, so they get a deliberately slow KDF.
"""

import hashlib
import hmac
import secrets

import anyio.to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
TOKEN_BYTES = 32

_hasher = PasswordHasher()


class WeakPasswordError(ValueError):
    """Raised when a chosen password does not meet the minimum policy."""


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a password. A user without a usable hash can never authenticate."""
    if not password_hash:
        # Spend the time anyway: answering instantly for unknown accounts would let an
        # attacker distinguish real from fake email addresses by response time.
        _hasher.hash(secrets.token_urlsafe(16))
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


async def hash_password_async(password: str) -> str:
    """Argon2 is deliberately slow, so it must not run on the event loop."""
    validate_password(password)
    return await anyio.to_thread.run_sync(_hasher.hash, password)


async def verify_password_async(password: str, password_hash: str | None) -> bool:
    return await anyio.to_thread.run_sync(verify_password, password, password_hash)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long"
        )


def generate_token() -> str:
    """A URL-safe secret handed to the user once and never stored in the clear."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)


def normalize_email(email: str) -> str:
    return email.strip().lower()
