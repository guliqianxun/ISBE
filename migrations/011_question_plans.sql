-- Search plans of question runs (`question_plan@1`), one row per version; an edit
-- adds a version and never changes an earlier one. Writer: question.plan
CREATE TABLE question_plans (
  run_id         TEXT NOT NULL,
  version        INTEGER NOT NULL CHECK (version >= 1),
  parent_version INTEGER CHECK (parent_version < version),
  source         TEXT NOT NULL CHECK (source IN ('model', 'fallback', 'human')),
  plan           TEXT NOT NULL,                -- the question_plan@1 document
  ledger_run_id  TEXT REFERENCES runs (id),    -- the planning job, whose model calls are in llm_calls
  created_at     TEXT NOT NULL,
  PRIMARY KEY (run_id, version),
  FOREIGN KEY (run_id, parent_version) REFERENCES question_plans (run_id, version)
);
