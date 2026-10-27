-- The knowledge graph of each question run (#51, question_graph@1): the latest
-- build only, compressed JSON without the paper records (the run's candidates).
-- Writer: question.graph
CREATE TABLE question_graphs (
  run_id      TEXT PRIMARY KEY REFERENCES question_runs (run_id),
  fingerprint TEXT NOT NULL,
  built_at    TEXT NOT NULL,
  nodes       INTEGER NOT NULL,
  edges       INTEGER NOT NULL,
  communities INTEGER NOT NULL,
  doc         BLOB NOT NULL
);
