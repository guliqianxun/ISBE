import hashlib
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from isbe import auth
from isbe.core import assets, db
from isbe.core.errors import describe

PASSWORD = "test password long enough"
HOME = "198.51.100.7"
SCHEMA = assets.text("migrations", "005_auth.sql")


def database(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path, autocommit=True, timeout=5, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = database()
    conn.executescript(SCHEMA)
    return conn


@pytest.fixture(autouse=True)
def fast_hash(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """One PBKDF2 round instead of 600 000 except where a test checks the real cost."""
    if "real_hash" not in request.keywords:
        monkeypatch.setattr(
            auth, "_derive", lambda password, salt: hashlib.pbkdf2_hmac("sha256", password, salt, 1, 32)
        )


def login(conn: sqlite3.Connection, name: str, password: str = PASSWORD, source: str = HOME):  # type: ignore[no-untyped-def]
    return auth.login(conn, name, password, source)


def fails(call, *args, **kwargs) -> int:  # type: ignore[no-untyped-def]
    with pytest.raises(auth.AuthError) as caught:
        call(*args, **kwargs)
    return caught.value.status


def test_accounts_are_normalized_private_and_salted(conn: sqlite3.Connection) -> None:
    assert not auth.has_admin(conn)
    admin = auth.create_user(conn, "Owner", PASSWORD, True)
    assert (admin.id, admin.username) == ("local", "owner")
    assert auth.has_admin(conn)
    second = auth.create_user(conn, "Member_2", PASSWORD, False)
    assert len(second.id) == 32 and auth.valid_id(second.id)
    assert second.role == "user"
    first, other = auth._credentials(conn, "owner"), auth._credentials(conn, "member_2")
    assert first is not None and other is not None
    assert (len(first.salt), len(first.hash)) == (16, 32)
    assert first.salt != other.salt and first.hash != other.hash
    assert first.hash != PASSWORD.encode()
    for user in auth.list_users(conn):
        assert set(vars(user)) == {"id", "username", "role", "active"}
    assert fails(auth.create_user, conn, "OWNER", PASSWORD, False) == 409


@pytest.mark.real_hash
def test_password_hashes_use_the_stored_parameters(conn: sqlite3.Connection) -> None:
    user = auth.create_user(conn, "owner", PASSWORD, True)
    salt, digest, iterations = conn.execute(
        "SELECT password_salt, password_hash, password_iterations FROM auth_users"
    ).fetchone()
    assert iterations == auth.ITERATIONS == 600_000
    assert digest == hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, 600_000, 32)
    assert login(conn, "owner")[0] == user


def test_validates_password_username_and_path_boundaries(conn: sqlite3.Connection) -> None:
    for name in ["", "ab", "../alice", "a/b", "a\\b", "a.b", "-alice", " alice", "你好世界", "é" * 3]:
        assert fails(auth.create_user, conn, name, PASSWORD, False) == 400, name
    assert fails(auth.create_user, conn, "a" * 65, PASSWORD, False) == 400
    for password in ["x" * 11, "x" * 1025, "密" * 11, "\udc80" * 12]:
        assert fails(auth.create_user, conn, "alice", password, False) == 400
    for user_id in ["../local", "LOCAL", "", "a" * 31 + "A"]:
        assert fails(auth.update_user, conn, user_id) == 400
    assert fails(auth.update_user, conn, "local") == 404
    assert auth.local_user() == auth.User("local", "local", "admin", True)


def test_wrong_unknown_and_malformed_credentials_have_the_same_error(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, "invalid", PASSWORD, True)
    errors = []
    for name, password in [("invalid", "wrong password"), ("unknown", PASSWORD), ("../invalid", PASSWORD)]:
        with pytest.raises(auth.AuthError) as caught:
            login(conn, name, password)
        errors.append(caught.value)
    assert {e.status for e in errors} == {401}
    assert len({str(e) for e in errors}) == 1
    assert fails(login, conn, "invalid", "x" * 1025) == 401
    assert fails(login, conn, "invalid", "\udc80" * 12) == 401


def test_opaque_sessions_expire_and_logout_revokes(conn: sqlite3.Connection) -> None:
    admin = auth.create_user(conn, "admin", PASSWORD, True)
    user, token = login(conn, "ADMIN")
    assert user == admin
    assert auth.valid_token(token)
    assert auth.authenticate(conn, token) == admin
    stored, lifetime = conn.execute(
        "SELECT token_hash, expires_at - created_at FROM auth_sessions"
    ).fetchone()
    assert stored != token and stored == db.sha256(token)
    assert lifetime == auth.SESSION_SECONDS
    assert auth.authenticate(conn, "../session") is None
    auth.logout(conn, token)
    auth.logout(conn, token)
    assert auth.authenticate(conn, token) is None
    _, expiring = login(conn, "admin")
    conn.execute("UPDATE auth_sessions SET created_at = 0, expires_at = 1")
    assert auth.authenticate(conn, expiring) is None


def test_reset_and_deactivation_revoke_all_sessions(conn: sqlite3.Connection) -> None:
    user = auth.create_user(conn, "member", PASSWORD, False)
    _, first = login(conn, "member")
    _, second = login(conn, "member")
    changed = "replacement password long enough"
    auth.update_user(conn, user.id, password=changed)
    assert auth.authenticate(conn, first) is None and auth.authenticate(conn, second) is None
    assert fails(login, conn, "member") == 401
    _, fresh = login(conn, "member", changed)
    assert not auth.update_user(conn, user.id, active=False).active
    assert auth.authenticate(conn, fresh) is None
    assert fails(login, conn, "member", changed) == 401
    auth.update_user(conn, user.id, active=True)
    assert auth.authenticate(conn, fresh) is None
    login(conn, "member", changed)


def test_last_active_admin_is_preserved_atomically(conn: sqlite3.Connection) -> None:
    first = auth.create_user(conn, "admin_one", PASSWORD, True)
    assert fails(auth.update_user, conn, first.id, "changed password value", False) == 409
    login(conn, "admin_one")  # the rejected change must not reset the password
    second = auth.create_user(conn, "admin_two", PASSWORD, True)
    assert second.id != "local"
    auth.update_user(conn, first.id, active=False)
    assert fails(auth.update_user, conn, second.id, active=False) == 409
    assert auth.has_admin(conn)


def test_throttle_survives_reopen_and_expires(tmp_path: Path) -> None:
    path = tmp_path / "auth.db"
    conn = database(path)
    conn.executescript(SCHEMA)
    for _ in range(auth.LOGIN_LIMIT):
        assert fails(login, conn, "unknown") == 401
    conn.close()
    conn = database(path)
    assert fails(login, conn, "UNKNOWN") == 429
    conn.execute("UPDATE auth_login_attempts SET window_start = 0")
    assert fails(login, conn, "unknown") == 401
    conn.close()


def test_throttle_is_bounded_and_cannot_be_evicted_by_unknown_names(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    for i in range(auth.MAX_LOGIN_BUCKETS - 1):
        conn.execute("INSERT INTO auth_login_attempts VALUES (?, ?, 1)", (f"user:{i}", now))
    conn.execute("INSERT INTO auth_login_attempts VALUES ('global', ?, 1)", (now,))
    assert fails(login, conn, "new_account") == 429
    assert conn.execute("SELECT COUNT(*) FROM auth_login_attempts").fetchone()[0] == auth.MAX_LOGIN_BUCKETS
    conn.execute("DELETE FROM auth_login_attempts WHERE bucket <> 'global'")
    conn.execute("UPDATE auth_login_attempts SET attempts = ?", (auth.GLOBAL_LIMIT,))
    assert fails(login, conn, "new_account") == 429
    assert conn.execute("SELECT COUNT(*) FROM auth_login_attempts").fetchone()[0] == 1


def test_stored_sessions_are_bounded_and_expired_rows_are_cleaned(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, "admin", PASSWORD, True)
    now = int(time.time())
    for i in range(25):
        conn.execute(
            "INSERT INTO auth_sessions VALUES (?, 'local', ?, ?)",
            (f"{i:064x}", now - 1, now + auth.SESSION_SECONDS),
        )
    conn.execute("INSERT INTO auth_sessions VALUES (?, 'local', 0, 1)", ("f" * 64,))
    _, token = login(conn, "admin")
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 16
    assert auth.authenticate(conn, token) is not None


def test_internal_database_errors_are_safe() -> None:
    conn = database()
    for call in [
        lambda: login(conn, "admin"),
        lambda: auth.has_admin(conn),
        lambda: auth.authenticate(conn, "a" * 64),
        lambda: auth.create_user(conn, "admin", PASSWORD, True),
    ]:
        with pytest.raises(auth.AuthError) as caught:
            call()
        assert auth.status(caught.value) == 500
        assert str(caught.value) == "Authentication service unavailable"
        assert "auth_" not in describe(caught.value)
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert auth.status(RuntimeError("other")) == 500


def test_concurrent_admin_deactivation_keeps_one_administrator(tmp_path: Path) -> None:
    path = tmp_path / "race.db"
    setup = database(path)
    setup.executescript(SCHEMA)
    ids = [auth.create_user(setup, name, PASSWORD, True).id for name in ["admin_one", "admin_two"]]
    setup.close()
    barrier = threading.Barrier(2)
    outcomes: list[int] = []

    def deactivate(user_id: str) -> None:
        conn = database(path)
        barrier.wait()
        try:
            auth.update_user(conn, user_id, active=False)
            outcomes.append(200)
        except auth.AuthError as error:
            outcomes.append(error.status)

    threads = [threading.Thread(target=deactivate, args=(user_id,)) for user_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == [200, 409]
    assert auth.has_admin(database(path))


def test_a_reset_racing_password_verification_wins(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    user = auth.create_user(conn, "member", PASSWORD, False)
    fast = auth._derive
    raced: list[bool] = []

    def reset_during_verification(password: bytes, salt: bytes) -> bytes:
        if not raced:
            raced.append(True)
            auth.update_user(conn, user.id, password="replacement password long enough")
        return fast(password, salt)

    monkeypatch.setattr(auth, "_derive", reset_during_verification)
    assert fails(login, conn, "member") == 401
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


def test_source_addresses_are_grouped_for_throttling() -> None:
    assert auth.source_key(" 203.0.113.9 ") == "203.0.113.9"
    assert auth.source_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert auth.source_key("2001:db8:1:2:aaaa::1") == auth.source_key("[2001:db8:1:2:ffff::9]")
    assert auth.source_key("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert auth.source_key("2001:db8:1:3::1") != auth.source_key("2001:db8:1:2::1")
    assert auth.source_key("fe80::1%eth0") == "fe80::/64"
    assert auth.source_key("unix-socket") == "unix-socket"
    assert auth.source_key("") == "unknown"


def test_one_source_cannot_lock_the_account_for_other_sources(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, "owner", PASSWORD, True)
    attacker = "203.0.113.66"
    for _ in range(auth.LOGIN_LIMIT):
        assert fails(login, conn, "owner", "wrong password", attacker) == 401
    assert fails(login, conn, "owner", PASSWORD, attacker) == 429, "the attacker's own pair is throttled"
    assert login(conn, "owner", PASSWORD, HOME)[0].username == "owner"
    assert login(conn, "owner", PASSWORD, "2001:db8:9::1")[0].username == "owner", "a new source works too"
    # Spraying other accounts from the same source stops at the source limit.
    for i in range(auth.SOURCE_LIMIT - auth.LOGIN_LIMIT):
        assert fails(login, conn, f"user_{i}", "wrong password", attacker) == 401
    assert fails(login, conn, "another_name", "wrong password", attacker) == 429
    assert login(conn, "owner", PASSWORD, "192.0.2.44")[0].username == "owner"


def test_owner_logs_in_from_a_trusted_source_during_a_distributed_attack(
    conn: sqlite3.Connection,
) -> None:
    auth.create_user(conn, "owner", PASSWORD, True)
    login(conn, "owner")  # the owner's usual address becomes trusted
    sources = [f"203.0.113.{n}" for n in range(auth.ACCOUNT_LIMIT // auth.LOGIN_LIMIT)]
    for source in sources:
        for _ in range(auth.LOGIN_LIMIT):
            assert fails(login, conn, "owner", "wrong password", source) == 401
    # The account bucket is exhausted: guessing from yet another source is refused before hashing.
    assert fails(login, conn, "owner", PASSWORD, "198.18.7.7") == 429
    assert fails(login, conn, "owner", "wrong password", "198.18.7.8") == 429
    # Exhaust the global budget as well; the owner's trusted address is unaffected by both.
    conn.execute("UPDATE auth_login_attempts SET attempts = ? WHERE bucket = 'global'", (auth.GLOBAL_LIMIT,))
    user, token = login(conn, "owner")
    assert user.username == "owner" and auth.authenticate(conn, token) == user
    # A wrong password from the trusted address still counts against its own pair.
    for _ in range(auth.LOGIN_LIMIT):
        assert fails(login, conn, "owner", "wrong password") == 401
    assert fails(login, conn, "owner") == 429


def test_distributed_guessing_is_still_limited(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, "victim", PASSWORD, False)
    attempts = 0
    for n in range(40):
        source = f"2001:db8:{n:x}::1"
        for _ in range(auth.LOGIN_LIMIT + 1):
            try:
                login(conn, "victim", "wrong password", source)
            except auth.AuthError as error:
                if error.status == 401:
                    attempts += 1
    assert attempts == auth.ACCOUNT_LIMIT, "at most ACCOUNT_LIMIT guesses per window, however many sources"
    # Rotating addresses inside one IPv6 /64 is one source.
    conn.execute("DELETE FROM auth_login_attempts")
    for n in range(auth.LOGIN_LIMIT):
        assert fails(login, conn, "victim", "wrong password", f"2001:db8:aa:bb::{n + 1:x}") == 401
    assert fails(login, conn, "victim", "wrong password", "2001:db8:aa:bb::ffff") == 429


def test_successes_refund_and_trust_is_bounded(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, "owner", PASSWORD, True)
    for _ in range(3 * auth.SOURCE_LIMIT):
        login(conn, "owner")
    assert fails(login, conn, "owner", "wrong password") == 401, "successful logins never exhaust a source"
    for n in range(auth.MAX_TRUSTED + 4):
        login(conn, "owner", PASSWORD, f"192.0.2.{n + 1}")
    trusted = conn.execute(
        "SELECT COUNT(*) FROM auth_login_attempts WHERE bucket LIKE 'trust:%'"
    ).fetchone()[0]
    assert trusted == auth.MAX_TRUSTED
    conn.execute("UPDATE auth_login_attempts SET window_start = window_start - ?", (auth.TRUST_SECONDS,))
    login(conn, "owner", PASSWORD, "192.0.2.1")
    assert conn.execute(
        "SELECT COUNT(*) FROM auth_login_attempts WHERE bucket LIKE 'trust:%'"
    ).fetchone()[0] == 1
    buckets = [row[0] for row in conn.execute("SELECT bucket FROM auth_login_attempts")]
    assert all("192.0.2" not in bucket and "owner" not in bucket for bucket in buckets)
