CREATE TABLE auth_users (
    id TEXT PRIMARY KEY CHECK (id = 'local' OR (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*')),
    username TEXT NOT NULL UNIQUE COLLATE NOCASE
        CHECK (length(username) BETWEEN 3 AND 64 AND username = lower(username)
               AND username NOT GLOB '*[^a-z0-9_-]*' AND substr(username, 1, 1) GLOB '[a-z0-9]'),
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    password_salt BLOB NOT NULL CHECK (length(password_salt) = 16),
    password_hash BLOB NOT NULL CHECK (length(password_hash) = 32),
    password_iterations INTEGER NOT NULL CHECK (password_iterations = 600000)
);

CREATE TABLE auth_sessions (
    token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
    user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK (expires_at > created_at)
);
CREATE INDEX auth_sessions_user ON auth_sessions(user_id, created_at);
CREATE INDEX auth_sessions_expiry ON auth_sessions(expires_at);

CREATE TABLE auth_login_attempts (
    bucket TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts > 0)
);
