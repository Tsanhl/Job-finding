"""Setup failures must be actionable before any browser consent or secret access."""
import asyncio
import json

import pytest

from src.pilot.mail import load_desktop_config
from src.pilot.runtime import Runtime
from src.pilot.store import Store


@pytest.mark.parametrize("value", [None, "", " ", "/nonexistent/synthetic-oauth.json"])
def test_invalid_path_has_actionable_error(value):
    with pytest.raises(ValueError, match="OAuth"):
        load_desktop_config(value, "candidate@example.test")


@pytest.mark.parametrize("data", [[], {}, {"web": {}}, {"installed": {}}])
def test_wrong_client_type(tmp_path, data):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Desktop app"):
        load_desktop_config(str(path), "candidate@example.test")


def test_runtime_blank_setup_never_starts_consent(tmp_path):
    store = Store(tmp_path / "workspace.sqlite3")
    try:
        runtime = Runtime(store, None, testing=True)
        with pytest.raises(ValueError, match="OAuth"):
            asyncio.run(runtime.command({"op": "gmail_connect", "email": "candidate@example.test", "config_path": ""}))
        assert not runtime.mail_jobs
        assert not store.rows("SELECT * FROM mail_connections")

    finally:
        store.close()
