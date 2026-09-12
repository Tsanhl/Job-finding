"""Employer-account handoffs. ApplyPilot never handles portal passwords."""

from __future__ import annotations


class Accounts:
    def __init__(self, store, backend=None):
        self.store = store

    def add_existing(self, employer, origin, email, password):
        """Reject the retired password-storage interface without touching the secret."""
        raise ValueError(
            "Employer passwords are not collected or stored. Sign in directly on the employer website."
        )

    async def ensure(self, page, target, plan, guard):
        plan.require("credentials")
        guard()
        return {
            "status": "NEEDS_AUTHENTICATION",
            "detail": (
                "Sign in directly on the employer website, complete any email "
                "verification, then Resume. ApplyPilot does not access your password."
            ),
        }

    async def register(
        self, page, target, plan, guard, transactions, *, selectors=None
    ):
        plan.require("register")
        guard()
        return {
            "status": "NEEDS_AUTHENTICATION",
            "detail": (
                "Create the employer account and set its password yourself. Complete "
                "any email verification, sign in, then Resume. ApplyPilot does not "
                "read, generate, store, or submit portal passwords."
            ),
        }
