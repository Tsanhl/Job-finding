CREATE TABLE source_roots(
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK(provider IN ('bright_network','workday','allhires','apply4law')),
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created REAL NOT NULL
);
CREATE TABLE discovery_runs(
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    location TEXT NOT NULL,
    requested INTEGER NOT NULL CHECK(requested BETWEEN 1 AND 100),
    sources TEXT NOT NULL CHECK(json_valid(sources)),
    created REAL NOT NULL
);
CREATE TABLE discovery_results(
    run_id TEXT NOT NULL REFERENCES discovery_runs(id),
    ordinal INTEGER NOT NULL CHECK(ordinal>=0),
    identity TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    PRIMARY KEY(run_id,identity),
    UNIQUE(run_id,ordinal)
);
CREATE INDEX discovery_recent ON discovery_runs(created DESC);
CREATE INDEX source_roots_enabled ON source_roots(enabled,provider);
CREATE TABLE linkedin_batches(
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(id),
    query TEXT NOT NULL,
    location TEXT NOT NULL,
    requested INTEGER NOT NULL CHECK(requested BETWEEN 1 AND 10),
    attempt_limit INTEGER NOT NULL CHECK(attempt_limit BETWEEN 1 AND 30),
    profile_version TEXT NOT NULL REFERENCES profile_versions(id),
    documents TEXT NOT NULL CHECK(json_valid(documents)),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','NEEDS_AUTHENTICATION','CAPACITY_WAIT','DONE','EXHAUSTED','STOPPED')),
    context_id TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE linkedin_candidates(
    batch_id TEXT NOT NULL REFERENCES linkedin_batches(id),
    ordinal INTEGER NOT NULL,
    identity TEXT NOT NULL,
    url TEXT NOT NULL,
    employer TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('QUEUED','PREPARING','PREPARED','SKIPPED','BLOCKED','UNSUPPORTED','CANCELLED')),
    application_id TEXT REFERENCES applications(id),
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(batch_id,identity),
    UNIQUE(batch_id,ordinal)
);
CREATE INDEX linkedin_candidates_ready ON linkedin_candidates(batch_id,status,ordinal);
