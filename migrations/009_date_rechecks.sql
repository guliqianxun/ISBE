-- Each bounded date-recheck batch shares the durable job and frozen search-plan ledger.
CREATE TABLE search_date_checks (
  run_id TEXT NOT NULL REFERENCES search_runs(id),
  material_id TEXT NOT NULL REFERENCES search_materials(id),
  previous_content TEXT NOT NULL,
  result TEXT,
  checked_at TEXT,
  retained INTEGER NOT NULL DEFAULT 0 CHECK (retained IN (0,1)),
  PRIMARY KEY (run_id,material_id)
);
CREATE INDEX search_date_checks_material ON search_date_checks(material_id,checked_at);
