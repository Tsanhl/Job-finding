"""Reviewed one-time import; source databases remain read-only and unchanged."""

import json
import sqlite3
import time
from pathlib import Path

from .store import digest, encode, uid


def preview(path, owner=None):
    source = Path(path).expanduser().resolve(strict=True)
    db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("JobSignal database integrity check failed")
        users = [
            dict(r) for r in db.execute("SELECT id,subject,display_name FROM users")
        ]
        if not owner:
            return {
                "users": [
                    {
                        "id": u["id"],
                        "label": u["display_name"],
                        "demo": u["subject"].startswith("demo:"),
                    }
                    for u in users
                ],
                "path": str(source),
            }
        user = next((u for u in users if u["id"] == owner), None)
        if not user:
            raise ValueError("Unknown source owner")
        searches = [
            {"id": r["id"], "payload": json.loads(r["body"])}
            for r in db.execute("SELECT * FROM searches WHERE user_id=?", (owner,))
        ]
        saved = [
            {**dict(r), "payload": json.loads(r["payload"])}
            for r in db.execute(
                "SELECT s.*,j.payload FROM saved_jobs s JOIN jobs j ON j.id=s.job_id WHERE s.user_id=?",
                (owner,),
            )
        ]
        profile = db.execute(
            "SELECT body FROM profiles WHERE user_id=?", (owner,)
        ).fetchone()
        payload = {
            "owner": owner,
            "demo": user["subject"].startswith("demo:"),
            "searches": searches,
            "saved": saved,
            "profile_proposal": json.loads(profile["body"]) if profile else {},
        }
        return {
            "path": str(source),
            "fingerprint": digest(payload),
            "owner": owner,
            "demo": payload["demo"],
            "search_count": len(searches),
            "saved_count": len(saved),
            "applied_count": sum(r["state"] == "applied" for r in saved),
            "has_profile_proposal": bool(profile),
            "payload": payload,
        }
    finally:
        db.close()


def apply(runtime, request):
    if request.get("confirmed") is not True:
        raise ValueError("Review the import preview before importing")
    checked = preview(request["path"], request["owner"])
    if checked["fingerprint"] != request["fingerprint"]:
        raise ValueError("Source changed after preview; review it again")
    store = runtime.store
    ws = runtime.workspace
    old = store.one(
        "SELECT id FROM legacy_imports WHERE source_hash=?", (checked["fingerprint"],)
    )
    if old:
        return {"state": "ALREADY_IMPORTED"}
    destination = store.path.parent / "backups"
    destination.mkdir(exist_ok=True, mode=0o700)
    store.backup(
        destination / ("before-jobsignal-import-" + str(time.time_ns()) + ".sqlite3")
    )
    data = checked["payload"]
    with store.tx():
        ws.owner()
        for search in data["searches"]:
            source = search["payload"]
            portfolio = {
                "query": " ".join(
                    source.get("keywords") or source.get("areas") or ["graduate"]
                ),
                "location": ", ".join(source.get("locations", [])),
                "requested": 10,
                "filters": {
                    k: v
                    for k, v in source.items()
                    if k not in {"candidate", "name", "include_in_alerts"}
                },
            }
            ws.command(
                {
                    "op": "workspace_portfolio_save",
                    "id": "import-" + digest([request["owner"], search["id"]])[:24],
                    "name": source.get("name", "Imported search"),
                    "payload": portfolio,
                }
            )
        for saved in data["saved"]:
            j = saved["payload"]
            job = {
                "identity": "jobsignal:" + saved["job_id"],
                "url": j.get("apply_url") or j["source_url"],
                "employer": j["employer"],
                "role": j["title"],
                "location": j.get("location", ""),
                "opening": j.get("opens", ""),
                "deadline": j.get("closes", ""),
                "requirements": j.get("requirements_text", ""),
                "imported_evidence": "Historical JobSignal record; current portal state unverified",
            }
            ws.ingest([job])
            if saved["state"] == "applied":
                app = ws.applied(job, saved["updated_at"])["application_id"]
                store.db.execute(
                    "UPDATE application_history SET notes=? WHERE application_id=?",
                    (saved["note"], app),
                )
                ws.event(
                    app,
                    "imported-user-reported-application",
                    {"verified_receipt": False},
                )
            ws.command(
                {
                    "op": "workspace_job_action",
                    "identity": job["identity"],
                    "action": "saved",
                    "saved": saved["state"] != "hidden",
                }
            )
        # Existing owner facts are never overwritten by an imported/demo profile.
        ws.set_setting(
            "import_proposal:" + checked["fingerprint"],
            {
                "profile": data["profile_proposal"],
                "demo": data["demo"],
                "status": "REVIEW_REQUIRED",
            },
        )
        store.db.execute(
            "INSERT INTO legacy_imports VALUES(?,?,?,?)",
            (
                uid(),
                checked["fingerprint"],
                encode(
                    {
                        "search_count": checked["search_count"],
                        "saved_count": checked["saved_count"],
                        "profile_proposal_only": True,
                    }
                ),
                time.time(),
            ),
        )
    return {
        "state": "IMPORTED",
        "searches": checked["search_count"],
        "saved": checked["saved_count"],
        "profile": "Preserved as an unconfirmed proposal; current profile unchanged",
    }
