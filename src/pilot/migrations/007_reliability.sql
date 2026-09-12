CREATE INDEX opportunities_saved_recent ON opportunity_index(saved,checked DESC,identity);
CREATE TABLE assessment_keys(application_id TEXT NOT NULL REFERENCES applications(id), component_key TEXT NOT NULL, assessment_id TEXT NOT NULL REFERENCES assessments(id), PRIMARY KEY(application_id,component_key));
CREATE TABLE assessment_evidence(assessment_id TEXT NOT NULL REFERENCES assessments(id), evidence_id TEXT NOT NULL, deadline TEXT NOT NULL DEFAULT '', created REAL NOT NULL, PRIMARY KEY(assessment_id,evidence_id));
INSERT INTO assessment_evidence SELECT id,evidence_id,deadline,created FROM assessments WHERE evidence_id IS NOT NULL;
ALTER TABLE mail_tracking ADD COLUMN last_page_success REAL;
