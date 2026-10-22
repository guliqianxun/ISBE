-- Preserve existing reports while admitting fixed-source research reports.
CREATE TABLE reports_research (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    period TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('weekly', 'monthly', 'daily', 'research')),
    path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (topic_id, period, kind)
);
INSERT INTO reports_research SELECT * FROM reports;
DROP TABLE reports;
ALTER TABLE reports_research RENAME TO reports;
