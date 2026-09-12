# Local schema and data dictionary

Generated from the four numbered migrations against a disposable SQLite database. Production defaults to `~/Library/Application Support/ApplyPilot/applypilot.sqlite3`. All times are Unix seconds; identities/hashes are text. JSON payloads preserve unknown fields. Credentials are Keychain references only.

Profile versions and events are immutable. Terminal application states cannot revert. Uncertain submissions retain a unique reservation. Foreign keys are enabled on every connection. Cache tables are disposable; all other tables are authoritative or evidence.

## Public question catalogue

`data/preset_questions.json` is a versioned, tracked schema containing blank
question definitions. Every entry has a unique `id`, `category`, exact
`match_terms`, user-facing `prompt`, `answer_type`, `profile_path`, `reuse`,
`ask_policy` and Boolean `setup_required`. Reusable choice definitions also list
their allowed `choices`. `review_reason` explains why exact wording or user review
is needed. The loader rejects duplicate IDs, unknown policies, invalid types,
application-specific profile paths and any tracked `answer` or `default_answer`.

Candidate values are stored only inside immutable local `profile_versions` or
application-scoped `answer_versions`. They are not part of this public schema.

## Tables

### account_locks

| Column | Type | Required | Key/default |
|---|---|---|---|
| account | TEXT | Yes | PK |
| application_id | TEXT | Yes |  |
| owner | TEXT | Yes |  |
| fence | INTEGER | Yes |  |

References: `application_id` → `applications.id`.

### discovery_runs

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| query | TEXT | Yes |  |
| location | TEXT | Yes |  |
| requested | INTEGER | Yes | 1–100 |
| sources | TEXT | Yes | JSON |
| created | REAL | Yes |  |

### discovery_results

| Column | Type | Required | Key/default |
|---|---|---|---|
| run_id | TEXT | Yes | PK |
| ordinal | INTEGER | Yes | Unique within run |
| identity | TEXT | Yes | PK |
| payload | TEXT | Yes | JSON |

References: `run_id` → `discovery_runs.id`.

### source_roots

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| provider | TEXT | Yes | bright_network/workday/allhires/apply4law |
| name | TEXT | Yes |  |
| url | TEXT | Yes | Unique |
| enabled | INTEGER | Yes | 1 |
| created | REAL | Yes |  |

### linkedin_batches

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| run_id | TEXT | No |  |
| query | TEXT | Yes |  |
| location | TEXT | Yes |  |
| requested | INTEGER | Yes | 1–10 |
| attempt_limit | INTEGER | Yes | 1–30 |
| profile_version | TEXT | Yes |  |
| documents | TEXT | Yes | JSON |
| status | TEXT | Yes | Bounded lifecycle |
| context_id | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `run_id` → `runs.id`, `profile_version` → `profile_versions.id`.

### linkedin_candidates

| Column | Type | Required | Key/default |
|---|---|---|---|
| batch_id | TEXT | Yes | PK |
| ordinal | INTEGER | Yes | Unique within batch |
| identity | TEXT | Yes | PK |
| url | TEXT | Yes |  |
| employer | TEXT | Yes | '' |
| role | TEXT | Yes | '' |
| status | TEXT | Yes | QUEUED/PREPARING/PREPARED/SKIPPED/BLOCKED/UNSUPPORTED/CANCELLED |
| application_id | TEXT | No |  |
| detail | TEXT | Yes | '' |

References: `batch_id` → `linkedin_batches.id`, `application_id` → `applications.id`.

### account_policies

| Column | Type | Required | Key/default |
|---|---|---|---|
| employer | TEXT | Yes | PK |
| origin | TEXT | Yes | PK |
| email | TEXT | Yes | PK |
| mailbox | TEXT | Yes |  |
| rules | TEXT | Yes |  |

### adapter_qualifications

| Column | Type | Required | Key/default |
|---|---|---|---|
| adapter | TEXT | Yes | PK |
| workflow | TEXT | Yes | PK |
| level | TEXT | Yes | PK |
| evidence | TEXT | Yes |  |

### answer_versions

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| application_id | TEXT | No |  |
| profile_version | TEXT | Yes |  |
| question | TEXT | Yes |  |
| scope | TEXT | Yes |  |
| answer | TEXT | Yes |  |
| approved | INTEGER | Yes |  |
| created | REAL | Yes |  |

References: `profile_version` → `profile_versions.id`, `application_id` → `applications.id`.

### application_documents

| Column | Type | Required | Key/default |
|---|---|---|---|
| application_id | TEXT | Yes | PK |
| document_id | TEXT | Yes | PK |

References: `document_id` → `documents.id`, `application_id` → `applications.id`.

### applications

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| vacancy_id | TEXT | Yes |  |
| profile_id | TEXT | Yes |  |
| account | TEXT | Yes |  |
| state | TEXT | Yes |  |
| owner | TEXT | No |  |
| fence | INTEGER | Yes | 0 |
| lease_until | REAL | No |  |
| updated | REAL | Yes |  |
| detail | TEXT | Yes | '' |

References: `profile_id` → `profiles.id`, `vacancy_id` → `vacancies.id`.

### batches

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| run_id | TEXT | Yes |  |
| requested | INTEGER | Yes |  |

References: `run_id` → `runs.id`.

### cache_entries

| Column | Type | Required | Key/default |
|---|---|---|---|
| key | TEXT | Yes | PK |
| kind | TEXT | Yes |  |
| payload | TEXT | Yes |  |
| expires | REAL | Yes |  |
| size | INTEGER | Yes |  |
| accessed | REAL | Yes |  |

### checkpoints

| Column | Type | Required | Key/default |
|---|---|---|---|
| application_id | TEXT | Yes | PK |
| payload | TEXT | Yes |  |
| updated | REAL | Yes |  |

References: `application_id` → `applications.id`.

### documents

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| kind | TEXT | Yes |  |
| path | TEXT | Yes |  |
| sha256 | TEXT | Yes |  |
| version | INTEGER | Yes |  |
| approved | INTEGER | Yes |  |
| profile_id | TEXT | No |  |
| applicability | TEXT | Yes |  |
| mime | TEXT | Yes |  |
| size | INTEGER | Yes |  |
| created | REAL | Yes |  |

References: `profile_id` → `profiles.id`.

### email_transactions

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| application_id | TEXT | Yes |  |
| account_id | TEXT | No |  |
| mailbox | TEXT | Yes |  |
| recipient | TEXT | Yes |  |
| tenant | TEXT | Yes |  |
| action | TEXT | Yes |  |
| requested | REAL | Yes |  |
| expires | REAL | Yes |  |
| rules | TEXT | Yes |  |
| status | TEXT | Yes |  |
| message_id | TEXT | No |  |

References: `account_id` → `portal_accounts.id`, `application_id` → `applications.id`.

### employers

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| name | TEXT | Yes |  |
| tenant | TEXT | Yes |  |

### events

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | INTEGER | Yes | PK |
| application_id | TEXT | No |  |
| kind | TEXT | Yes |  |
| payload | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `application_id` → `applications.id`.

### field_manifests

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| application_id | TEXT | Yes |  |
| payload | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `application_id` → `applications.id`.

### grants

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| run_id | TEXT | Yes |  |
| expires | REAL | Yes |  |
| revoked | INTEGER | Yes | 0 |
| provenance | TEXT | Yes |  |

References: `run_id` → `runs.id`.

### legacy_imports

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| source_hash | TEXT | Yes |  |
| payload | TEXT | Yes |  |
| created | REAL | Yes |  |

### mail_connections

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| email | TEXT | Yes |  |
| scopes | TEXT | Yes |  |
| secret_ref | TEXT | Yes |  |
| status | TEXT | Yes |  |

### missing_questions

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| application_id | TEXT | Yes |  |
| question_key | TEXT | Yes |  |
| payload | TEXT | Yes |  |
| resolved | INTEGER | Yes | 0 |

References: `application_id` → `applications.id`.

### portal_accounts

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| employer | TEXT | Yes |  |
| origin | TEXT | Yes |  |
| email | TEXT | Yes |  |
| secret_ref | TEXT | Yes |  |
| status | TEXT | Yes |  |

### profile_records

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| version_id | TEXT | Yes |  |
| kind | TEXT | Yes |  |
| record_key | TEXT | Yes |  |
| payload | TEXT | Yes |  |

References: `version_id` → `profile_versions.id`.

### profile_versions

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| profile_id | TEXT | Yes |  |
| parent_id | TEXT | No |  |
| payload | TEXT | Yes |  |
| provenance | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `parent_id` → `profile_versions.id`, `profile_id` → `profiles.id`.

### profiles

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| created | REAL | Yes |  |

### receipts

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| attempt_id | TEXT | No |  |
| application_id | TEXT | Yes |  |
| source | TEXT | Yes |  |
| reference | TEXT | Yes |  |
| payload | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `application_id` → `applications.id`, `attempt_id` → `submission_attempts.id`.

### reservations

| Column | Type | Required | Key/default |
|---|---|---|---|
| slot | INTEGER | Yes | PK |
| application_id | TEXT | Yes |  |
| owner | TEXT | Yes |  |
| fence | INTEGER | Yes |  |
| expires | REAL | Yes |  |

References: `application_id` → `applications.id`.

### run_applications

| Column | Type | Required | Key/default |
|---|---|---|---|
| run_id | TEXT | Yes | PK |
| application_id | TEXT | Yes | PK |
| profile_version | TEXT | Yes |  |
| target | TEXT | Yes |  |

References: `profile_version` → `profile_versions.id`, `application_id` → `applications.id`, `run_id` → `runs.id`.

### runs

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| plan | TEXT | Yes |  |
| status | TEXT | Yes |  |
| created | REAL | Yes |  |

### schema_migrations

| Column | Type | Required | Key/default |
|---|---|---|---|
| version | TEXT | Yes | PK |
| sha256 | TEXT | Yes |  |
| applied | REAL | Yes |  |

### sessions

| Column | Type | Required | Key/default |
|---|---|---|---|
| application_id | TEXT | Yes | PK |
| tab_id | TEXT | Yes |  |
| context_id | TEXT | Yes |  |
| retained | INTEGER | Yes | 1 |

References: `application_id` → `applications.id`.

### submission_attempts

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| application_id | TEXT | Yes |  |
| run_id | TEXT | Yes |  |
| state | TEXT | Yes |  |
| snapshot | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `run_id` → `runs.id`, `application_id` → `applications.id`.

### task_dependencies

| Column | Type | Required | Key/default |
|---|---|---|---|
| task_id | TEXT | Yes | PK |
| depends_on | TEXT | Yes | PK |

References: `depends_on` → `tasks.id`, `task_id` → `tasks.id`.

### tasks

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| run_id | TEXT | Yes |  |
| application_id | TEXT | Yes |  |
| kind | TEXT | Yes |  |
| status | TEXT | Yes |  |
| attempts | INTEGER | Yes | 0 |
| not_before | REAL | Yes | 0 |

References: `application_id` → `applications.id`, `run_id` → `runs.id`.

### upload_evidence

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| application_id | TEXT | Yes |  |
| document_id | TEXT | Yes |  |
| evidence | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `document_id` → `documents.id`, `application_id` → `applications.id`.

### vacancies

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| employer_id | TEXT | Yes |  |
| role | TEXT | Yes |  |
| identity | TEXT | Yes |  |
| url | TEXT | Yes |  |
| cycle | TEXT | No |  |
| eligibility | TEXT | Yes | 'unknown' |
| metadata | TEXT | Yes | '{}' |

References: `employer_id` → `employers.id`.

### vacancy_sources

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| vacancy_id | TEXT | No |  |
| url | TEXT | Yes |  |
| checked | REAL | Yes |  |
| evidence | TEXT | Yes |  |

References: `vacancy_id` → `vacancies.id`.

### validations

| Column | Type | Required | Key/default |
|---|---|---|---|
| id | TEXT | Yes | PK |
| application_id | TEXT | Yes |  |
| snapshot | TEXT | Yes |  |
| created | REAL | Yes |  |

References: `application_id` → `applications.id`.

## Constraints and migration policy

Exact CHECK constraints, indexes and triggers are in the numbered files under `src/pilot/migrations`, including `004_function_workflows.sql`. Each migration is transactional and checksum tracked; altered installed migrations fail rather than silently changing history. Use a verified consistent backup to roll back data/schema together; automatic destructive down-migrations are not provided. See UPGRADE.md for commands.
