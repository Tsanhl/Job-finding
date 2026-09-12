#!/usr/bin/env python3
"""
Fill + apply the 3 external tabs (GE HealthCare, Mishcon, Marex).
Keeps Chromium open. PINGs on missing info. Never closes browser.
"""

from __future__ import annotations

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.pilot.legacy import main as runtime_entry
    runtime_entry()
    raise SystemExit(0)


import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import defaults_for_location, get_context
from src.config import ensure_dirs, load_config
from src.external_apply import fill_generic_application_form, looks_like_signup_wall
from src.profile import load_profile

JOBS = [
    {
        "company": "GE HealthCare",
        "title": "Regulatory Affairs Intern",
        "urls": [
            "https://careers.gehealthcare.com/global/en/job/GEVGHLGLOBALR4043524EXTERNALENGLOBAL/Regulatory-Affairs-Intern?utm_source=linkedin&utm_medium=phenom-feeds",
            "https://careers.gehealthcare.com/",
        ],
    },
    {
        "company": "Mishcon de Reya LLP",
        "title": "Legal PA - Private (Art Law)",
        "urls": [
            "https://fsr.cvmailuk.com/mishcon/main.cfm?page=jobSpecific&jobId=78562&rcd=73025&srxksl=1",
        ],
    },
    {
        "company": "Marex",
        "title": "Legal Graduate",
        "urls": [
            "https://marex.breezy.hr/p/e706e005373b01-legal-graduate/apply",
            "https://marex.breezy.hr/p/e706e005373b01-legal-graduate?src=LinkedIn",
        ],
    },
]


def find_or_open(context, urls: list[str]):
    for p in context.pages:
        u = (p.url or "").lower()
        for target in urls:
            key = target.split("?")[0].lower()
            if key in u or any(part in u for part in key.split("/")[-2:] if len(part) > 8):
                return p
    page = context.new_page()
    page.goto(urls[0], wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    return page


def page_snapshot(page) -> dict:
    try:
        return page.evaluate(
            """() => ({
              url: location.href.slice(0, 200),
              title: document.title.slice(0, 100),
              hasPassword: !!document.querySelector('input[type=password]'),
              hasFile: !!document.querySelector('input[type=file]'),
              buttons: [...document.querySelectorAll('button, a, input[type=submit]')]
                .slice(0, 20)
                .map(el => ((el.innerText||el.value||'') + '').trim().slice(0, 40))
                .filter(Boolean),
              body: (document.body?.innerText||'').replace(/\\s+/g,' ').slice(0, 400),
            })"""
        )
    except Exception as e:
        return {"error": str(e)[:120]}


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    defaults = defaults_for_location(dict(cfg["defaults"]), "London")
    cv = cfg["cv_path"]

    print("=" * 60, flush=True)
    print("Fill 3 external applications — Chromium STAYS OPEN", flush=True)
    print("=" * 60, flush=True)

    context = get_context(cfg["browser_data_dir"])
    # Ensure LinkedIn page exists somewhere
    if not any("linkedin.com" in (p.url or "") for p in context.pages):
        try:
            lp = context.new_page()
            lp.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass

    results = []
    pings = []

    for job in JOBS:
        print(f"\n=== {job['title']} @ {job['company']} ===", flush=True)
        page = find_or_open(context, job["urls"])
        try:
            page.bring_to_front()
        except Exception:
            pass
        page.wait_for_timeout(1200)
        snap = page_snapshot(page)
        print(f"  URL: {snap.get('url')}", flush=True)
        print(f"  Buttons sample: {snap.get('buttons', [])[:8]}", flush=True)

        if looks_like_signup_wall(page) or snap.get("hasPassword"):
            msg = f"PING: still need login/signup on {job['company']} — {snap.get('url')}"
            print(f"  *** {msg} ***", flush=True)
            pings.append(msg)
            results.append({**job, "status": "needs-authentication", "url": page.url})
            continue

        detail = fill_generic_application_form(
            page,
            profile=profile,
            defaults=defaults,
            cv_path=cv,
            job_context=f"{job['title']} at {job['company']}",
            company=job["company"],
            role=job["title"],
            use_ai=False,
            dry_run=False,
            log=lambda m: print(f"  {m}", flush=True),
        )
        print(f"  → {detail.to_dict()}", flush=True)
        status = detail.status.value
        if status != "review-ready":
            ping = (
                f"PING: {job['company']} — {detail.detail}. "
                f"Tell me any missing answers (or finish submit in that tab)."
            )
            print(f"  *** {ping} ***", flush=True)
            pings.append(ping)
        results.append(
            {**job, **detail.to_dict(), "url": page.url}
        )
        time.sleep(1)

    out = Path(cfg["output_dir"]) / "external_fill_results.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    print("\n=== SUMMARY ===", flush=True)
    for r in results:
        print(f"  [{r['status']}] {r['title']} @ {r['company']}", flush=True)
    if pings:
        print("\n*** ACTION NEEDED FROM YOU ***", flush=True)
        for p in pings:
            print(f"  • {p}", flush=True)
        print("Reply in chat with the missing details; I will update your profile and continue.", flush=True)
    else:
        print("All applications reached their safest available review state.", flush=True)
    print("\nWorker done. Chromium kept open by open_browser.py (owner).", flush=True)
    print("Say 'finish all tasks' only when you want Chromium closed.", flush=True)
    # Do NOT close context — we are CDP-attached; closing would kill the shared browser.


if __name__ == "__main__":
    main()
