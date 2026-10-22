"""Accounts and revocable sessions for shared mode. All public errors
are `AuthError`s whose messages are safe to display.

Passwords: PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte salt, 32-byte hash,
so accounts created by the earlier Rust build keep working. Sessions: a random
64-hex token for the client, its SHA-256 in `auth_sessions`, 24 hours, at most
16 per account.

Login throttling is per source and per account. A single global bucket plus
one per username would let anyone lock everybody out, or one account for its
owner. Buckets in `auth_login_attempts`:

- `pair:<user>:<source>` 5 attempts / 15 min — one source guessing one account;
  cleared when that source logs in.
- `source:<source>` 20 failed attempts / 15 min across accounts — one source
  spraying; a successful login refunds its own attempt.
- `account:<user>` 50 failed attempts / 15 min from untrusted sources — the
  distributed guess against one account. One source can spend at most 5 of
  them, so it takes at least ten sources to exhaust it.
- `global` 100 untrusted attempts / min — bounds the total guessing rate and
  password-hashing work.
- `trust:<user>:<source>` written on success and kept 30 days (16 most recent
  per account): a source the owner has logged in from skips the account and
  global buckets and the table-size cap, so even a distributed attack cannot
  lock the owner out of the places they normally log in from. A new source
  during such an attack waits for the window, which is the price of still
  limiting distributed guessing.

`<user>` and `<source>` are SHA-256 hex of the normalized username and source;
a source is an IP address (IPv6 grouped by /64, so one host cannot rotate
through its own prefix), or the caller's string if it is not one.
"""

import hashlib
import hmac
import ipaddress
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from isbe.core import db
from isbe.core.errors import HttpError

ITERATIONS = 600_000
SESSION_SECONDS = 24 * 60 * 60
MAX_SESSIONS = 16
LOGIN_WINDOW = 15 * 60
LOGIN_LIMIT = 5
SOURCE_LIMIT = 20
ACCOUNT_LIMIT = 50
GLOBAL_WINDOW = 60
GLOBAL_LIMIT = 100
TRUST_SECONDS = 30 * 24 * 60 * 60
MAX_TRUSTED = 16
MAX_LOGIN_BUCKETS = 1024

_THROTTLED = "Too many login attempts; try again later"
_INVALID = "Invalid username or password"


class AuthError(HttpError):
    pass


def status(error: BaseException) -> int:
    return error.status if isinstance(error, AuthError) else 500


@contextmanager
def _internal() -> Iterator[None]:
    """Database failures become one generic message without schema details."""
    try:
        yield
    except sqlite3.Error:
        raise AuthError(500, "Authentication service unavailable") from None


@dataclass(frozen=True)
class User:
    id: str
    username: str
    role: str
    active: bool


def local_user() -> User:
    return User("local", "local", "admin", True)


def _username(value: str) -> str:
    if not (
        3 <= len(value.encode("utf-8", "surrogatepass")) <= 64
        and value[0].isascii()
        and value[0].isalnum()
        and all(c.isascii() and (c.isalnum() or c in "_-") for c in value)
    ):
        raise AuthError(
            400,
            "Username must be 3–64 letters, numbers, underscores or hyphens and start with "
            "a letter or number",
        )
    return value.lower()


def valid_id(value: str) -> bool:
    return value == "local" or (len(value) == 32 and all(c in "0123456789abcdef" for c in value))


def _utf8(text: str) -> bytes | None:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _derive(password: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, ITERATIONS, 32)


def _password_material(password: str) -> tuple[bytes, bytes]:
    raw = _utf8(password)
    if raw is None or len(raw) > 1024 or len(password) < 12:
        raise AuthError(400, "Password must contain at least 12 characters and at most 1024 bytes")
    salt = secrets.token_bytes(16)
    return salt, _derive(raw, salt)


def _user(row: sqlite3.Row | tuple) -> User:
    return User(row[0], row[1], row[2], bool(row[3]))


def has_admin(conn: sqlite3.Connection) -> bool:
    with _internal():
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM auth_users WHERE role = 'admin' AND active = 1)"
        ).fetchone()
    return bool(row[0])


def create_user(conn: sqlite3.Connection, name: str, password: str, admin: bool) -> User:
    name = _username(name)
    salt, digest = _password_material(password)
    with _internal(), db.immediate(conn):
        if conn.execute("SELECT EXISTS(SELECT 1 FROM auth_users WHERE username = ?)", (name,)).fetchone()[0]:
            raise AuthError(409, "Username is already in use")
        local = conn.execute("SELECT EXISTS(SELECT 1 FROM auth_users WHERE id = 'local')").fetchone()[0]
        user = User(
            "local" if admin and not local else secrets.token_hex(16),
            name,
            "admin" if admin else "user",
            True,
        )
        conn.execute(
            "INSERT INTO auth_users (id, username, role, password_salt, password_hash, "
            "password_iterations) VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.username, user.role, salt, digest, ITERATIONS),
        )
    return user


@dataclass(frozen=True)
class _Credentials:
    user: User
    salt: bytes
    hash: bytes


def _credentials(conn: sqlite3.Connection, name: str) -> _Credentials | None:
    row = conn.execute(
        "SELECT id, username, role, active, password_salt, password_hash FROM auth_users "
        "WHERE username = ?",
        (name,),
    ).fetchone()
    return None if row is None else _Credentials(_user(row), bytes(row[4]), bytes(row[5]))


def source_key(source: str) -> str:
    """The throttling identity of a client address: IPv4, an IPv6 /64, or the text itself."""
    text = source.strip()
    host = text.strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return text or "unknown"
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


@dataclass(frozen=True)
class _Buckets:
    pair: str
    source: str
    account: str
    trust: str
    trust_prefix: str

    @classmethod
    def of(cls, name: str, source: str) -> _Buckets:
        user, origin = db.sha256(name), db.sha256(source_key(source))
        return cls(
            f"pair:{user}:{origin}",
            f"source:{origin}",
            f"account:{user}",
            f"trust:{user}:{origin}",
            f"trust:{user}:",
        )


def _reserve(conn: sqlite3.Connection, buckets: _Buckets, now: int) -> bool:
    """Count one attempt in every bucket that applies, or refuse it; returns trust."""
    with db.immediate(conn):
        conn.execute(
            "DELETE FROM auth_login_attempts WHERE "
            "(bucket = 'global' AND window_start <= ?) OR "
            "(substr(bucket, 1, 6) = 'trust:' AND window_start <= ?) OR "
            "(bucket <> 'global' AND substr(bucket, 1, 6) <> 'trust:' AND window_start <= ?)",
            (now - GLOBAL_WINDOW, now - TRUST_SECONDS, now - LOGIN_WINDOW),
        )
        trusted = bool(
            conn.execute(
                "SELECT EXISTS(SELECT 1 FROM auth_login_attempts WHERE bucket = ?)",
                (buckets.trust,),
            ).fetchone()[0]
        )
        limits = [(buckets.pair, LOGIN_LIMIT), (buckets.source, SOURCE_LIMIT)]
        if not trusted:
            limits += [(buckets.account, ACCOUNT_LIMIT), ("global", GLOBAL_LIMIT)]
        for key, limit in limits:
            count = conn.execute(
                "SELECT COALESCE((SELECT attempts FROM auth_login_attempts WHERE bucket = ?), 0)",
                (key,),
            ).fetchone()[0]
            if count >= limit:
                raise AuthError(429, _THROTTLED)
            if count == 0 and not trusted:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM auth_login_attempts WHERE substr(bucket, 1, 6) <> 'trust:'"
                ).fetchone()[0]
                if rows >= MAX_LOGIN_BUCKETS:
                    # Fail closed rather than evicting an active account's throttle under a flood.
                    raise AuthError(429, _THROTTLED)
            conn.execute(
                "INSERT INTO auth_login_attempts (bucket, window_start, attempts) VALUES (?, ?, 1) "
                "ON CONFLICT(bucket) DO UPDATE SET attempts = attempts + 1",
                (key, now),
            )
    return trusted


def _refund(conn: sqlite3.Connection, bucket: str) -> None:
    conn.execute(
        "UPDATE auth_login_attempts SET attempts = attempts - 1 WHERE bucket = ? AND attempts > 1",
        (bucket,),
    )
    conn.execute("DELETE FROM auth_login_attempts WHERE bucket = ? AND attempts <= 1", (bucket,))


def login(conn: sqlite3.Connection, name: str, password: str, source: str) -> tuple[User, str]:
    """Verify credentials from client address `source`; returns the user and a new token.

    Wrong, unknown, inactive and malformed credentials all fail with the same
    401 after the same hashing cost; a throttled attempt fails with 429 before
    any hashing.
    """
    try:
        name = _username(name)
    except AuthError:
        name = ""
    buckets = _Buckets.of(name, source)
    now = int(time.time())
    with _internal():
        trusted = _reserve(conn, buckets, now)
        saved = _credentials(conn, name)
    salt, digest = (saved.salt, saved.hash) if saved else (bytes(16), bytes(32))
    raw = _utf8(password)
    bounded = raw if raw is not None and len(raw) <= 1024 else b"invalid"
    # Unknown and inactive accounts pay the same password verification cost.
    verified = hmac.compare_digest(_derive(bounded, salt), digest)
    if not (saved and saved.user.active and verified and raw and 12 <= len(raw) <= 1024):
        raise AuthError(401, _INVALID)
    token = secrets.token_hex(32)
    with _internal(), db.immediate(conn):
        # A reset or deactivation that raced password verification must win.
        current = _credentials(conn, name)
        if not (
            current
            and current.user.active
            and current.hash == saved.hash
            and current.salt == saved.salt
        ):
            raise AuthError(401, _INVALID)
        conn.execute("DELETE FROM auth_login_attempts WHERE bucket = ?", (buckets.pair,))
        _refund(conn, buckets.source)
        if not trusted:
            _refund(conn, buckets.account)
        conn.execute(
            "INSERT INTO auth_login_attempts (bucket, window_start, attempts) VALUES (?, ?, 1) "
            "ON CONFLICT(bucket) DO UPDATE SET window_start = excluded.window_start",
            (buckets.trust, now),
        )
        conn.execute(
            "DELETE FROM auth_login_attempts WHERE substr(bucket, 1, ?) = ? AND bucket NOT IN "
            "(SELECT bucket FROM auth_login_attempts WHERE substr(bucket, 1, ?) = ? "
            "ORDER BY window_start DESC, bucket LIMIT ?)",
            (len(buckets.trust_prefix), buckets.trust_prefix) * 2 + (MAX_TRUSTED,),
        )
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        conn.execute(
            "DELETE FROM auth_sessions WHERE user_id = ?1 AND token_hash NOT IN (SELECT "
            "token_hash FROM auth_sessions WHERE user_id = ?1 ORDER BY created_at DESC, rowid "
            f"DESC LIMIT {MAX_SESSIONS - 1})",
            (current.user.id,),
        )
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (db.sha256(token), current.user.id, now, now + SESSION_SECONDS),
        )
    return current.user, token


def valid_token(token: str) -> bool:
    return len(token) == 64 and all(c in "0123456789abcdef" for c in token)


def authenticate(conn: sqlite3.Connection, token: str) -> User | None:
    if not valid_token(token):
        return None
    with _internal():
        row = conn.execute(
            "SELECT u.id, u.username, u.role, u.active FROM auth_sessions s "
            "JOIN auth_users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
            (db.sha256(token), int(time.time())),
        ).fetchone()
    return None if row is None else _user(row)


def logout(conn: sqlite3.Connection, token: str) -> None:
    if valid_token(token):
        with _internal():
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (db.sha256(token),))


def list_users(conn: sqlite3.Connection) -> list[User]:
    with _internal():
        rows = conn.execute(
            "SELECT id, username, role, active FROM auth_users ORDER BY username"
        ).fetchall()
    return [_user(row) for row in rows]


def update_user(
    conn: sqlite3.Connection,
    user_id: str,
    password: str | None = None,
    active: bool | None = None,
) -> User:
    """Reset a password and/or (de)activate; either revokes every session of the account."""
    if not valid_id(user_id):
        raise AuthError(400, "Invalid account identifier")
    material = None if password is None else _password_material(password)
    with _internal(), db.immediate(conn):
        row = conn.execute(
            "SELECT id, username, role, active FROM auth_users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise AuthError(404, "Account not found")
        user = _user(row)
        if active is False and user.active and user.role == "admin":
            admins = conn.execute(
                "SELECT COUNT(*) FROM auth_users WHERE role = 'admin' AND active = 1"
            ).fetchone()[0]
            if admins <= 1:
                raise AuthError(409, "The last active administrator cannot be disabled")
        if material is not None:
            conn.execute(
                "UPDATE auth_users SET password_salt = ?, password_hash = ?, "
                "password_iterations = ? WHERE id = ?",
                (*material, ITERATIONS, user_id),
            )
        if active is not None:
            conn.execute("UPDATE auth_users SET active = ? WHERE id = ?", (int(active), user_id))
            user = User(user.id, user.username, user.role, active)
        if password is not None or active is False:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    return user
