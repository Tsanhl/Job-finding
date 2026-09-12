"""Desktop Gmail OAuth and transaction-bound verification; no mail in FILL_ONLY."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import re
import socket
import time
from email.utils import parseaddr
from urllib.parse import urlsplit

import httpx

from .secrets import NativeSecrets
from .store import encode, uid

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
API = "https://gmail.googleapis.com/gmail/v1/users/me"


class Gmail:
    def __init__(self, store, backend=None, transport=None):
        self.store = store
        self.backend = backend or NativeSecrets()
        self.transport = transport
        self.tokens = {}

    async def connect(self, config, expected_email, *, is_current=lambda: True):
        from google_auth_oauthlib.flow import InstalledAppFlow

        if set(config) != {"installed"}:
            raise ValueError("Google Desktop OAuth client configuration required")
        flow = InstalledAppFlow.from_client_config(
            config, [SCOPE], autogenerate_code_verifier=True
        )
        credentials = await asyncio.to_thread(
            flow.run_local_server,
            host="127.0.0.1",
            port=0,
            open_browser=True,
            timeout_seconds=180,
            authorization_prompt_message="Complete Gmail consent in the system browser.",
            success_message="Consent received. Return to ApplyPilot to check connection status.",
            prompt="consent",
            access_type="offline",
        )
        if SCOPE not in (credentials.granted_scopes or credentials.scopes or []):
            raise PermissionError("Gmail read scope was not granted")
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            response = await client.get(
                API + "/profile",
                headers={"Authorization": "Bearer " + credentials.token},
            )
            response.raise_for_status()
            identity = response.json()["emailAddress"]
        if identity.casefold() != expected_email.casefold():
            raise ValueError(
                "Selected Gmail identity does not match the requested mailbox"
            )
        if not credentials.refresh_token:
            raise ValueError("Offline refresh token missing; reconnect with consent")
        ref = "ApplyPilot:Gmail:" + identity.casefold()
        stored = json.loads(credentials.to_json())
        stored.pop("token", None)
        stored.pop("expiry", None)
        if not is_current():
            return {"email": identity, "status": "CANCELLED"}
        self.backend.put(ref, identity, json.dumps(stored), replace=True)
        self.store.db.execute(
            "INSERT OR REPLACE INTO mail_connections VALUES(?,?,?,?,?)",
            (identity.casefold(), identity, encode([SCOPE]), ref, "CONNECTED"),
        )
        return {"email": identity, "status": "CONNECTED", "scope": SCOPE}

    async def _token(self, connection):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        raw = self.backend._read(connection["secret_ref"], connection["email"])
        if not raw:
            raise ValueError("Reconnect Gmail")
        credential = Credentials.from_authorized_user_info(json.loads(raw), [SCOPE])
        if not credential.valid:
            try:
                await asyncio.to_thread(credential.refresh, Request())
            except Exception:
                self.store.db.execute(
                    "UPDATE mail_connections SET status='RECONNECT' WHERE id=?",
                    (connection["id"],),
                )
                raise ValueError("Gmail token expired or revoked; reconnect")
        return credential.token

    async def request(self, connection_id, path, params=None):
        row = self.store.one(
            "SELECT * FROM mail_connections WHERE id=? AND status='CONNECTED'",
            (connection_id,),
        )
        if not row:
            raise ValueError("Gmail connection required")
        token = await self._token(row)
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            response = await client.get(
                API + path, params=params, headers={"Authorization": "Bearer " + token}
            )
            if response.status_code in (401, 403):
                self.store.db.execute(
                    "UPDATE mail_connections SET status='RECONNECT' WHERE id=?",
                    (connection_id,),
                )
                raise ValueError("Reconnect Gmail")
            if response.status_code == 429:
                raise ValueError("Gmail rate limit; retry later")
            response.raise_for_status()
            return response.json()

    async def status(self, connection_id):
        row = self.store.one(
            "SELECT * FROM mail_connections WHERE id=?", (connection_id,)
        )
        if not row or row["status"] != "CONNECTED":
            return {"status": row["status"] if row else "NOT_CONNECTED"}
        profile = await self.request(connection_id, "/profile")
        return {"status": "CONNECTED", "email": profile["emailAddress"]}

    async def revoke(self, connection_id):
        row = self.store.one(
            "SELECT * FROM mail_connections WHERE id=?", (connection_id,)
        )
        if not row:
            return {"status": "DISCONNECTED", "provider_revocation": "No connection"}
        raw = self.backend._read(row["secret_ref"], row["email"])
        # Disable local use before making a remote request whose response may be lost.
        self.disconnect(connection_id)
        if raw:
            token = json.loads(raw).get("refresh_token")
            if token:
                async with httpx.AsyncClient(
                    timeout=20, transport=self.transport
                ) as client:
                    response = await client.post(
                        "https://oauth2.googleapis.com/revoke", data={"token": token}
                    )
                    if response.status_code != 200:
                        return {
                            "status": "DISCONNECTED",
                            "provider_revocation": "UNCONFIRMED; retry revoke or use Google account settings",
                        }
            self.backend.delete(row["secret_ref"], row["email"])
        return {"status": "DISCONNECTED", "provider_revocation": "CONFIRMED"}

    def disconnect(self, connection_id):
        row = self.store.one(
            "SELECT * FROM mail_connections WHERE id=?", (connection_id,)
        )
        if row:
            self.store.db.execute(
                "UPDATE mail_connections SET status='DISCONNECTED' WHERE id=?",
                (connection_id,),
            )
            self.tokens.pop(connection_id, None)
        return {
            "status": "DISCONNECTED",
            "provider_revocation": "Not requested; revoke separately in Google account settings",
        }


def validate_destination(url, rules, *, testing=False):
    u = urlsplit(url)
    if (
        u.username
        or u.password
        or u.fragment
        or u.scheme not in ({"http", "https"} if testing else {"https"})
    ):
        raise ValueError("Forbidden verification destination")
    if (u.hostname or "") not in rules.get("hosts", []):
        raise ValueError("Unapproved verification host")
    if not any(u.path.startswith(prefix) for prefix in rules.get("paths", [])):
        raise ValueError("Unapproved verification path")
    if not testing:
        addresses = socket.getaddrinfo(
            u.hostname, u.port or 443, type=socket.SOCK_STREAM
        )
        if any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("Non-public destination")
    return url


class EmailTransactions:
    def __init__(self, store, gmail, *, testing=False):
        self.store = store
        self.gmail = gmail
        self.testing = testing

    def begin(self, app, mailbox, recipient, tenant, action, rules, plan):
        plan.require("mail")
        if action not in {"activation", "magic_login", "email_otp", "receipt"}:
            raise PermissionError(
                "Unsupported email action; recovery requires separate handling"
            )
        if not rules.get("senders") or not rules.get("hosts") or not rules.get("paths"):
            raise ValueError("Exact sender/destination rules required")
        id = uid()
        self.store.db.execute(
            "INSERT INTO email_transactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                id,
                app,
                None,
                mailbox,
                recipient,
                tenant,
                action,
                time.time(),
                time.time() + 600,
                encode(rules),
                "PENDING",
                None,
            ),
        )
        return id

    def matches(self, transaction, message):
        if transaction["status"] != "PENDING" or transaction["expires"] <= time.time():
            return False
        timestamp = int(message.get("internalDate", 0)) / 1000
        if not transaction["requested"] - 5 <= timestamp <= transaction["expires"]:
            return False
        headers = message.get("payload", {}).get("headers", [])

        def values(name):
            return [
                h["value"] for h in headers if h["name"].casefold() == name.casefold()
            ]

        sender = parseaddr((values("From") or [""])[0])[1].casefold()
        recipients = [
            parseaddr(v)[1].casefold() for v in values("To") + values("Delivered-To")
        ]
        rules = json.loads(transaction["rules"])
        if (
            sender not in rules["senders"]
            or transaction["recipient"].casefold() not in recipients
        ):
            return False
        auth = values("Authentication-Results")
        # Reject ambiguous header sets. Gmail-added, aligned DKIM evidence required;
        # no display-name, keyword or arbitrary header acceptance.
        domain = sender.rsplit("@", 1)[-1]
        if len(auth) != 1 or not auth[0].lstrip().startswith("mx.google.com;"):
            return False
        if not re.search(r"\bdkim=pass\b", auth[0]) or not re.search(
            r"header\.(?:d|i)=@?" + re.escape(domain) + r"(?:[;\s]|$)", auth[0]
        ):
            return False
        subject = (values("Subject") or [""])[0]
        if not rules.get("subject") or rules["subject"] not in subject:
            return False
        return True

    async def poll(self, id, plan):
        plan.require("mail")
        tx = self.store.one("SELECT * FROM email_transactions WHERE id=?", (id,))
        if not tx or tx["status"] != "PENDING":
            raise ValueError("No active email transaction")
        if tx["expires"] <= time.time():
            self.store.db.execute(
                "UPDATE email_transactions SET status='EXPIRED' WHERE id=?", (id,)
            )
            return None
        rules = json.loads(tx["rules"])
        query = (
            "after:"
            + str(int(tx["requested"]) - 5)
            + " to:"
            + tx["recipient"]
            + " {"
            + " ".join("from:" + s for s in rules["senders"])
            + "}"
        )
        ids = await self.gmail.request(
            tx["mailbox"], "/messages", {"q": query, "maxResults": 10}
        )
        matches = []
        for item in ids.get("messages", []):
            metadata = await self.gmail.request(
                tx["mailbox"], "/messages/" + item["id"], {"format": "metadata"}
            )
            if self.matches(tx, metadata):
                matches.append(item["id"])
        if len(matches) != 1:
            return None
        if self.store.one(
            "SELECT id FROM email_transactions WHERE message_id=?", (matches[0],)
        ):
            return None
        full = await self.gmail.request(
            tx["mailbox"], "/messages/" + matches[0], {"format": "full"}
        )
        chunks = []

        def body(part):
            if part.get("mimeType") in {"text/plain", "text/html"} and part.get(
                "body", {}
            ).get("data"):
                chunks.append(
                    base64.urlsafe_b64decode(part["body"]["data"] + "===").decode(
                        errors="replace"
                    )
                )
            for child in part.get("parts", []):
                body(child)

        body(full["payload"])
        text = "\n".join(chunks)
        # Return only to the narrow local consumer, never to UI/model/DB/logs.
        if tx["action"] == "email_otp":
            codes = set(re.findall(r"(?<!\d)\d{6}(?!\d)", text))
            value = next(iter(codes)) if len(codes) == 1 else None
        else:
            urls = set(re.findall(r'https?://[^\s<>"\']+', text))
            valid = []
            for url in urls:
                try:
                    valid.append(validate_destination(url, rules, testing=self.testing))
                except ValueError:
                    continue
            value = valid[0] if len(valid) == 1 else None
        if value:
            with self.store.tx():
                self.store.db.execute(
                    "UPDATE email_transactions SET message_id=?,status='CONSUMING' WHERE id=? AND status='PENDING'",
                    (matches[0], id),
                )
            return {"transaction": id, "value": value, "action": tx["action"]}
        return None

    async def consume(
        self,
        page,
        payload,
        plan,
        guard,
        *,
        positive_state,
        otp_selector="",
        otp_action="",
    ):
        """Consume once locally; never prefetch a one-use link or return its value."""
        tx = self.store.one(
            "SELECT * FROM email_transactions WHERE id=?", (payload["transaction"],)
        )
        if not tx or tx["status"] != "CONSUMING":
            raise ValueError("Transaction already consumed or not active")
        guard()
        rules = json.loads(tx["rules"])
        capability = "otp" if tx["action"] == "email_otp" else "activate"
        plan.require(capability)
        try:
            if tx["action"] == "email_otp":
                validate_destination(page.url, rules, testing=self.testing)
                control = page.locator(otp_selector)
                action = page.locator(otp_action)
                if (
                    not otp_selector
                    or not otp_action
                    or await control.count() != 1
                    or await action.count() != 1
                ):
                    raise ValueError("OTP adapter not qualified")
                guard()
                await control.fill(payload["value"], timeout=15000)
                guard()
                await action.click(timeout=15000)
            else:
                destination = validate_destination(
                    payload["value"], rules, testing=self.testing
                )

                async def guard_navigation(route):
                    try:
                        if route.request.is_navigation_request():
                            validate_destination(
                                route.request.url, rules, testing=self.testing
                            )
                        await route.fallback()
                    except Exception:
                        await route.abort()

                await page.route("**/*", guard_navigation)
                try:
                    guard()
                    await page.goto(
                        destination, wait_until="domcontentloaded", timeout=30000
                    )
                finally:
                    await page.unroute("**/*", guard_navigation)
            if await positive_state(page):
                self.store.db.execute(
                    "UPDATE email_transactions SET status='CONSUMED' WHERE id=?",
                    (tx["id"],),
                )
                return {"status": "CONFIRMED"}
        except Exception:
            pass
        finally:
            payload.pop("value", None)
        self.store.db.execute(
            "UPDATE email_transactions SET status='UNCONFIRMED' WHERE id=?", (tx["id"],)
        )
        return {
            "status": "UNCONFIRMED",
            "detail": "Reconcile account state; do not consume again",
        }
