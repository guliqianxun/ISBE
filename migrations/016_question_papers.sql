-- Each paper record of the question runs is stored once. Hits, paper set members and
-- events held a full copy of the record (about four per paper); they now hold its ref,
-- and the readers put the record back, so what they return does not change. The raw
-- essaies replies are compressed. The isbe_* functions are registered by `db` from
-- `core.paperrefs`.

-- A paper record (common#paper) under its ref, a digest of its content. Rows are only
-- ever added. Writer: question.papers
CREATE TABLE question_papers (
  ref   TEXT PRIMARY KEY,
  paper TEXT NOT NULL
);

INSERT OR IGNORE INTO question_papers (ref, paper)
  SELECT isbe_paper_ref(paper), isbe_paper_json(paper) FROM question_hits;
INSERT OR IGNORE INTO question_papers (ref, paper)
  SELECT isbe_paper_ref(json_extract(m.value, '$.paper')), isbe_paper_json(json_extract(m.value, '$.paper'))
  FROM paper_sets, json_each(paper_sets.doc, '$.members') AS m
  WHERE json_type(m.value, '$.paper') = 'object';
INSERT OR IGNORE INTO question_papers (ref, paper)
  SELECT isbe_paper_ref(json_extract(data, '$.paper')), isbe_paper_json(json_extract(data, '$.paper'))
  FROM question_events WHERE json_type(data, '$.paper') = 'object';

-- One paper a query returned, at its first rank. Writer: question.search
CREATE TABLE question_hits_016 (
  run_id    TEXT NOT NULL,
  search    INTEGER NOT NULL,
  query_id  TEXT NOT NULL,
  rank      INTEGER NOT NULL CHECK (rank >= 1),
  key       TEXT NOT NULL,                     -- the paper's preferred identity key
  new       INTEGER NOT NULL CHECK (new IN (0, 1)),  -- 0: the search already had this key
  paper_ref TEXT NOT NULL REFERENCES question_papers (ref),
  PRIMARY KEY (run_id, search, query_id, rank),
  FOREIGN KEY (run_id, search, query_id) REFERENCES question_queries (run_id, search, query_id)
);
INSERT INTO question_hits_016 (run_id, search, query_id, rank, key, new, paper_ref)
  SELECT run_id, search, query_id, rank, key, new, isbe_paper_ref(paper) FROM question_hits ORDER BY rowid;
DROP TABLE question_hits;
ALTER TABLE question_hits_016 RENAME TO question_hits;

-- A member's `paper` becomes `paper_ref`; an event's `paper` (hit, expand) the same.
UPDATE paper_sets SET doc = isbe_slim_members(doc);
UPDATE question_events SET data = isbe_slim_event(data) WHERE json_type(data, '$.paper') = 'object';

-- essaies' JSON body(ies) as returned, zlib-compressed; `response` stays NULL.
ALTER TABLE question_queries ADD COLUMN response_z BLOB;
UPDATE question_queries SET response_z = isbe_deflate(response), response = NULL WHERE response IS NOT NULL;
