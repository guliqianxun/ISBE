-- The queue of question runs, read by the single worker of the root data directory
-- (the run itself, its events and results live in its owner's workspace). One row
-- per run; the worker takes a step when `ready_at` has passed. Writer: question.queue
CREATE TABLE question_tasks (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL UNIQUE,
  owner      TEXT NOT NULL,
  request_id TEXT,                            -- the client's idempotency key, per owner
  state      TEXT NOT NULL CHECK (state IN ('waiting', 'running', 'paused', 'finished')),
  step       TEXT NOT NULL CHECK (step IN ('plan', 'search', 'screen', 'finish')),
  ready_at   TEXT,                            -- when the step may start; NULL: held (the plan is being edited)
  started    INTEGER NOT NULL DEFAULT 0 CHECK (started IN (0, 1)),  -- 1: the step began and stopped part way
  control    TEXT CHECK (control IN ('pause', 'cancel')),            -- asked of the step in hand
  options    TEXT NOT NULL,                   -- JSON: years, review, engine, overrides carried over
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (owner, request_id)
);
CREATE INDEX question_tasks_ready ON question_tasks (state, ready_at, seq);
CREATE INDEX question_tasks_owner ON question_tasks (owner, seq);
