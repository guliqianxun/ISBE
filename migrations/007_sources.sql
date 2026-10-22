-- Configured news sources and their collected leads are independent of LLM triage.
CREATE TABLE source_definitions (
  id TEXT PRIMARY KEY,
  config TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  origin TEXT NOT NULL CHECK (origin IN ('manual','discovered')),
  discovered_from TEXT,
  last_checked TEXT,
  last_status TEXT,
  last_error TEXT
);
CREATE TABLE source_fetches (
  source_id TEXT NOT NULL REFERENCES source_definitions(id),
  hash TEXT NOT NULL,
  response_url TEXT NOT NULL,
  content_type TEXT NOT NULL,
  body BLOB NOT NULL,
  collected_at TEXT NOT NULL,
  PRIMARY KEY (source_id, hash)
);
CREATE TABLE source_materials (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source_definitions(id),
  url TEXT NOT NULL,
  current_hash TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  UNIQUE (source_id,url)
);
CREATE TABLE source_material_versions (
  material_id TEXT NOT NULL REFERENCES source_materials(id),
  hash TEXT NOT NULL,
  content TEXT NOT NULL,
  fetch_hash TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  PRIMARY KEY (material_id,hash)
);
CREATE TABLE source_jobs (
  id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  operation TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('running','succeeded','partial','failed','interrupted')),
  created_at TEXT NOT NULL,
  finished_at TEXT,
  stats TEXT NOT NULL DEFAULT '{}',
  error TEXT
);
CREATE INDEX source_materials_recent ON source_materials(first_seen DESC,id);
