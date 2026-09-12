CREATE TRIGGER protect_completed_application BEFORE UPDATE OF state ON applications
WHEN OLD.state IN ('SUBMITTED_CONFIRMED','SUBMITTED_USER_REPORTED') AND NEW.state NOT IN ('SUBMITTED_CONFIRMED','SUBMITTED_USER_REPORTED')
BEGIN SELECT RAISE(ABORT,'completed application cannot resume'); END;
CREATE TRIGGER protect_uncertain_submission BEFORE UPDATE OF state ON applications
WHEN OLD.state IN ('SUBMITTING','SUBMISSION_UNCONFIRMED') AND NEW.state NOT IN ('SUBMITTING','SUBMISSION_UNCONFIRMED','SUBMITTED_CONFIRMED','SUBMITTED_USER_REPORTED')
BEGIN SELECT RAISE(ABORT,'uncertain transmission requires receipt reconciliation'); END;
CREATE INDEX application_status ON applications(state,updated);
CREATE INDEX app_events ON events(application_id,id);
CREATE INDEX app_validation ON validations(application_id,created);
CREATE INDEX account_mail_pending ON email_transactions(status,expires,application_id);
