-- Schema v1. Every table has exactly one writer module (see docs/DESIGN.md §4).

-- Everything any source found. Writer: sources
CREATE TABLE items (
  id           TEXT PRIMARY KEY,           -- arxiv:2609.01234 | rss:<url> | gh:owner/repo | zhihu:<id>
  kind         TEXT NOT NULL CHECK (kind IN ('paper', 'article', 'repo')),
  source       TEXT NOT NULL,              -- essaies | arxiv | rss:<feed> | github | zhihu | exec:<name>
  title        TEXT NOT NULL,
  summary      TEXT,
  url          TEXT NOT NULL,
  authors      TEXT,                       -- JSON array
  lang         TEXT NOT NULL,
  published_at TEXT NOT NULL,              -- RFC 3339
  extra        TEXT NOT NULL DEFAULT '{}'  -- JSON: categories, hf_upvotes, citations, stars
);
CREATE INDEX items_published_at ON items (published_at);

-- Which topic pulled which item. Writer: sources
CREATE TABLE topic_items (
  topic_id   TEXT NOT NULL,
  item_id    TEXT NOT NULL REFERENCES items (id),
  first_seen TEXT NOT NULL,
  PRIMARY KEY (topic_id, item_id)
);
CREATE INDEX topic_items_first_seen ON topic_items (topic_id, first_seen);

-- Relevance verdicts, cached because the judge is an LLM call. Writer: triage
-- A changed retrieval block changes contract_sha, which forces a re-judge.
CREATE TABLE triage (
  topic_id     TEXT NOT NULL,
  item_id      TEXT NOT NULL REFERENCES items (id),
  contract_sha TEXT NOT NULL,
  verdict      TEXT NOT NULL CHECK (verdict IN ('kept', 'judge_dropped', 'gate_dropped')),
  tier         TEXT CHECK (tier IN ('core', 'secondary')),
  reason       TEXT NOT NULL,
  judged_at    TEXT NOT NULL,
  PRIMARY KEY (topic_id, item_id)
);

-- Full text of kept papers only. Writer: enrich
CREATE TABLE fulltext (
  item_id      TEXT PRIMARY KEY REFERENCES items (id),
  via          TEXT NOT NULL CHECK (via IN ('metrail', 'arxiv-html')),
  body         TEXT NOT NULL,
  extracted_at TEXT NOT NULL
);

-- Report index; the body is the markdown file at `path`. Writer: digest
CREATE TABLE reports (
  id          TEXT PRIMARY KEY,
  topic_id    TEXT NOT NULL,
  period      TEXT NOT NULL,                -- 2026-W41 | 2026-10 | 2026-10-12
  kind        TEXT NOT NULL CHECK (kind IN ('weekly', 'monthly', 'daily')),
  path        TEXT NOT NULL,
  fingerprint TEXT NOT NULL,                -- JSON: model, memory revisions, contract sha, item ids
  created_at  TEXT NOT NULL,
  UNIQUE (topic_id, period, kind)
);

-- The ledger. Writer: the job wrapper only
CREATE TABLE runs (
  id          TEXT PRIMARY KEY,
  job         TEXT NOT NULL,                -- collect | triage | enrich | digest | notify
  topic_id    TEXT,
  status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  stats       TEXT NOT NULL DEFAULT '{}',
  error       TEXT
);
CREATE INDEX runs_topic_started ON runs (topic_id, started_at);

-- Every LLM call, replayable. Writer: llm
CREATE TABLE llm_calls (
  id         TEXT PRIMARY KEY,
  run_id     TEXT REFERENCES runs (id),
  purpose    TEXT NOT NULL,                 -- judge | digest | cards | reviewer | extract
  model      TEXT NOT NULL,
  prompt_sha TEXT NOT NULL,
  prompt     TEXT NOT NULL,
  response   TEXT NOT NULL,
  tokens_in  INTEGER,
  tokens_out INTEGER,
  ms         INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX llm_calls_run ON llm_calls (run_id);
