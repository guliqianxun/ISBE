-- Search plans discover across websites; each run preserves its query and provider response.
CREATE TABLE search_plans (
  id TEXT PRIMARY KEY,
  config TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  last_checked TEXT,
  last_status TEXT
);
CREATE TABLE search_runs (
  id TEXT PRIMARY KEY REFERENCES source_jobs(id),
  plan_id TEXT NOT NULL REFERENCES search_plans(id),
  config TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE search_requests (
  run_id TEXT NOT NULL REFERENCES search_runs(id),
  query_index INTEGER NOT NULL,
  query TEXT NOT NULL,
  raw BLOB,
  state TEXT NOT NULL,
  error TEXT,
  PRIMARY KEY (run_id,query_index)
);
CREATE TABLE search_hits (
  run_id TEXT NOT NULL,
  query_index INTEGER NOT NULL,
  rank INTEGER NOT NULL,
  material_id TEXT NOT NULL,
  content TEXT NOT NULL,
  PRIMARY KEY (run_id,query_index,rank),
  FOREIGN KEY (run_id,query_index) REFERENCES search_requests(run_id,query_index)
);
CREATE INDEX search_hits_material ON search_hits(material_id);
CREATE TABLE search_materials (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES search_plans(id),
  url TEXT NOT NULL,
  content TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  UNIQUE (plan_id,url)
);
CREATE INDEX search_materials_recent ON search_materials(first_seen DESC,id);
CREATE TABLE search_daily_usage (
  day TEXT PRIMARY KEY,
  requests INTEGER NOT NULL CHECK (requests >= 0)
);
