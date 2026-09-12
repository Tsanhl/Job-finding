# Local portal support and acceptance

Running locally avoids hosting the candidate database. It does not remove employer website differences or Google's consent requirement. Filling to final review needs neither Gmail OAuth nor employer password retrieval; the applicant signs in and completes verification in the managed browser.

## Implemented provider contracts

AllHires and Apply4Law share an ASP.NET form-action contract. The implementation recognises stable control IDs ending in `btnNext`, `btnSaveContinue` or `btnSaveAndContinue`, and a matching non-final label. The selected action may POST only to its existing form endpoint, with the exact named submitter or event target. An arbitrary caption, unexpected event target or final-action parameter does not grant permission. Compound non-file application values can be saved by that bounded action.

Repeated employment/education sections use stable named section/editor containers, input identities or definition-list summaries. Add/Save actions have distinct recognised IDs. Records match employer/title or institution/degree before adding, preserve conflicting existing entries, require date precision, and verify the saved summary. Workday structured editors share the record value/readback implementation; unfamiliar or multiple ambiguous editors require handoff.

AllHires/Apply4Law submission has a separate final control and requires a matching application ID, account, role, submitted marker and receipt reference. A target can carry `portal_application_id` separately from its vacancy/requisition `identity`, so the candidate-specific draft reference does not weaken vacancy deduplication. Workday retains its separate contract. SubmitService performs the final action only with current authority and qualifying evidence. The generated local CI report records synthetic evidence for all three submission contracts when their tests pass.

These are executable recognition contracts tested with intercepted Chromium fixtures. They are not a claim that these exact IDs were observed in every employer tenant. Different skins, renamed controls, server APIs, cross-origin upload services and unsupported widgets require an adapter update based on the selected form. No live acceptance has been invented.

```bash
.venv-upgrade/bin/python cli.py runtime tabs
.venv-upgrade/bin/python cli.py runtime audit SELECTED_TAB_ID
```

The read-only audit reports recognised capabilities and section counts without returning passwords, input values or application answers. It never clicks a control. A managed browser was not available during the development acceptance check, so no real application form was inspected or modified.

## Document transport

An approved file hash is required before an upload request. Raw file bytes or one-file multipart data with supported CSRF metadata are accepted at a same-origin upload/file/attachment endpoint. Extra application fields, unapproved file contents and unknown destinations are rejected. HTTP upload failures invalidate apparent saved-file evidence. Local PDF/DOCX/TXT extraction is bounded, cached by content hash and parser version, and explicitly unconfirmed; it never promotes parsed text into candidate facts automatically.

## Request and status interface

The deterministic request interpreter proposes workflow, scope and worker count from a bounded instruction and already selected targets. It does not invent employers or issue grants. Review the proposal before setting execution authority. Dashboard progress refreshes every three seconds; Gmail connection progress refreshes every two seconds. Model metrics retain request and token counts without prompts or credentials.

### Supervised Workday account/contact smoke test (September 2026)

Observed a live Workday registration page and a user-completed sign-in. The account
attempt did not establish confirmed registration; it remains a reconciliation
handoff and must not be replayed automatically. No live submission is qualified
by this test. Contact filling exposed button-based listboxes, selected-item phone
country chips, required radio legends, and phone-extension misclassification;
these shapes now have isolated Chromium regression coverage.

New registrations require the applicant's chosen password in the portal. The
applicant creates the account, enters the password and completes any email
verification directly in the employer site or email client. ApplyPilot does not
read, generate, store or submit employer passwords. Chat history cannot be erased
by deleting a local secret, so the dashboard directs applicants to secure portal
entry instead. Account and verification pages park the application and release
its worker; Resume continues from the retained form after the applicant signs in.
