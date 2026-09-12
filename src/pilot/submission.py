"""The only final transmission path. Intent precedes click; uncertain is not retryable."""

import time
from dataclasses import asdict

from .adapters import choose
from .models import Result, State
from .store import encode, uid


class SubmitService:
    def __init__(self, store, documents, *, testing=False):
        self.store = store
        self.documents = documents
        self.testing = testing

    async def submit(self, page, plan, target, app, run, result, guard):
        plan.require("submit")
        guard()
        if not plan.will_submit(target):
            raise PermissionError("Target-scoped submission authority required")
        if result.state != State.REVIEW_READY or result.blockers:
            raise ValueError("Validated final review required")
        if target.eligibility != "eligible":
            return Result(
                State.ELIGIBILITY_UNVERIFIED,
                detail="Confirm current eligibility before submission",
            )
        if self.store.load_profile(plan.profile_version).get(
            "_history_reconciliation_required"
        ):
            return Result(
                State.REVIEW_READY,
                fields=result.fields,
                documents=result.documents,
                detail="Reconcile imported application history before submission",
            )
        adapter = choose(target.url, testing=self.testing)
        level = (
            "SYNTHETIC" if self.testing or plan.supervised_acceptance else "UNATTENDED"
        )
        qualified = self.store.one(
            "SELECT * FROM adapter_qualifications WHERE adapter=? AND workflow=? AND level=?",
            (adapter.name, "submit", level),
        )
        from .qualification import valid

        if (
            not qualified or (not self.testing and not valid(qualified))
        ) or not adapter.implemented_submission:
            return Result(
                State.REVIEW_READY,
                fields=result.fields,
                documents=result.documents,
                detail="Adapter lacks submission qualification; manual review remains available",
            )
        if not await adapter.is_review(page) or not await adapter.identity_matches(
            page, target
        ):
            raise ValueError("Final review or account/application identity changed")
        if await adapter.receipt(page, target):
            raise ValueError("Already submitted; reconcile receipt")
        for d in plan.documents:
            self.documents.resolve(
                d,
                target,
                self.store.one(
                    "SELECT profile_id FROM profile_versions WHERE id=?",
                    (plan.profile_version,),
                )["profile_id"],
            )
        if any(not d["saved"] for d in result.documents):
            raise ValueError("Attachment acceptance unresolved")
        from .engine import inspect

        expected = {
            (f.get("frame_url"), f.get("field_id"), f.get("record", "")): f
            for f in result.fields
        }

        async def unchanged():
            current = await inspect(page, adapter.root)
            for field in current:
                if field.get("disabled") or (
                    not field.get("visible") and field.get("input_type") != "file"
                ):
                    continue
                previous = expected.get(
                    (
                        field.get("frame_url"),
                        field.get("field_id"),
                        field.get("record", ""),
                    )
                )
                if previous is None or any(
                    field.get(key) != previous.get(key)
                    for key in ("observed_value", "checked", "files")
                ):
                    raise ValueError(
                        "Form changed after validation; inspect and approve the updated review"
                    )

        await unchanged()
        attempt = uid()
        with self.store.tx():
            guard()
            self.store.db.execute(
                "INSERT INTO submission_attempts VALUES(?,?,?,?,?,?)",
                (attempt, app, run, "INTENT", encode(asdict(result)), time.time()),
            )
            self.store.db.execute(
                "UPDATE applications SET state=? WHERE id=?", ("SUBMITTING", app)
            )
            self.store.event(app, "submission-intent", {"attempt": attempt})
        try:
            guard()
            plan.require("submit")
            control = page.locator(adapter.submit_selector)
            if await control.count() != 1:
                raise ValueError("Ambiguous final control")
            await unchanged()
            guard()
            plan.require("submit")
            await control.click(timeout=15000)
            receipt = None
            for _ in range(20):
                receipt = await adapter.receipt(page, target)
                if receipt:
                    break
                await page.wait_for_timeout(100)
            if receipt:
                with self.store.tx():
                    self.store.db.execute(
                        "UPDATE submission_attempts SET state=? WHERE id=?",
                        ("CONFIRMED", attempt),
                    )
                    self.store.db.execute(
                        "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                        (
                            uid(),
                            attempt,
                            app,
                            "portal",
                            receipt["reference"],
                            encode(receipt),
                            time.time(),
                        ),
                    )
                    self.store.event(app, "submission-confirmed", {"attempt": attempt})
                return Result(State.SUBMITTED_CONFIRMED, detail=receipt["reference"])
        except Exception:
            # Do not disclose secret-bearing URLs or retry a possible transmission.
            pass
        self.store.db.execute(
            "UPDATE submission_attempts SET state=? WHERE id=?",
            ("UNCONFIRMED", attempt),
        )
        return Result(
            State.SUBMISSION_UNCONFIRMED,
            detail="Possible transmission; reconcile portal status before another attempt",
        )
