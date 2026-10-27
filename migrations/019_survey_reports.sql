-- Preserve existing reports while admitting survey reports of question runs
-- (`question.survey`): topic_id is the run id. Writer: question.survey
CREATE TABLE reports_survey (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    period TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('weekly', 'monthly', 'daily', 'research', 'survey')),
    path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (topic_id, period, kind)
);
INSERT INTO reports_survey SELECT * FROM reports;
DROP TABLE reports;
ALTER TABLE reports_survey RENAME TO reports;
