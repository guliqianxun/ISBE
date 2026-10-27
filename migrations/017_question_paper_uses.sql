-- Which runs refer to each paper record (by a hit, a set member or an event), so
-- deleting a run removes the records only it used without scanning every run.
-- Writer: question.papers
CREATE TABLE question_paper_uses (
  run_id TEXT NOT NULL REFERENCES question_runs (run_id),
  ref    TEXT NOT NULL REFERENCES question_papers (ref),
  PRIMARY KEY (run_id, ref)
) WITHOUT ROWID;
CREATE INDEX question_paper_uses_ref ON question_paper_uses (ref);
-- Removing a record checks that no hit refers to it (the foreign key): without
-- this index each removal scans every hit of the workspace.
CREATE INDEX question_hits_paper_ref ON question_hits (paper_ref);

INSERT OR IGNORE INTO question_paper_uses (run_id, ref)
  SELECT run_id, paper_ref FROM question_hits
  UNION SELECT run_id, m.value ->> '$.paper_ref' FROM paper_sets, json_each(paper_sets.doc, '$.members') AS m
    WHERE m.value ->> '$.paper_ref' IS NOT NULL
  UNION SELECT run_id, data ->> '$.paper_ref' FROM question_events WHERE data ->> '$.paper_ref' IS NOT NULL;

-- Records no run uses any more (left by run deletes before this migration).
DELETE FROM question_papers WHERE ref NOT IN (SELECT ref FROM question_paper_uses);
