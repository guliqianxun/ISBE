-- The paper's method figure, saved under figures/. Writer: enrich
-- JSON {"file", "caption"}; '{}' when the paper has none; NULL when not looked for yet.
ALTER TABLE fulltext ADD COLUMN figure TEXT;
