"""Repeated-record reconciliation and bounded Add/Save operations."""

from __future__ import annotations

import json


class Records:
    def __init__(self, adapter):
        self.adapter = adapter

    async def reconcile(self, page, profile, guard, plan):
        """A provider must expose reviewed record mechanics; never guess Add buttons."""
        problems = []
        if self.adapter.name in {"allhires", "apply4law", "workday"}:
            from .portal_records import reconcile

            return await reconcile(page, self.adapter, profile, guard, plan)
        if not self.adapter.record_add_selector:
            return problems
        for kind in ("work_experience", "education"):
            records = profile.get(kind, [])
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list):
                continue
            section = page.locator("[data-record-section=" + json.dumps(kind) + "]")
            if await section.count() != 1:
                continue
            saved = await section.locator("[data-saved-record]").evaluate_all(
                '(els)=>els.map(e=>({id:e.dataset.recordId,values:JSON.parse(e.dataset.values||"{}")}))'
            )
            for record in records:
                if not isinstance(record, dict):
                    continue
                identity = str(record.get("id", ""))
                values = {
                    k: str(record.get(k, ""))
                    for k in (
                        "employer",
                        "title",
                        "institution",
                        "degree",
                        "start",
                        "end",
                        "responsibilities",
                    )
                    if record.get(k) is not None and k in record
                }
                if not identity:
                    problems.append("Repeated records need stable confirmed record IDs")
                    continue
                matches = [
                    r
                    for r in saved
                    if r["id"] == identity
                    or (
                        values
                        and all(r["values"].get(k) == v for k, v in values.items())
                    )
                ]
                if len(matches) == 1:
                    continue
                if matches:
                    problems.append(
                        "Ambiguous CV-parsed duplicate record; review the existing entries"
                    )
                    continue
                add = section.locator(self.adapter.record_add_selector)
                if await add.count() != 1:
                    problems.append("Record Add control is unavailable")
                    continue
                guard()
                plan.require("fill")
                await add.click(timeout=15000)
                editor = section.locator("[data-record-editor]")
                if await editor.count() != 1:
                    problems.append("Record editor not found after Add")
                    break
                for key, value in values.items():
                    control = editor.locator("[name=" + json.dumps(key) + "]")
                    if await control.count() == 1:
                        guard()
                        await control.fill(value, timeout=15000)
                identity_control = editor.locator("[name=record_id]")
                if await identity_control.count() == 1:
                    guard()
                    await identity_control.fill(identity, timeout=15000)
                save = editor.locator(self.adapter.record_save_selector)
                if await save.count() != 1:
                    problems.append("Verified record Save control unavailable")
                    break
                guard()
                await save.click(timeout=15000)
                readback = await section.locator("[data-saved-record]").evaluate_all(
                    '(els)=>els.map(e=>({id:e.dataset.recordId,values:JSON.parse(e.dataset.values||"{}")}))'
                )
                if not any(
                    r["id"] == identity
                    and all(r["values"].get(k) == v for k, v in values.items())
                    for r in readback
                ):
                    problems.append("Saved record differs from confirmed values")
                    break
                saved = readback
        return problems
