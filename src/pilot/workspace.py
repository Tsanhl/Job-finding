"""One owner's durable local records, shared by the dashboard and Codex tools."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .store import digest, encode, uid
from .job_presentation import expired, requirement_sections


def public_url(value):
    parsed = urlsplit(str(value))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use a public HTTPS job link")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a public employer link")
    return str(value)


def canonical(value):
    p = urlsplit(value)
    query = [
        (k, v) for k, v in parse_qsl(p.query) if not k.startswith(("utm_", "iref_"))
    ]
    return urlunsplit(
        (p.scheme, p.netloc.lower(), p.path.rstrip("/"), urlencode(query), "")
    )


def no_secrets(value):
    if isinstance(value, dict):
        for k, v in value.items():
            if any(
                x in k.casefold()
                for x in (
                    "password",
                    "refresh_token",
                    "access_token",
                    "client_secret",
                    "api_key",
                )
            ):
                raise ValueError(
                    "Passwords and credentials cannot be saved in your profile"
                )
            no_secrets(v)
    elif isinstance(value, list):
        for v in value:
            no_secrets(v)
    elif isinstance(value, str):
        import re

        if re.search(
            r"(?i)\b(password|access.token|refresh.token|api.key|client.secret)\s*[:=]\s*\S+|\b(?:sk-|ghp_)[A-Za-z0-9_-]{20,}",
            value,
        ):
            raise ValueError("Credentials cannot be stored as reusable information")


class AssessmentIdentityError(ValueError):
    pass


class Workspace:
    def __init__(self, store):
        self.store = store

    def setting(self, key, default=None):
        row = self.store.one(
            "SELECT payload FROM workspace_settings WHERE key=?", (key,)
        )
        return json.loads(row["payload"]) if row else default

    def set_setting(self, key, value):
        self.store.db.execute(
            "INSERT INTO workspace_settings VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
            (key, encode(value), time.time()),
        )

    def profile(self):
        version = self.setting("active_profile")
        if not version:
            profiles = self.store.rows(
                "SELECT profile_id,MAX(created) AS latest FROM profile_versions GROUP BY profile_id"
            )
            if len(profiles) > 1:
                return {"version": "", "payload": {}, "selection_required": True}
            row = self.store.one(
                "SELECT id FROM profile_versions ORDER BY created DESC LIMIT 1"
            )
            version = (
                row["id"]
                if row
                else self.store.profile({}, provenance="empty local owner")
            )
            self.set_setting("active_profile", version)
        return {
            "version": version,
            "payload": self.store.load_profile(version),
            "selection_required": False,
        }

    def select_profile(self, version):
        self.store.load_profile(version)
        if self.setting("active_profile") and self.setting("active_profile") != version:
            raise ValueError(
                "This workspace already has an owner; use a separate workspace for another person"
            )
        self.set_setting("active_profile", version)
        return self.profile()

    def owner(self):
        profile = self.profile()
        if not profile["version"]:
            raise ValueError("Select your existing profile in My Information first")
        return self.store.one(
            "SELECT profile_id FROM profile_versions WHERE id=?", (profile["version"],)
        )["profile_id"]

    def save_profile(self, payload, parent):
        if not isinstance(payload, dict) or len(encode(payload)) > 500_000:
            raise ValueError("Profile must be an object below 500 KB")
        no_secrets(payload)
        with self.store.tx():
            current = self.profile()
            if not parent or parent != current["version"]:
                raise ValueError(
                    "Profile changed. Reload before saving; your edits were not overwritten."
                )
            version = self.store.profile(
                payload, parent=parent, provenance="user-confirmed local workspace"
            )
            self.set_setting("active_profile", version)
        return self.profile()

    def ingest(self, rows):
        with self.store.tx():
            for job in rows:
                job = dict(job)
                job["url"] = public_url(job["url"])
                identity = job.get("identity") or canonical(job["url"])
                job["identity"] = identity
                self.store.db.execute(
                    "INSERT INTO opportunity_index(identity,payload,first_seen,checked) VALUES(?,?,?,?) ON CONFLICT(identity) DO UPDATE SET payload=excluded.payload,checked=excluded.checked",
                    (identity, encode(job), time.time(), time.time()),
                )

    def opportunities(self, filter="all", *, limit=1000, cursor=None):
        if filter not in {"all", "saved", "opened", "new", "unapplied"}:
            raise ValueError("Unknown opportunity filter")
        if type(limit) is not int or not 1 <= limit <= 1001:
            raise ValueError("Page size must be an integer from 1 to 1000")
        where, args = [], []
        if filter == "saved":
            where.append("saved=1")
        elif filter == "opened":
            where.append("opened IS NOT NULL")
        if cursor is not None:
            if (
                not isinstance(cursor, dict)
                or type(cursor.get("checked")) not in (int, float)
                or not isinstance(cursor.get("identity"), str)
            ):
                raise ValueError("Invalid opportunity cursor")
            where.append("(checked < ? OR (checked = ? AND identity > ?))")
            args.extend([cursor["checked"], cursor["checked"], cursor["identity"]])
        sql = (
            "SELECT * FROM opportunity_index"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY checked DESC,identity ASC"
        )
        result = []
        # Iterate lazily: expiry and application-state filters precede the page limit too.
        for row in self.store.db.execute(sql, args):
            item = {
                **json.loads(row["payload"]),
                **{
                    k: row[k]
                    for k in (
                        "saved",
                        "opened",
                        "application_id",
                        "first_seen",
                        "checked",
                    )
                },
            }
            if expired(item):
                continue
            item["requirement_sections"] = item.get(
                "requirement_sections"
            ) or requirement_sections(item.get("requirements", ""))
            # A previously filled application may not yet have an index binding.
            app = self._find_app(item)
            item["application_id"] = app["id"] if app else item["application_id"]
            item["applied"] = bool(
                app
                and app["state"] in {"SUBMITTED_CONFIRMED", "SUBMITTED_USER_REPORTED"}
            )
            raw = (
                item.get("opening_date")
                or item.get("opens_at")
                or item.get("opening")
                or ""
            )
            try:
                opened = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
                item["newly_opened"] = (
                    0 <= (datetime.now(timezone.utc).date() - opened).days <= 7
                )
            except (TypeError, ValueError):
                item["newly_opened"] = False
            if filter == "saved" and not item["saved"]:
                continue
            if filter == "opened" and not item["opened"]:
                continue
            if filter == "new" and not item["newly_opened"]:
                continue
            if filter == "unapplied" and item["applied"]:
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def opportunity_page(self, filter="all", limit=100, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Page size must be an integer from 1 to 1000")
        rows = self.opportunities(filter, limit=limit + 1, cursor=cursor)
        items = rows[:limit]
        next_cursor = (
            {k: items[-1][k] for k in ("checked", "identity")}
            if len(rows) > limit
            else None
        )
        return {"items": items, "next_cursor": next_cursor}

    def job(self, identity):
        row = self.store.one(
            "SELECT payload FROM opportunity_index WHERE identity=?", (identity,)
        )
        if not row:
            raise ValueError("Unknown opportunity")
        return json.loads(row["payload"])

    def _find_app(self, job):
        owner = self.owner()
        rows = self.store.rows(
            "SELECT a.*,v.identity,v.url FROM applications a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.profile_id=?",
            (owner,),
        )
        matches = [
            r
            for r in rows
            if r["identity"] == job["identity"]
            or canonical(r["url"]) == canonical(job["url"])
        ]
        if len(matches) > 1:
            raise ValueError(
                "Multiple application identities match this link; review the records first"
            )
        return matches[0] if matches else None

    def applied(self, job, applied=None):
        for key in ("employer", "role", "url"):
            if not isinstance(job.get(key), str) or not job[key].strip():
                raise ValueError("Employer, role and portal URL are required")
        public_url(job["url"])
        job = {**job, "identity": job.get("identity") or canonical(job["url"])}
        when = time.time() if applied is None else float(applied)
        if not 0 < when <= time.time() + 86400:
            raise ValueError("Choose a valid application date")
        with self.store.tx():
            owner = self.owner()
            current = self._find_app(job)
            if current:
                app = current["id"]
            else:
                account = self.profile()["payload"].get("email", "")
                employer = digest([job["employer"], account])
                vacancy = digest([job["identity"]])
                app = digest([vacancy, owner])
                self.store.db.execute(
                    "INSERT OR IGNORE INTO employers VALUES(?,?,?)",
                    (employer, job["employer"], account),
                )
                self.store.db.execute(
                    "INSERT OR IGNORE INTO vacancies(id,employer_id,role,identity,url) VALUES(?,?,?,?,?)",
                    (vacancy, employer, job["role"], job["identity"], job["url"]),
                )
                self.store.db.execute(
                    "INSERT INTO applications(id,vacancy_id,profile_id,account,state,updated) VALUES(?,?,?,?,'QUEUED',?)",
                    (app, vacancy, owner, account, time.time()),
                )
            terminal = current and current["state"] in {
                "SUBMITTED_USER_REPORTED",
                "SUBMITTED_CONFIRMED",
            }
            if not terminal:
                self.store.user_submitted(app)
            snapshot = {
                **job,
                "profile_version": self.profile()["version"],
                "documents": [
                    r["document_id"]
                    for r in self.store.rows(
                        "SELECT document_id FROM application_documents WHERE application_id=?",
                        (app,),
                    )
                ],
            }
            self.store.db.execute(
                "INSERT INTO application_history(application_id,snapshot,applied) VALUES(?,?,?) ON CONFLICT(application_id) DO UPDATE SET snapshot=CASE WHEN application_history.snapshot='{}' THEN excluded.snapshot ELSE application_history.snapshot END,applied=COALESCE(application_history.applied,excluded.applied),outcome=CASE WHEN application_history.outcome='Application in progress' THEN 'Awaiting response' ELSE application_history.outcome END",
                (app, encode(snapshot), when),
            )
            self.store.db.execute(
                "UPDATE opportunity_index SET application_id=? WHERE identity=?",
                (app, job["identity"]),
            )
            if not terminal:
                self.event(app, "applied-user-reported", {"applied": when})
        return {
            "application_id": app,
            "state": self.store.one(
                "SELECT state FROM applications WHERE id=?", (app,)
            )["state"],
        }

    def event(self, app, kind, payload):
        self.store.db.execute(
            "INSERT INTO history_events(application_id,kind,payload,created) VALUES(?,?,?,?)",
            (app, kind, encode(payload), time.time()),
        )

    def assert_app(self, app):
        if not self.store.one(
            "SELECT id FROM applications WHERE id=? AND profile_id=?",
            (app, self.owner()),
        ):
            raise ValueError("Application does not belong to this workspace")

    def history(self):
        rows = self.store.rows(
            "SELECT a.*,v.role,v.url,v.identity,e.name AS employer,h.snapshot,h.applied,h.outcome,h.notes,h.revision FROM applications a JOIN vacancies v ON v.id=a.vacancy_id JOIN employers e ON e.id=v.employer_id LEFT JOIN application_history h ON h.application_id=a.id WHERE a.profile_id=? ORDER BY a.updated DESC",
            (self.owner(),),
        )
        for r in rows:
            r["snapshot"] = json.loads(r["snapshot"]) if r["snapshot"] else {}
            r["assessments"] = self.store.rows(
                "SELECT * FROM assessments WHERE application_id=? ORDER BY created",
                (r["id"],),
            )
            for assessment in r["assessments"]:
                assessment["evidence"] = self.store.rows(
                    "SELECT evidence_id,deadline FROM assessment_evidence WHERE assessment_id=? ORDER BY created",
                    (assessment["id"],),
                )
                assessment["deadline_conflict"] = (
                    len(
                        {x["deadline"] for x in assessment["evidence"] if x["deadline"]}
                        | (
                            {assessment["deadline"]}
                            if assessment["deadline"]
                            else set()
                        )
                    )
                    > 1
                )
            r["outcome"] = r["outcome"] or (
                "Awaiting response"
                if r["state"].startswith("SUBMITTED_")
                else "Application in progress"
            )
            r["events"] = self.store.rows(
                "SELECT kind,payload,created FROM history_events WHERE application_id=? ORDER BY id",
                (r["id"],),
            )
        return rows

    def remember_search(self, request, provider, job_id):
        payload = {
            k: request[k]
            for k in (
                "query",
                "location",
                "requested",
                "filters",
                "sources",
                "include_builtin",
                "original_prompt",
            )
            if k in request
        }
        no_secrets(payload)
        if len(encode(payload)) > 20000:
            raise ValueError("Search context is too large")
        self.store.db.execute(
            "INSERT INTO search_history VALUES(?,?,?,?,?)",
            (uid(), job_id, provider, encode(payload), time.time()),
        )

    def search_history(self):
        rows = self.store.rows(
            "SELECT s.*,j.state,j.result FROM search_history s LEFT JOIN workspace_jobs j ON j.id=s.job_id ORDER BY s.created DESC LIMIT 100"
        )
        for row in rows:
            row["payload"] = json.loads(row["payload"])
            result = json.loads(row.pop("result") or "{}")
            row["returned"] = result.get("returned")
        # Preserve earlier searches created before this table existed.
        first = self.store.one("SELECT MIN(created) AS t FROM search_history")["t"]
        for row in self.store.rows(
            "SELECT * FROM discovery_runs ORDER BY created DESC LIMIT 100"
        ):
            if first is None or row["created"] < first:
                rows.append(
                    {
                        "id": row["id"],
                        "provider": "legacy",
                        "created": row["created"],
                        "state": "DONE",
                        "payload": {
                            "query": row["query"],
                            "location": row["location"],
                            "requested": row["requested"],
                        },
                        "returned": self.store.one(
                            "SELECT COUNT(*) AS n FROM discovery_results WHERE run_id=?",
                            (row["id"],),
                        )["n"],
                    }
                )
        return sorted(rows, key=lambda r: r["created"], reverse=True)[:100]

    @staticmethod
    def component_key(component):
        import re

        value = " ".join(str(component).casefold().split())
        # Strip delivery prefixes only; retain test name, round, provider and all other wording.
        return re.sub(r"^(?:(?:re|fw|fwd|reminder)\s*[:–—-]\s*)+", "", value)

    def assessment(
        self, app, component, deadline="", evidence=None, component_key=None
    ):
        self.assert_app(app)
        if not str(component).strip():
            raise ValueError("Assessment name is required")
        key = (
            "explicit:" + str(component_key)
            if component_key
            else "label:" + self.component_key(component)
        )
        with self.store.tx():
            found = self.store.one(
                "SELECT assessment_id FROM assessment_keys WHERE application_id=? AND component_key=?",
                (app, key),
            )
            if not found and not component_key:
                legacy = [
                    r
                    for r in self.store.rows(
                        "SELECT id,component FROM assessments WHERE application_id=?",
                        (app,),
                    )
                    if self.component_key(r["component"])
                    == self.component_key(component)
                ]
                if len(legacy) == 1:
                    found = {"assessment_id": legacy[0]["id"]}
                elif len(legacy) > 1:
                    raise AssessmentIdentityError(
                        "Multiple historical assessment records match; choose an explicit component identity"
                    )
            id = found["assessment_id"] if found else digest([app, key])
            created = (
                self.store.one("SELECT id FROM assessments WHERE id=?", (id,)) is None
            )
            if created:
                self.store.db.execute(
                    "INSERT INTO assessments(id,application_id,component,status,deadline,evidence_id,created) VALUES(?,?,?,'TO_DO',?,?,?)",
                    (id, app, component, deadline, evidence, time.time()),
                )
            self.store.db.execute(
                "INSERT OR IGNORE INTO assessment_keys VALUES(?,?,?)", (app, key, id)
            )
            if evidence:
                self.store.db.execute(
                    "INSERT OR IGNORE INTO assessment_evidence VALUES(?,?,?,?)",
                    (id, evidence, deadline, time.time()),
                )
            if created:
                self.event(app, "assessment-added", {"assessment": id})
        return {"id": id}

    def complete_assessment(self, id, revision):
        row = self.store.one("SELECT * FROM assessments WHERE id=?", (id,))
        if not row:
            raise ValueError("Unknown assessment")
        self.assert_app(row["application_id"])
        if row["status"].startswith("COMPLETED"):
            return {"status": row["status"]}
        if row["revision"] != revision:
            raise ValueError("Assessment changed; reload before updating")
        with self.store.tx():
            self.store.db.execute(
                "UPDATE assessments SET status='COMPLETED_USER_REPORTED',revision=revision+1 WHERE id=?",
                (id,),
            )
            self.event(
                row["application_id"],
                "assessment-completed-user-reported",
                {"assessment": id},
            )
        return {"status": "COMPLETED_USER_REPORTED"}

    def command(self, request):
        op = request["op"]
        if op == "workspace_profile":
            return self.profile()
        if op == "workspace_search_history":
            return self.search_history()
        if op == "workspace_save_context":
            content = str(request.get("content", "")).strip()
            no_secrets(content)
            if (
                not content
                or len(content) > 12000
                or request.get("user_requested_save") is not True
            ):
                raise ValueError("Save only bounded context the user asked to retain")
            import re

            if re.search(
                r"(?i)\b(password|access.token|refresh.token|api.key|client.secret)\s*[:=]",
                content,
            ):
                raise ValueError("Credentials cannot be stored as context")
            id = uid()
            self.store.db.execute(
                "INSERT INTO saved_context VALUES(?,?,?,?,?)",
                (
                    id,
                    request.get("kind", "note"),
                    content,
                    "user-provided local context; not an instruction grant",
                    time.time(),
                ),
            )
            return {"id": id}
        if op == "workspace_context":
            query = str(request.get("query", "")).strip()
            if not query:
                raise ValueError("Specify which saved context is needed")
            return self.store.rows(
                "SELECT * FROM saved_context WHERE instr(lower(content),lower(?))>0 ORDER BY created DESC LIMIT 10",
                (query,),
            )
        if op == "workspace_select_profile":
            return self.select_profile(request["version"])
        if op == "workspace_save_profile":
            return self.save_profile(request["payload"], request["parent"])
        if op == "workspace_opportunity_page":
            return self.opportunity_page(
                request.get("filter", "all"),
                request.get("limit", 100),
                request.get("cursor"),
            )
        if op == "workspace_opportunities":
            return self.opportunities(request.get("filter", "all"))
        if op == "workspace_ingest":
            self.ingest(request["jobs"])
            return {"saved": len(request["jobs"])}
        if op == "workspace_job_action":
            job = self.job(request["identity"])
            action = request["action"]
            if action == "applied":
                return self.applied(job)
            if action == "opened":
                self.store.db.execute(
                    "UPDATE opportunity_index SET opened=? WHERE identity=?",
                    (time.time(), request["identity"]),
                )
            elif action == "saved":
                if type(request.get("saved")) is not bool:
                    raise ValueError("Saved must be a boolean")
                self.store.db.execute(
                    "UPDATE opportunity_index SET saved=? WHERE identity=?",
                    (int(request["saved"]), request["identity"]),
                )
            else:
                raise ValueError("Unknown opportunity action")
            return {"updated": True}
        if op == "workspace_applied":
            return self.applied(request["job"], request.get("applied"))
        if op == "workspace_history":
            return self.history()
        if op == "workspace_assessment":
            return self.assessment(
                request["application_id"],
                request["component"],
                request.get("deadline", ""),
                component_key=request.get("component_key"),
            )
        if op == "workspace_assessment_complete":
            return self.complete_assessment(request["id"], request["revision"])
        if op == "workspace_history_update":
            app = request["application_id"]
            self.assert_app(app)
            outcome = request["outcome"]
            if outcome not in {
                "Application in progress",
                "Awaiting response",
                "Assessment stage",
                "Interview",
                "Offer",
                "Rejected",
                "Withdrawn",
                "Closed",
            }:
                raise ValueError("Unknown recruitment outcome")
            with self.store.tx():
                old = self.store.one(
                    "SELECT revision FROM application_history WHERE application_id=?",
                    (app,),
                )
                if (old["revision"] if old else None) != request.get("revision"):
                    raise ValueError("History changed; reload first")
                self.store.db.execute(
                    "INSERT INTO application_history(application_id,snapshot,outcome,notes) VALUES(?,?,?,?) ON CONFLICT(application_id) DO UPDATE SET outcome=excluded.outcome,notes=excluded.notes,revision=application_history.revision+1",
                    (app, "{}", outcome, str(request.get("notes", ""))[:20000]),
                )
                self.event(app, "history-user-update", {"outcome": outcome})
            return {
                "updated": True,
                "revision": self.store.one(
                    "SELECT revision FROM application_history WHERE application_id=?",
                    (app,),
                )["revision"],
            }
        if op == "workspace_portfolios":
            return [
                {**r, "payload": json.loads(r["payload"])}
                for r in self.store.rows(
                    "SELECT * FROM search_portfolios ORDER BY updated DESC"
                )
            ]
        if op == "workspace_portfolio_save":
            name = str(request["name"]).strip()
            payload = request["payload"]
            no_secrets(payload)
            if not name or not isinstance(payload, dict):
                raise ValueError("Name and search criteria are required")
            from .candidate_matching import criteria

            criteria(payload)
            id = request.get("id") or uid()
            with self.store.tx():
                old = self.store.one(
                    "SELECT revision FROM search_portfolios WHERE id=?", (id,)
                )
                if old and old["revision"] != request.get("revision"):
                    raise ValueError("Portfolio changed; reload first")
                self.store.db.execute(
                    "INSERT INTO search_portfolios VALUES(?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload=excluded.payload,revision=search_portfolios.revision+1,updated=excluded.updated",
                    (id, name, encode(payload), time.time()),
                )
            return {"id": id}
        raise ValueError("Unsupported workspace command")
