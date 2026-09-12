"""Opt-in daily Gmail polling. Evidence is encrypted; assessment links are never opened."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from email.utils import getaddresses

import httpx
from cryptography.fernet import Fernet

from .mail import Gmail
from .secrets import NativeSecrets
from .store import digest, encode
from .workspace import AssessmentIdentityError

DAY = 86400
CONTINUATION = 30
RETRY_BASE = 60
RETRY_MAX = 3600


class EvidenceVault:
    def __init__(self, store, backend=None):
        self.backend = backend or NativeSecrets()
        self.store = store
        saved = store.one(
            "SELECT payload FROM workspace_settings WHERE key='evidence_vault'"
        )
        self.ref = (
            json.loads(saved["payload"])["ref"]
            if saved
            else "ApplyPilot:Evidence:" + digest(str(store.path))[:24]
        )
        if not saved:
            store.db.execute(
                "INSERT INTO workspace_settings VALUES('evidence_vault',?,?)",
                (encode({"ref": self.ref}), time.time()),
            )

    def cipher(self, create=True):
        key = self.backend._read(self.ref, "local-owner")
        if not key:
            if not create or self.store.one(
                "SELECT id FROM recruitment_messages LIMIT 1"
            ):
                raise ValueError(
                    "Mail evidence key is unavailable; restore its recovery bundle"
                )
            key = Fernet.generate_key().decode()
            self.backend.put(self.ref, "local-owner", key, replace=False)
        return Fernet(key.encode())

    def seal(self, payload):
        return self.cipher().encrypt(encode(payload).encode()).decode()

    def open(self, payload):
        return json.loads(self.cipher(create=False).decrypt(payload.encode()))


def parse_message(message):
    headers = {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    chunks = []

    def visit(part):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            chunks.append(
                base64.urlsafe_b64decode(part["body"]["data"] + "===").decode(
                    errors="replace"
                )[:100000]
            )
        for child in part.get("parts", []):
            visit(child)

    visit(message.get("payload", {}))
    text = "\n".join(chunks) or str(message.get("snippet", ""))
    subject = headers.get("subject", "")
    content = (subject + "\n" + text).lower()
    if re.search(
        r"(?<!not )\b(?:successfully completed|assessment complete|completed your assessment)\b",
        content,
    ):
        kind = "ASSESSMENT_COMPLETE"
    elif re.search(
        r"\b(assessment|psychometric|online test|coding test|arctic shores|hirevue)\b",
        content,
    ):
        kind = "ASSESSMENT"
    elif re.search(r"\b(interview invitation|invite you to an interview)\b", content):
        kind = "INTERVIEW"
    elif re.search(r"\b(unfortunately|not been successful|not progressing)\b", content):
        kind = "OUTCOME_REVIEW"
    elif re.search(
        r"\b(application received|received your application|thank you for applying)\b",
        content,
    ):
        kind = "RECEIPT"
    else:
        kind = "OTHER"
    # Preserve wording. Relative/ambiguous dates require portal review, never fabricated arithmetic.
    deadline = next(
        (
            line.strip()[:500]
            for line in text.splitlines()
            if re.search(
                r"\b(deadline|complete.{0,35}by|working days|within.{0,20}days)\b",
                line,
                re.I,
            )
        ),
        "",
    )
    return {
        "subject": subject[:500],
        "excerpt": text[:6000],
        "deadline": deadline,
        "kind": kind,
        "recipients": [
            a.casefold()
            for _, a in getaddresses(
                [headers.get("to", ""), headers.get("delivered-to", "")]
            )
        ],
        "sender": headers.get("from", "")[:500],
    }


class MailTracking:
    def __init__(self, store, workspace, gmail=None, vault=None, clock=time.time):
        self.store = store
        self.workspace = workspace
        self.gmail = gmail or Gmail(store)
        self.vault = vault or EvidenceVault(store)
        self.clock = clock
        self.locks = {}

    def status(self):
        return self.store.rows(
            "SELECT c.id,c.email,c.status AS connection_status,t.enabled,t.interval_seconds,t.lookback_days,t.next_due,t.last_success,t.last_page_success,t.failures,t.status FROM mail_connections c LEFT JOIN mail_tracking t ON t.connection_id=c.id"
        )

    def configure(self, request):
        id = request["connection_id"]
        enabled = request.get("enabled")
        days = request.get("lookback_days", 90)
        if type(enabled) is not bool or type(days) is not int or not 1 <= days <= 365:
            raise ValueError(
                "Choose tracking on/off and a lookback between 1 and 365 days"
            )
        row = self.store.one("SELECT status FROM mail_connections WHERE id=?", (id,))
        if not row or row["status"] != "CONNECTED":
            raise ValueError("Connect this Gmail account first")
        self.workspace.owner()
        with self.store.tx():
            old = self.store.one(
                "SELECT last_success,status,next_due FROM mail_tracking WHERE connection_id=?",
                (id,),
            )
            due = (
                max(self.clock(), (old["last_success"] or 0) + DAY)
                if old
                else self.clock()
            )
            if old and old["status"] in {
                "BACKLOG",
                "RETRY_PENDING",
                "CURSOR_EXPIRED",
                "INTERRUPTED",
            }:
                due = max(
                    self.clock(), min(old["next_due"], self.clock() + CONTINUATION)
                )
            self.store.db.execute(
                "INSERT INTO mail_tracking(connection_id,enabled,lookback_days,next_due,status) VALUES(?,?,?,?,?) ON CONFLICT(connection_id) DO UPDATE SET enabled=excluded.enabled,lookback_days=excluded.lookback_days,next_due=excluded.next_due,status=excluded.status,lease_until=0",
                (id, int(enabled), days, due, "READY" if enabled else "DISABLED"),
            )
        return {"interval_seconds": DAY, "enabled": enabled}

    def match(self, evidence, connection):
        if connection.casefold() not in evidence["recipients"]:
            return None
        content = (evidence["subject"] + " " + evidence["excerpt"]).casefold()
        candidates = []
        for app in self.workspace.history():
            employer = app["employer"].casefold()
            role = app["role"].casefold()
            # Two independent exact text anchors; shared provider domains alone never match.
            if (
                len(employer) >= 3
                and len(role) >= 8
                and employer in content
                and role in content
            ):
                candidates.append(app["id"])
        return candidates[0] if len(candidates) == 1 else None

    def apply_evidence(self, message, evidence, app):
        self.workspace.assert_app(app)
        kind = message["classification"]
        if kind == "ASSESSMENT":
            try:
                self.workspace.assessment(
                    app,
                    evidence["subject"] or "Online assessment",
                    evidence["deadline"],
                    message["id"],
                )
            except AssessmentIdentityError:
                return (
                    False  # Leave ambiguous historical duplicates in the review queue.
                )

        # Completion, interviews and receipts remain reviewable evidence. A generic message
        # cannot confirm every test, manufacture a receipt, or overwrite a manual outcome.
        self.workspace.event(
            app, "mail-" + kind.lower(), {"evidence_id": message["id"]}
        )
        self.store.db.execute(
            "UPDATE recruitment_messages SET application_id=?,resolved=1 WHERE id=?",
            (app, message["id"]),
        )

    def resolve(self, id, app, assessment_id=None):
        row = self.store.one("SELECT * FROM recruitment_messages WHERE id=?", (id,))
        if not row:
            raise ValueError("Unknown message")
        self.workspace.assert_app(app)
        if row["resolved"]:
            if row["application_id"] != app:
                raise ValueError("Message already linked to another application")
            return {"linked": True}
        evidence = self.vault.open(row["protected_payload"])
        with self.store.tx():
            if assessment_id:
                assessment = self.store.one(
                    "SELECT application_id FROM assessments WHERE id=?",
                    (assessment_id,),
                )
                if (
                    not assessment
                    or assessment["application_id"] != app
                    or row["classification"]
                    not in {"ASSESSMENT", "ASSESSMENT_COMPLETE"}
                ):
                    raise ValueError(
                        "Choose an assessment belonging to this application"
                    )
                self.store.db.execute(
                    "INSERT OR IGNORE INTO assessment_evidence VALUES(?,?,?,?)",
                    (assessment_id, id, evidence["deadline"], self.clock()),
                )
                self.store.db.execute(
                    "UPDATE recruitment_messages SET application_id=?,resolved=1 WHERE id=?",
                    (app, id),
                )
                self.workspace.event(
                    app,
                    "assessment-evidence-linked",
                    {"assessment": assessment_id, "evidence_id": id},
                )
            elif self.apply_evidence(row, evidence, app) is False:
                return {"linked": False, "needs_assessment": True}
        return {"linked": True}

    def messages(self):
        result = []
        for row in self.store.rows(
            "SELECT * FROM recruitment_messages ORDER BY received DESC LIMIT 200"
        ):
            try:
                evidence = self.vault.open(row["protected_payload"])
            except Exception:
                evidence = {
                    "subject": "Evidence locked; check Keychain access",
                    "excerpt": "",
                    "deadline": "",
                }
            result.append(
                {k: v for k, v in row.items() if k != "protected_payload"} | evidence
            )
        return result

    async def sync(self, connection):
        lock = self.locks.setdefault(connection, asyncio.Lock())
        if lock.locked():
            return {"state": "ALREADY_RUNNING"}
        async with lock:
            row = self.store.one(
                "SELECT * FROM mail_tracking WHERE connection_id=?", (connection,)
            )
            if not row:
                raise ValueError("Enable Gmail tracking before the first sync")
            now = self.clock()
            if row["lease_until"] > now:
                return {"state": "ALREADY_RUNNING"}
            lease = now + 300
            self.store.db.execute(
                "UPDATE mail_tracking SET lease_until=?,status='SYNCING' WHERE connection_id=?",
                (lease, connection),
            )
            try:
                async with asyncio.timeout(240):
                    result = await self._sync_page(connection, row, lease)
                return result
            except asyncio.CancelledError:
                self.store.db.execute(
                    "UPDATE mail_tracking SET lease_until=0,status='INTERRUPTED',next_due=? WHERE connection_id=? AND lease_until=?",
                    (self.clock() + CONTINUATION, connection, lease),
                )
                raise
            except Exception as error:
                status = "RETRY_PENDING"
                delay = min(RETRY_MAX, RETRY_BASE * 2 ** min(row["failures"], 6))
                if isinstance(error, httpx.HTTPStatusError):
                    if error.response.status_code in (401, 403):
                        status, delay = "CHECK_CONNECTION", DAY
                    else:
                        retry = error.response.headers.get("Retry-After", "")
                        if retry.isdigit():
                            delay = min(RETRY_MAX, max(delay, int(retry)))
                self.store.db.execute(
                    "UPDATE mail_tracking SET lease_until=0,status=?,next_due=?,failures=failures+1 WHERE connection_id=? AND lease_until=?",
                    (status, self.clock() + delay, connection, lease),
                )
                raise

    async def _sync_page(self, connection, row, lease):
        mode = row["sync_mode"]
        anchor = row["anchor_history"]
        params = {"maxResults": 100}
        if row["page_token"]:
            params["pageToken"] = row["page_token"]
        if row["history_id"] and mode == "history":
            params.update(startHistoryId=row["history_id"], historyTypes="messageAdded")
            try:
                response = await self.gmail.request(connection, "/history", params)
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
                self.store.db.execute(
                    "UPDATE mail_tracking SET history_id=NULL,page_token=NULL,sync_mode='initial',anchor_history=NULL,lease_until=0,status='CURSOR_EXPIRED',next_due=? WHERE connection_id=? AND lease_until=?",
                    (self.clock() + CONTINUATION, connection, lease),
                )
                return {
                    "state": "CURSOR_EXPIRED",
                    "next_action": "Automatic lookback reconciliation scheduled shortly",
                }
            ids = list(
                dict.fromkeys(
                    m["message"]["id"]
                    for h in response.get("history", [])
                    for m in h.get("messagesAdded", [])
                )
            )[:100]
            # Never advance past unprocessed changes in an oversized history page.
            all_ids = list(
                dict.fromkeys(
                    m["message"]["id"]
                    for h in response.get("history", [])
                    for m in h.get("messagesAdded", [])
                )
            )
            if len(all_ids) > 100:
                ids = all_ids  # bounded by provider page; deadline interrupts safely before cursor commit
        else:
            if not anchor:
                anchor = (await self.gmail.request(connection, "/profile"))["historyId"]
                with self.store.tx():
                    self._guard(connection, lease)
                    self.store.db.execute(
                        "UPDATE mail_tracking SET anchor_history=? WHERE connection_id=?",
                        (anchor, connection),
                    )
            params["q"] = (
                f'newer_than:{row["lookback_days"]}d {{assessment application interview psychometric "online test" "coding test"}}'
            )
            response = await self.gmail.request(connection, "/messages", params)
            ids = [m["id"] for m in response.get("messages", [])]
        processed = 0
        for id in ids:
            if self.store.one(
                "SELECT id FROM recruitment_messages WHERE connection_id=? AND message_id=?",
                (connection, id),
            ):
                continue
            message = await self.gmail.request(
                connection, "/messages/" + id, {"format": "metadata"}
            )
            preview = parse_message(message)
            if preview["kind"] != "OTHER":
                message = await self.gmail.request(
                    connection, "/messages/" + id, {"format": "full"}
                )
            else:
                # Do not retain non-recruitment subjects or snippets from mailbox history.
                message = {**message, "snippet": "", "payload": {}}
            evidence = parse_message(message)
            app = (
                self.match(evidence, connection)
                if evidence["kind"] != "OTHER"
                else None
            )
            key = digest([connection, id])
            sealed = self.vault.seal(evidence)
            with self.store.tx():
                self._guard(connection, lease)
                self.store.db.execute(
                    "INSERT OR IGNORE INTO recruitment_messages VALUES(?,?,?,?,?,?,?,0)",
                    (
                        key,
                        connection,
                        id,
                        None,
                        evidence["kind"],
                        sealed,
                        int(message.get("internalDate", self.clock() * 1000)) / 1000,
                    ),
                )
                if app:
                    self.apply_evidence(
                        {"id": key, "classification": evidence["kind"]}, evidence, app
                    )
            processed += 1
        token = response.get("nextPageToken")
        history = (
            row["history_id"]
            if token
            else (response.get("historyId") if mode == "history" else anchor)
        )
        pending = bool(token) or mode == "initial"
        with self.store.tx():
            self._guard(connection, lease)
            self.store.db.execute(
                "UPDATE mail_tracking SET history_id=?,page_token=?,sync_mode=?,anchor_history=?,last_success=?,next_due=?,lease_until=0,status=?,failures=0,last_page_success=? WHERE connection_id=?",
                (
                    history,
                    token,
                    mode if token else "history",
                    anchor if token else None,
                    row["last_success"] if pending else self.clock(),
                    self.clock() + (CONTINUATION if pending else DAY),
                    "BACKLOG" if pending else "UP_TO_DATE",
                    self.clock(),
                    connection,
                ),
            )
        return {
            "state": "BACKLOG" if pending else "UP_TO_DATE",
            "processed": processed,
            "next_action": "Automatic continuation scheduled shortly"
            if pending
            else "Next automatic check in one day",
        }

    def _guard(self, connection, lease):
        row = self.store.one(
            "SELECT t.lease_until,c.status FROM mail_tracking t JOIN mail_connections c ON c.id=t.connection_id WHERE t.connection_id=?",
            (connection,),
        )
        if not row or row["lease_until"] != lease or row["status"] != "CONNECTED":
            raise ValueError("Tracking stopped or connection changed")

    async def tick(self):
        for row in self.store.rows(
            "SELECT t.connection_id FROM mail_tracking t JOIN mail_connections c ON c.id=t.connection_id WHERE t.enabled=1 AND t.next_due<=? AND t.lease_until<=? AND c.status='CONNECTED'",
            (self.clock(), self.clock()),
        ):
            try:
                await self.sync(row["connection_id"])
            except Exception:
                pass  # status is persisted without mailbox contents

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(30)
