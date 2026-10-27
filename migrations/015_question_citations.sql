-- The citations around a question run's papers: what essaies says each candidate cites
-- and is cited by. The run's citation edges are read from these rows (both papers must
-- be candidates), so papers added later still meet edges fetched earlier. A paper is
-- looked up once per scope; rows are only ever added.

-- One paper's lookup. `scope` archive: essaies' batch over its own archive (fast, no
-- OpenAlex call); all: one full lookup through OpenAlex (references and the first page
-- of citing works). Writer: question.citations
CREATE TABLE question_citations (
  run_id     TEXT NOT NULL REFERENCES question_runs (run_id),
  key        TEXT NOT NULL,                     -- the candidate's preferred identity key
  scope      TEXT NOT NULL CHECK (scope IN ('archive', 'all')),
  found      INTEGER NOT NULL CHECK (found IN (0, 1)),  -- 0: essaies does not know the paper
  cites      TEXT NOT NULL,                     -- JSON: the works it cites, as paper records
  cited_by   TEXT NOT NULL,                     -- JSON: the works citing it, the same way
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (run_id, key, scope)
);
