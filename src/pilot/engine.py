"""Asynchronous shared filling loop with persisted read-back and resumable gaps."""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from urllib.parse import urlsplit

from ..field_manifest import (
    FieldKind,
    ManifestField,
    answer_limit_blocker,
    classify_field,
    exact_option,
    resolve_deterministic_answer,
)
from ..submission_guard import ai_policy_prohibited
from .adapters import choose
from .facts import resolve as resolve_fact
from .models import Result, Scope, State
from .store import digest

MANIFEST = r"""(root) => {
const visible=e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'};
const controls=[...root.querySelectorAll('input:not([type=password]),textarea,select,button[aria-haspopup=listbox][name],[role=combobox],[role=checkbox],[role=radio],[contenteditable=true]')].filter(e=>!/(password|passcode|verification.?code|one.?time|otp|token|captcha)/i.test([e.name,e.id,e.autocomplete,e.getAttribute('aria-label')].join(' ')));
return controls.map((e,i)=>{
 const fieldset=e.closest('fieldset,[role=group],section');
 const option_label=[...(e.labels||[])].map(x=>x.innerText).join(' ').trim();
 let label=option_label||e.getAttribute('aria-label')||e.getAttribute('placeholder')||e.name||'';
 if(['radio','checkbox'].includes(e.type))label=(fieldset?.querySelector('legend')?.textContent||'')+' '+label;
 const section=fieldset?.querySelector('legend,h2,h3')?.textContent||fieldset?.getAttribute('aria-label')||'';
 const name=e.name||''; const id=e.id||'';
 const search=e.matches('input[data-uxi-widget-type=selectinput]');
 const dropdown=e.matches('button[aria-haspopup=listbox][name]');
 const selected=[...e.closest('[data-automation-id=multiselectInputContainer]')?.querySelectorAll('[data-automation-id=selectedItem] [data-automation-id=promptOption]')||[]].map(x=>x.textContent.trim()).join(', ');
 if(dropdown)label=label.replace(/\s+(Select One|Required).*$/,'').trim();
 const groupRequired=!!fieldset?.querySelector('legend')?.textContent.trim().endsWith('*');
 const selector=id?'[id='+JSON.stringify(id)+']':name?'[name='+JSON.stringify(name)+']':e.getAttribute('aria-label')?'[aria-label='+JSON.stringify(e.getAttribute('aria-label'))+']':null;
 return {name,field_id:id||name||label,selector,question:label.trim(),option_label,section:section.trim(),tag:e.tagName.toLowerCase(),input_type:e.type||'',workday_search:search,role:(dropdown||search)?'combobox':e.getAttribute('role')||'',options:e.options?[...e.options].filter(o=>!o.disabled&&o.value!=='').map(o=>o.textContent.trim()):[],required:e.required||e.getAttribute('aria-required')==='true'||(dropdown&&/Required$/.test(e.getAttribute('aria-label')||''))||groupRequired,visible:visible(e),disabled:e.disabled||false,observed_value:dropdown?(e.value?e.innerText:''):selected|| (e.isContentEditable?e.innerText:e.value)||e.getAttribute('aria-valuetext')||'',observed_label:dropdown?(e.value?e.innerText:''):selected|| (e.selectedOptions?[...e.selectedOptions].map(o=>o.textContent.trim()).join(' '):''),checked:e.checked||e.getAttribute('aria-checked')==='true',max_characters:e.maxLength>0?e.maxLength:null,max_words:(()=>{const hint=label+' '+(e.getAttribute('aria-describedby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ');const m=hint.match(/(\d+)\s*words?/i);return m?Number(m[1]):null})(),accept:e.accept||'',files:e.files?[...e.files].map(f=>f.name):[],record:fieldset?.getAttribute('data-record-id')||'',record_kind:fieldset?.getAttribute('data-record-kind')||'',value:e.value||'',index:i};
});}"""


async def inspect(page, root_selector="form,main,body"):
    fields = []
    origin = urlsplit(page.url).netloc
    for frame in page.frames:
        if frame != page.main_frame and urlsplit(frame.url).netloc not in {"", origin}:
            continue
        root = None
        selected = ""
        for selector in root_selector.split(","):
            candidate = frame.locator(selector.strip())
            if await candidate.count() == 1:
                root = candidate
                selected = selector.strip()
                break
            if await candidate.count() > 1 and selector.strip() == "form":
                raise ValueError(
                    "Multiple forms in this frame; a specific application adapter is required"
                )
        if root is None:
            continue
        for raw in await root.evaluate(MANIFEST):
            raw["frame_url"] = frame.url.split("?")[0].split("#")[0]
            raw["root_selector"] = selected
            fields.append(raw)
    return fields


def model(raw):
    names = ManifestField.__dataclass_fields__
    f = ManifestField(**{k: v for k, v in raw.items() if k in names})
    return replace(
        f,
        kind=classify_field(
            f.question,
            field_id=f.field_id,
            section=f.section,
            tag=f.tag,
            input_type=f.input_type,
            role=f.role,
        ),
    )


async def locate(page, raw):
    if not raw["selector"]:
        raise ValueError("No stable field locator; user assistance required")
    frames = [
        f for f in page.frames if f.url.split("?")[0].split("#")[0] == raw["frame_url"]
    ]
    if len(frames) != 1:
        raise ValueError("Application frame changed or is ambiguous")
    frame = frames[0]
    root = frame.locator(raw.get("root_selector", "body"))
    loc = root.locator(raw["selector"])
    if await loc.count() != 1:
        if raw["input_type"] == "radio":
            loc = root.locator(
                raw["selector"] + "[value=" + json.dumps(raw["value"]) + "]"
            )
        if await loc.count() != 1:
            raise ValueError("Field locator is ambiguous")
    return loc


class Engine:
    def __init__(self, documents, *, testing=False, draft=None):
        self.documents = documents
        self.testing = testing
        self.draft = draft

    def question(self, raw, target, reason, kind="information"):
        result = {
            "question": raw.get("question") or reason,
            "field_id": raw.get("field_id", ""),
            "record": raw.get("record", ""),
            "country": target.country,
            "employer": target.employer,
            "role": target.role,
            "identity": target.identity,
            "reason": reason,
            "kind": kind,
        }
        from .question_catalog import match

        preset = match(result["question"])
        if preset:
            result["preset"] = {
                key: preset[key]
                for key in (
                    "id",
                    "answer_type",
                    "profile_path",
                    "reuse",
                    "choices",
                    "review_reason",
                )
                if key in preset
            }
        return result

    async def fill(
        self, page, plan, target, profile, guard, *, checkpoint=None, on_progress=None
    ):
        from .fill_boundary import filling_boundary

        adapter = choose(target.url, testing=self.testing)
        hashes = set()
        if "upload" in plan.permissions and not plan.preview:
            profile_row = self.documents.store.one(
                "SELECT profile_id FROM profile_versions WHERE id=?",
                (plan.profile_version,),
            )
            for doc in plan.documents:
                try:
                    approved = self.documents.resolve(
                        doc, target, profile_row["profile_id"] if profile_row else None
                    )
                    hashes.add(approved["sha256"])
                except ValueError:
                    pass  # Per-field document blockers are collected by the engine.
        async with filling_boundary(
            page, adapter, guard, upload_hashes=hashes
        ) as boundary:
            result = await self._fill(
                page,
                plan,
                target,
                profile,
                guard,
                checkpoint=checkpoint,
                on_progress=on_progress,
            )
        if boundary["upload_failures"]:
            result.state = State.INCOMPLETE
            result.detail = (
                "Upload request failed; saved attachments require reconciliation"
            )
            for document in result.documents:
                document["saved"] = False
        if boundary["blocked"]:
            result.state = State.INCOMPLETE
            result.detail = "Portal attempted an unqualified write or submission during filling; inspect the saved form"
        return result

    async def _fill(
        self, page, plan, target, profile, guard, *, checkpoint=None, on_progress=None
    ):
        adapter = choose(target.url, testing=self.testing)
        evidence = []
        uploads = []
        seen = set()
        started = time.monotonic()
        for step in range(30):
            guard()
            if urlsplit(page.url).netloc != urlsplit(target.url).netloc:
                return Result(
                    State.NEEDS_AUTHENTICATION,
                    detail="Application origin changed; rebind after login",
                )
            if await page.locator("input[type=password]").count():
                return Result(
                    State.NEEDS_AUTHENTICATION,
                    detail="Sign in, then Resume; execution slot released",
                )
            receipt = await adapter.receipt(page, target)
            if receipt:
                return Result(State.SUBMITTED_CONFIRMED, detail=receipt["reference"])
            record_problems = []
            if not plan.preview:
                from .records import Records

                record_problems = await Records(adapter).reconcile(
                    page, profile, guard, plan
                )
            fields = await inspect(page, adapter.root)
            if not fields and await adapter.is_review(page):
                saved_fields = evidence or (checkpoint or {}).get("fields", [])
                body = (await page.locator("body").inner_text()).casefold()
                expected = [
                    f.get("observed_value", "")
                    for f in saved_fields
                    if f.get("input_type") not in {"file", "checkbox", "radio"}
                    and f.get("visible")
                    and f.get("observed_value")
                ]
                if expected and all(
                    str(value).casefold() in body for value in expected
                ):
                    return Result(
                        State.REVIEW_READY,
                        fields=saved_fields,
                        documents=uploads or (checkpoint or {}).get("documents", []),
                        detail="Saved final summary matches the inspected fields",
                    )
                return Result(
                    State.INCOMPLETE,
                    fields=saved_fields,
                    documents=uploads,
                    detail="Final summary needs reconciliation with saved answers",
                )
            if not fields:
                return Result(
                    State.UNSUPPORTED,
                    detail="No supported fields; enter the application or select its frame",
                )
            if plan.preview:
                return Result(
                    State.PAGE_FILLED,
                    fields=fields,
                    detail="Preview only; no changes made",
                )
            fingerprint = digest(
                [
                    (r["field_id"], r["section"], r["observed_value"], r["checked"])
                    for r in fields
                ]
            )
            if fingerprint in seen:
                return Result(
                    State.INCOMPLETE,
                    fields=fields,
                    detail="No progress after navigation; resume from this saved step",
                )
            seen.add(fingerprint)
            blockers = [
                self.question({}, target, p, "records") for p in record_problems
            ]
            text = (await page.locator("body").inner_text())[:30000]
            prohibited = ai_policy_prohibited(text)
            for raw in fields:
                guard()
                if raw["disabled"] or (
                    not raw["visible"] and raw["input_type"] != "file"
                ):
                    continue
                f = model(raw)
                if f.input_type in {"submit", "reset", "hidden"} or (
                    f.input_type == "button" and f.kind != FieldKind.CUSTOM_DROPDOWN
                ):
                    continue
                sensitive = f.kind in {
                    FieldKind.SIGNATURE,
                    FieldKind.DECLARATION,
                    FieldKind.CONSENT,
                    FieldKind.DEMOGRAPHIC,
                }
                # Portal edits are preserved; compare known facts and validate limits.
                if f.has_observed_value and f.input_type != "file":
                    exact = profile.get("application_answers", {}).get(
                        target.identity, {}
                    )
                    expected_value = (
                        str(exact[f.field_id])
                        if f.field_id in exact
                        else resolve_fact(f, profile, target)
                    )
                    if (
                        not sensitive
                        and expected_value
                        and f.input_type not in {"checkbox", "radio"}
                        and expected_value.strip().casefold()
                        != (raw.get("observed_label") or f.observed_value)
                        .strip()
                        .casefold()
                    ):
                        # An existing value is never overwritten without a user decision.
                        blockers.append(
                            self.question(
                                raw,
                                target,
                                "Existing value differs from the selected profile",
                                "conflict",
                            )
                        )
                    if answer_limit_blocker(f, f.observed_value):
                        blockers.append(
                            self.question(
                                raw,
                                target,
                                "Existing answer exceeds the question limit",
                            )
                        )
                    continue
                if sensitive:
                    if f.required and not f.has_observed_value:
                        blockers.append(
                            self.question(
                                raw,
                                target,
                                "Complete this personal decision in the form",
                                "decision",
                            )
                        )
                    continue
                try:
                    control = await locate(page, raw)
                    if f.input_type == "file":
                        kind = {
                            FieldKind.CV_UPLOAD: "cv",
                            FieldKind.COVER_LETTER_UPLOAD: "cover_letter",
                            FieldKind.TRANSCRIPT_UPLOAD: "transcript",
                        }.get(
                            f.kind,
                            "writing_sample"
                            if "writing sample" in f.question.lower()
                            else "other",
                        )
                        matches = []
                        for id in plan.documents:
                            registered = self.documents.store.one(
                                "SELECT * FROM documents WHERE id=?", (id,)
                            )
                            if not registered or registered["kind"] != kind:
                                continue
                            applicability = json.loads(registered["applicability"])
                            if any(
                                applicability.get(key)
                                and applicability[key] != getattr(target, key)
                                for key in ("employer", "role", "identity")
                            ):
                                continue
                            doc = self.documents.resolve(
                                id,
                                target,
                                self.documents.store.one(
                                    "SELECT profile_id FROM profile_versions WHERE id=?",
                                    (plan.profile_version,),
                                )["profile_id"],
                            )
                            if doc["kind"] == kind:
                                matches.append(doc)
                        if len(matches) != 1:
                            if f.required:
                                blockers.append(
                                    self.question(
                                        raw,
                                        target,
                                        "Choose one approved document for this upload",
                                        "document",
                                    )
                                )
                            continue
                        doc = matches[0]
                        if (prohibited or target.ai_policy != "allowed") and kind in {
                            "cover_letter",
                            "writing_sample",
                        }:
                            scope = json.loads(doc["applicability"])
                            if scope.get("authorship") != "candidate":
                                blockers.append(
                                    self.question(
                                        raw,
                                        target,
                                        "Employer AI policy unresolved or prohibits AI; candidate-authored approval required",
                                        "policy",
                                    )
                                )
                                continue
                        if raw["accept"]:
                            accepts = [
                                x.strip().lower() for x in raw["accept"].split(",")
                            ]
                            if not any(
                                doc["path"].lower().endswith(x)
                                if x.startswith(".")
                                else doc["mime"] == x
                                or (x.endswith("/*") and doc["mime"].startswith(x[:-1]))
                                for x in accepts
                            ):
                                blockers.append(
                                    self.question(
                                        raw,
                                        target,
                                        "Approved document format is not accepted",
                                        "document",
                                    )
                                )
                                continue
                        from pathlib import Path

                        filename = Path(doc["path"]).name
                        previous = next(
                            (
                                d
                                for d in (checkpoint or {}).get("documents", [])
                                if d.get("document_id") == doc["id"]
                                and d.get("field_id") == f.field_id
                                and d.get("saved")
                            ),
                            None,
                        )
                        accepted = await adapter.attachment_evidence(
                            page, f.field_id, filename
                        )
                        if not (previous and accepted):
                            guard()
                            plan.require("upload")
                            await control.set_input_files(doc["path"], timeout=15000)
                            accepted = None
                            for _ in range(60):
                                guard()
                                accepted = await adapter.attachment_evidence(
                                    page, f.field_id, filename
                                )
                                if accepted:
                                    break
                                await page.wait_for_timeout(250)
                        saved = accepted is not None
                        uploads.append(
                            {
                                "document_id": doc["id"],
                                "field_id": f.field_id,
                                "saved": saved,
                                "evidence": accepted,
                            }
                        )
                        if not saved:
                            blockers.append(
                                self.question(
                                    raw,
                                    target,
                                    "File selected; portal acceptance still needs verification",
                                    "upload_pending",
                                )
                            )
                        continue
                    proposal = resolve_deterministic_answer(
                        f, profile=profile, defaults={}
                    )
                    value = resolve_fact(f, profile, target)
                    if raw["record"]:
                        records = profile.get(raw["record_kind"], [])
                        record = (
                            next(
                                (
                                    r
                                    for r in records
                                    if isinstance(r, dict)
                                    and str(r.get("id")) == raw["record"]
                                ),
                                None,
                            )
                            if isinstance(records, list)
                            else None
                        )
                        if record:
                            label = f.question.lower()
                            mapping = {
                                "employer": "employer",
                                "company": "employer",
                                "job title": "title",
                                "responsibilities": "responsibilities",
                                "institution": "institution",
                                "degree": "degree",
                                "start date": "start",
                                "end date": "end",
                            }
                            key = next(
                                (v for k, v in mapping.items() if k in label), None
                            )
                            if key:
                                value = str(record.get(key, ""))
                    exact = profile.get("application_answers", {}).get(
                        target.identity, {}
                    )
                    if raw["field_id"] in exact:
                        value = str(exact[raw["field_id"]])
                    if (
                        not value
                        and self.draft
                        and f.kind
                        in {
                            FieldKind.COVER_LETTER,
                            FieldKind.MOTIVATION,
                            FieldKind.COMPETENCY,
                            FieldKind.NARRATIVE,
                        }
                        and target.ai_policy == "allowed"
                        and not prohibited
                        and "external_ai" in plan.permissions
                    ):
                        guard()
                        plan.require("external_ai")
                        value = await self.draft(f, profile, target, plan)
                    if not value:
                        if f.required:
                            q = self.question(
                                raw,
                                target,
                                proposal.blocker or "Required answer is missing",
                            )
                            suggestion = resolve_deterministic_answer(
                                f,
                                profile=profile.get("_candidate_suggestions", {}),
                                defaults={},
                            ).value
                            if suggestion:
                                q["suggested_value"] = suggestion
                                q["reason"] = (
                                    "Confirm this existing imported value for this question"
                                )
                            blockers.append(q)
                        continue
                    if f.input_type == "date" and not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", value
                    ):
                        blockers.append(
                            self.question(
                                raw,
                                target,
                                "Exact date required; month/year will not be invented",
                            )
                        )
                        continue
                    if answer_limit_blocker(f, value):
                        blockers.append(
                            self.question(
                                raw, target, "Answer exceeds the question limit"
                            )
                        )
                        continue
                    guard()
                    plan.require("fill")
                    if f.tag == "select":
                        option = exact_option(f.options, value)
                        if not option:
                            raise ValueError("No exact option for confirmed answer")
                        await control.select_option(label=option, timeout=15000)
                    elif f.kind == FieldKind.CUSTOM_DROPDOWN:
                        if raw.get("workday_search"):
                            await control.fill(value, timeout=15000)
                        else:
                            await control.click(timeout=15000)
                        option = page.get_by_role("option", name=value, exact=True)
                        await option.wait_for(state="visible", timeout=10000)
                        if await option.count() != 1:
                            raise ValueError("No unique exact dropdown option")
                        guard()
                        if adapter.name == "workday":
                            from .workday import select_option

                            await select_option(page, target, raw, option, value, guard)
                        else:
                            await option.click(timeout=15000)
                    elif f.input_type == "radio" or f.role == "radio":
                        if value.casefold() not in {
                            raw["value"].casefold(),
                            raw.get("option_label", "").casefold(),
                            f.question.casefold(),
                        }:
                            continue
                        await control.check(timeout=15000)
                    elif f.input_type == "checkbox" or f.role == "checkbox":
                        await control.set_checked(
                            value.casefold() in {"yes", "true"}, timeout=15000
                        )
                    else:
                        await control.fill(value, timeout=15000)
                    # Reacquire to survive portal rerenders.
                    reread = await inspect(page, adapter.root)
                    if on_progress:
                        on_progress(evidence + reread, uploads)
                    current = next(
                        (
                            r
                            for r in reread
                            if r["field_id"] == raw["field_id"]
                            and r["record"] == raw["record"]
                            and r["frame_url"] == raw["frame_url"]
                        ),
                        None,
                    )
                    if not current:
                        raise ValueError("Field disappeared before read-back")
                    if (
                        f.input_type not in {"radio", "checkbox"}
                        and f.tag != "select"
                        and f.kind != FieldKind.CUSTOM_DROPDOWN
                        and current["observed_value"] != value
                    ):
                        raise ValueError("Portal did not retain the entered answer")
                except PermissionError:
                    raise
                except Exception as exc:  # noqa: BLE001 - report one field and continue
                    blockers.append(
                        self.question(
                            raw,
                            target,
                            str(exc)
                            if isinstance(exc, ValueError)
                            else "Portal control failed; resume to retry safely",
                            "technical",
                        )
                    )
            latest = await inspect(page, adapter.root)
            # Validate required radio groups collectively, not each unchecked option.
            radios = {}
            for raw in latest:
                if raw["disabled"] or not raw["visible"]:
                    continue
                if raw["input_type"] == "radio":
                    radios.setdefault(
                        (raw.get("name") or raw["field_id"], raw["section"]), []
                    ).append(raw)
                    continue
                if (
                    raw["required"]
                    and raw["input_type"] != "file"
                    and not model(raw).has_observed_value
                    and not any(q["field_id"] == raw["field_id"] for q in blockers)
                ):
                    blockers.append(
                        self.question(raw, target, "Required value is not saved")
                    )
            for rows in radios.values():
                if any(r["required"] for r in rows) and not any(
                    r["checked"] for r in rows
                ):
                    blockers.append(
                        self.question(rows[0], target, "Choose a required option")
                    )
            evidence.extend(latest)
            if on_progress:
                on_progress(evidence, uploads)
            if blockers:
                return Result(
                    State.NEEDS_INFORMATION,
                    blockers,
                    evidence,
                    uploads,
                    "Independent fields completed; answer the grouped questions or edit the form and Resume",
                )
            if target.scope == Scope.CURRENT_PAGE:
                return Result(
                    State.PAGE_FILLED,
                    fields=evidence,
                    documents=uploads,
                    detail="Current page filled and read back",
                )
            if await adapter.is_review(page):
                return Result(
                    State.REVIEW_READY,
                    fields=evidence,
                    documents=uploads,
                    detail="Supported final review reached; saved fields validated",
                )
            action = await adapter.next_action(page)
            if action is None:
                return Result(
                    State.INCOMPLETE,
                    fields=evidence,
                    documents=uploads,
                    detail="No qualified non-final navigation; move to the next section and Resume",
                )
            guard()
            plan.require("fill")
            if adapter.name == "workday":
                from .workday import advance

                await advance(page, target, action, guard)
            else:
                await adapter.advance(page, action, guard)
            await page.wait_for_timeout(150)
            if time.monotonic() - started > 120:
                return Result(
                    State.INCOMPLETE,
                    fields=evidence,
                    documents=uploads,
                    detail="Execution slice complete; Resume continues from saved progress",
                )
        return Result(
            State.INCOMPLETE,
            fields=evidence,
            documents=uploads,
            detail="Step limit reached; completion not assumed",
        )
