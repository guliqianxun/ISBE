-- Question runs and their search stage: the runs themselves, the event stream, every
-- essaies query with its raw response, every hit, the paper sets and the set operations
-- that derive them. Rows are only ever added, except a run's status.

-- One research question being worked on. Writer: question.runs
CREATE TABLE question_runs (
  run_id     TEXT PRIMARY KEY,
  question   TEXT NOT NULL,
  lang       TEXT NOT NULL CHECK (lang IN ('zh', 'en')),
  status     TEXT NOT NULL CHECK (status IN ('queued', 'running', 'paused', 'cancelled', 'failed', 'done')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- `question_event@1` per run, seq 1, 2, … Writer: question.runs
CREATE TABLE question_events (
  run_id TEXT NOT NULL REFERENCES question_runs (run_id),
  seq    INTEGER NOT NULL CHECK (seq >= 1),
  at     TEXT NOT NULL,
  type   TEXT NOT NULL,
  data   TEXT NOT NULL,                       -- the event's data object
  PRIMARY KEY (run_id, seq)
);

-- One essaies search made for a plan query, as it came back. A run searched again
-- (an edited plan) numbers its searches 1, 2, … Writer: question.search
CREATE TABLE question_queries (
  run_id       TEXT NOT NULL REFERENCES question_runs (run_id),
  search       INTEGER NOT NULL CHECK (search >= 1),
  query_id     TEXT NOT NULL,
  plan_version INTEGER NOT NULL,
  query        TEXT NOT NULL,
  status       INTEGER,                       -- HTTP status of the last attempt; NULL when none was answered
  error        TEXT,                          -- why the query failed; NULL when it worked
  response     TEXT,                          -- essaies' JSON body(ies) as returned, one per page
  hits         INTEGER NOT NULL DEFAULT 0,
  ms           INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (run_id, search, query_id)
);

-- One paper a query returned, at its first rank. Writer: question.search
CREATE TABLE question_hits (
  run_id   TEXT NOT NULL,
  search   INTEGER NOT NULL,
  query_id TEXT NOT NULL,
  rank     INTEGER NOT NULL CHECK (rank >= 1),
  key      TEXT NOT NULL,                     -- the paper's preferred identity key
  new      INTEGER NOT NULL CHECK (new IN (0, 1)),  -- 0: the search already had this key
  paper    TEXT NOT NULL,                     -- the paper record (common#paper)
  PRIMARY KEY (run_id, search, query_id, rank),
  FOREIGN KEY (run_id, search, query_id) REFERENCES question_queries (run_id, search, query_id)
);

-- `paper_set@1` documents. Writer: question.sets
CREATE TABLE paper_sets (
  run_id     TEXT NOT NULL REFERENCES question_runs (run_id),
  set_id     TEXT NOT NULL,
  kind       TEXT NOT NULL,
  made_by    TEXT,                            -- the set_op that produced it
  size       INTEGER NOT NULL,
  doc        TEXT NOT NULL,                   -- the paper_set@1 document
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, set_id)
);

-- `set_op@1` documents, in the order they ran. Writer: question.sets
CREATE TABLE set_ops (
  run_id     TEXT NOT NULL REFERENCES question_runs (run_id),
  op_id      TEXT NOT NULL,
  position   INTEGER NOT NULL,
  op         TEXT NOT NULL,
  output     TEXT NOT NULL,
  doc        TEXT NOT NULL,                   -- the set_op@1 document
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, op_id),
  UNIQUE (run_id, position)
);
