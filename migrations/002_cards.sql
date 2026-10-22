-- Facts extracted from a paper's full text, each with a quote checked against
-- that text. Writer: enrich
-- `key` hashes the extraction prompt and the full text: a change to either rebuilds the card.
CREATE TABLE cards (
  item_id    TEXT NOT NULL REFERENCES items (id),
  lang       TEXT NOT NULL CHECK (lang IN ('en', 'zh')),  -- language of the values
  key        TEXT NOT NULL,
  card       TEXT NOT NULL,  -- JSON: {"fields": {name: {"value", "quote"}}, "rejected": [name]}
  created_at TEXT NOT NULL,
  PRIMARY KEY (item_id, lang)
);
