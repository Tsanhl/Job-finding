CREATE TABLE search_history(id TEXT PRIMARY KEY, job_id TEXT REFERENCES workspace_jobs(id), provider TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)), created REAL NOT NULL);
CREATE INDEX search_history_recent ON search_history(created DESC);
CREATE TABLE saved_context(id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('search','autofill','note')), content TEXT NOT NULL, provenance TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX saved_context_recent ON saved_context(created DESC);
