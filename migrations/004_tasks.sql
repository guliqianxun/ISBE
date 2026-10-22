-- The root database owns the durable FIFO; workspace runs link back by task id.
CREATE TABLE tasks (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL,
  topic TEXT NOT NULL,
  request_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued','running','succeeded','failed','interrupted','cancelled')),
  stage TEXT NOT NULL CHECK (stage IN ('queued','collect','triage','enrich','digest')),
  error TEXT,
  result_period TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  topic_snapshot TEXT NOT NULL,
  settings_fingerprint TEXT NOT NULL,
  UNIQUE (owner, request_id)
);
CREATE INDEX tasks_fifo ON tasks (state, seq);
CREATE INDEX tasks_owner ON tasks (owner, seq);
CREATE UNIQUE INDEX tasks_active_topic ON tasks (owner, topic) WHERE state IN ('queued','running');
-- An overlapping submission keeps its own idempotency key after the task ends.
CREATE TABLE task_requests (
  owner TEXT NOT NULL,
  request_id TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (owner, request_id)
);
ALTER TABLE runs ADD COLUMN task_id TEXT;
CREATE INDEX runs_task ON runs (task_id);
