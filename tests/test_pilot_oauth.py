"""Provider simulations only. No native Keychain calls or real mail."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.pilot.mail import SCOPE, Gmail
from src.pilot.store import Store


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def put(self, service, account, value, **kwargs):
        self.values[service, account] = value

    def _read(self, service, account):
        return self.values.get((service, account))


def test_oauth_connect_identity_scope_and_token_storage(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        secrets = MemorySecrets()
        credential = SimpleNamespace(
            granted_scopes=[SCOPE],
            scopes=[SCOPE],
            token="synthetic-access",
            refresh_token="synthetic-refresh",
            to_json=lambda: json.dumps(
                {
                    "token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "expiry": "time",
                    "client_id": "synthetic",
                    "client_secret": "synthetic",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            ),
        )
        flow = MagicMock()
        flow.run_local_server.return_value = credential

        def response(request):
            assert request.headers["authorization"] == "Bearer synthetic-access"
            return httpx.Response(200, json={"emailAddress": "candidate@example.test"})

        gmail = Gmail(s, secrets, httpx.MockTransport(response))
        with patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=flow,
        ) as create:
            result = await gmail.connect({"installed": {}}, "candidate@example.test")
            assert result["status"] == "CONNECTED"
            assert create.call_args.kwargs["autogenerate_code_verifier"] is True
            assert flow.run_local_server.call_args.kwargs["host"] == "127.0.0.1"
            assert flow.run_local_server.call_args.kwargs["port"] == 0
            assert flow.run_local_server.call_args.kwargs["timeout_seconds"] == 180
        stored = json.loads(next(iter(secrets.values.values())))
        assert "token" not in stored and "expiry" not in stored
        assert "synthetic-refresh" not in json.dumps(
            s.rows("SELECT * FROM mail_connections")
        )
        assert gmail.disconnect("candidate@example.test")["status"] == "DISCONNECTED"
        with pytest.raises(ValueError):
            await gmail.request("candidate@example.test", "/profile")
        s.close()

    asyncio.run(scenario())


def test_oauth_wrong_mailbox_does_not_store_credentials(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        secrets = MemorySecrets()
        gmail = Gmail(
            s,
            secrets,
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"emailAddress": "wrong@example.test"}
                )
            ),
        )
        credentials = SimpleNamespace(
            granted_scopes=[SCOPE], token="synthetic", refresh_token="synthetic"
        )
        flow = MagicMock()
        flow.run_local_server.return_value = credentials
        with patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=flow,
        ):
            with pytest.raises(ValueError, match="identity"):
                await gmail.connect({"installed": {}}, "candidate@example.test")
        assert not secrets.values
        assert not s.rows("SELECT * FROM mail_connections")
        s.close()

    asyncio.run(scenario())


def test_oauth_state_mismatch_rejected_by_provider_library():
    from oauthlib.oauth2 import MismatchingStateError
    from requests_oauthlib import OAuth2Session

    oauth = OAuth2Session(
        "synthetic", redirect_uri="https://localhost/callback", state="expected"
    )
    with pytest.raises(MismatchingStateError):
        oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            authorization_response="https://localhost/callback?code=synthetic&state=wrong",
        )


def test_revoke_disables_connection_and_removes_only_its_token(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        backend = MemorySecrets()
        backend.delete = lambda service, account: backend.values.pop(
            (service, account), None
        )
        backend.put(
            "synthetic-ref",
            "candidate@example.test",
            json.dumps({"refresh_token": "synthetic-refresh"}),
        )
        backend.put("unrelated", "other", "preserve")
        s.db.execute(
            "INSERT INTO mail_connections VALUES(?,?,?,?,?)",
            (
                "candidate@example.test",
                "candidate@example.test",
                json.dumps([SCOPE]),
                "synthetic-ref",
                "CONNECTED",
            ),
        )

        def response(r):
            assert str(r.url) == "https://oauth2.googleapis.com/revoke"
            assert r.method == "POST" and b"synthetic-refresh" in r.content
            return httpx.Response(200)

        gmail = Gmail(s, backend, httpx.MockTransport(response))
        result = await gmail.revoke("candidate@example.test")
        assert result["provider_revocation"] == "CONFIRMED"
        assert await gmail.status("candidate@example.test") == {
            "status": "DISCONNECTED"
        }
        assert backend.values == {("unrelated", "other"): "preserve"}
        s.close()

    asyncio.run(scenario())


def test_cancelled_connect_cannot_restore_disconnected_access(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        backend = MemorySecrets()
        credential = SimpleNamespace(
            granted_scopes=[SCOPE],
            scopes=[SCOPE],
            token="synthetic",
            refresh_token="synthetic",
            to_json=lambda: json.dumps({"refresh_token": "synthetic"}),
        )
        flow = MagicMock()
        flow.run_local_server.return_value = credential
        gmail = Gmail(
            s,
            backend,
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"emailAddress": "candidate@example.test"}
                )
            ),
        )
        with patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=flow,
        ):
            result = await gmail.connect(
                {"installed": {}}, "candidate@example.test", is_current=lambda: False
            )
        assert result["status"] == "CANCELLED"
        assert not backend.values and not s.rows("SELECT * FROM mail_connections")
        s.close()

    asyncio.run(scenario())
