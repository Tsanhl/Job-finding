CREATE INDEX recruitment_review_page ON recruitment_messages(resolved,received DESC,id DESC) WHERE classification NOT IN ('OTHER','UNAVAILABLE');
