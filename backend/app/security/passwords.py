"""Password hashing.

User passwords are low-entropy secrets that an attacker may try to guess offline
after a database disclosure, so they are hashed with **Argon2id**, a memory-hard
key-derivation function, rather than a fast digest.

Session tokens are deliberately *not* handled here. They are 256-bit random
values with nothing to guess, so they use a fast SHA-256 digest instead (see
`app.security.tokens`). Using a slow KDF there would only add latency to every
authenticated request.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Library defaults (Argon2id, time_cost=3, memory_cost=64 MiB, parallelism=4)
# are appropriate here and are deliberately not weakened for speed. Password
# hashing happens at login and at seed time only.
_hasher = PasswordHasher()

# A pre-computed hash of a value nobody can supply. Used to spend the same
# verification work when an account does not exist, so response timing does not
# reveal whether an email address is registered.
_DUMMY_HASH = _hasher.hash("newtonite-dummy-password-for-login-timing")


def hash_password(plaintext: str) -> str:
    """Return an Argon2id hash for `plaintext`."""
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Return True when `plaintext` matches `password_hash`.

    Never raises for malformed input: an unrecognised hash is simply a failed
    verification, which keeps callers free of exception handling around
    untrusted data.
    """
    try:
        return _hasher.verify(password_hash, plaintext)
    except (VerificationError, InvalidHashError):
        return False


def spend_dummy_verification() -> None:
    """Burn a comparable amount of time when no user matched.

    Call this on the "email not found" path so that a missing account and a wrong
    password take approximately the same time to answer.
    """
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-password")
    except VerificationError:
        pass


__all__ = ["hash_password", "spend_dummy_verification", "verify_password"]
