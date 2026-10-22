-- Successful original article reads survive service restarts and source outages.
CREATE TABLE article_text (
  url TEXT PRIMARY KEY NOT NULL,
  content TEXT NOT NULL CHECK (length(content) <= 1048576)
);
