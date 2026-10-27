-- The screening stage of question runs: the model's verdict on every candidate and
-- people's overrides. Rows are only ever added; an override is undone by a new row
-- that revokes it.

-- `screen_verdict@1` per paper and screening. A run screened again (new criteria, or
-- the same ones) numbers its screenings 1, 2, …; a paper's latest verdict is the one of
-- the highest screening that judged it. Writer: question.screen
CREATE TABLE screen_verdicts (
  run_id       TEXT NOT NULL REFERENCES question_runs (run_id),
  screen       INTEGER NOT NULL CHECK (screen >= 1),
  key          TEXT NOT NULL,                 -- the paper's preferred identity key
  decision     TEXT NOT NULL CHECK (decision IN ('include', 'exclude', 'unscreened')),
  batch_id     TEXT NOT NULL,
  criteria_sha TEXT NOT NULL,                 -- prompt, criteria and model; a verdict under the same hash is reused
  cached       INTEGER NOT NULL CHECK (cached IN (0, 1)),  -- 1: copied from an earlier screening
  doc          TEXT NOT NULL,                 -- the screen_verdict@1 document
  created_at   TEXT NOT NULL,
  PRIMARY KEY (run_id, screen, key)
);

CREATE INDEX screen_verdicts_reuse ON screen_verdicts (run_id, criteria_sha, key);

-- `screen_override@1`: a person's decision on a paper, or the revert of an earlier one.
-- Writer: question.screen
CREATE TABLE screen_overrides (
  run_id      TEXT NOT NULL REFERENCES question_runs (run_id),
  override_id TEXT NOT NULL,
  position    INTEGER NOT NULL CHECK (position >= 1),
  key         TEXT NOT NULL,
  decision    TEXT CHECK (decision IN ('include', 'exclude')),  -- NULL: revokes an earlier override
  revokes     TEXT,
  doc         TEXT NOT NULL,                  -- the screen_override@1 document
  created_at  TEXT NOT NULL,
  PRIMARY KEY (run_id, override_id),
  UNIQUE (run_id, position),
  CHECK ((decision IS NULL) = (revokes IS NOT NULL))
);
