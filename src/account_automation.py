"""Ephemeral employer-account creation and Gmail verification helpers."""

from __future__ import annotations

import re
import secrets
import string
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

LogFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class PortalCredentials:
    """Credentials supplied for this run only; the password is never represented."""

    email: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if "@" not in self.email or self.email.startswith("@"):
            raise ValueError("Provide a valid application email address.")
        if len(self.password) < 8:
            raise ValueError("The employer-account password must contain at least 8 characters.")


class PortalCredentialManager:
    """Provide one credential per employer portal without exposing passwords."""

    def __init__(
        self,
        *,
        email: str,
        shared_password: str = "",
        generate_unique: bool = True,
        save_to_keychain: bool = True,
    ) -> None:
        if "@" not in email or email.startswith("@"):
            raise ValueError("Provide a valid application email address.")
        if generate_unique and not save_to_keychain:
            raise ValueError(
                "Unique generated portal passwords require macOS Keychain storage."
            )
        if not generate_unique and len(shared_password) < 8:
            raise ValueError("The shared employer-account password must contain at least 8 characters.")
        if save_to_keychain and sys.platform != "darwin":
            raise ValueError("macOS Keychain storage is available only on macOS.")
        self.email = email
        self._shared_password = shared_password
        self.generate_unique = generate_unique
        self.save_to_keychain = save_to_keychain
        self._cache: dict[str, PortalCredentials] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(company: str, portal_url: str) -> str:
        host = (urlparse(portal_url).hostname or "portal").lower()
        safe_company = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "employer"
        return f"{safe_company}@{host}"

    @staticmethod
    def _generate_password(length: int = 24) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#%+=_-"
        required = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!@#%+=_-"),
        ]
        required.extend(secrets.choice(alphabet) for _ in range(max(0, length - 4)))
        secrets.SystemRandom().shuffle(required)
        return "".join(required)

    def _save_keychain(self, key: str, password: str) -> None:
        from .pilot.secrets import NativeSecrets
        NativeSecrets().put(f"ApplyPilot:{key}", self.email, password)

    def _load_keychain(self, key: str) -> str:
        from .pilot.secrets import NativeSecrets
        return NativeSecrets()._read(f"ApplyPilot:{key}", self.email) or ""

    def for_portal(self, company: str, portal_url: str) -> PortalCredentials:
        key = self._key(company, portal_url)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            stored_password = self._load_keychain(key) if self.save_to_keychain else ""
            password = stored_password
            if not password:
                password = (
                    self._generate_password()
                    if self.generate_unique
                    else self._shared_password
                )
            if self.save_to_keychain and not stored_password:
                try:
                    self._save_keychain(key, password)
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not save employer credentials to macOS Keychain ({type(exc).__name__})."
                    ) from exc
            credentials = PortalCredentials(email=self.email, password=password)
            self._cache[key] = credentials
            return credentials

    def redact(self, value: str) -> str:
        safe = value
        with self._lock:
            passwords = [credential.password for credential in self._cache.values()]
        if self._shared_password:
            passwords.append(self._shared_password)
        for password in passwords:
            safe = safe.replace(password, "[redacted]")
        return safe


@dataclass(frozen=True, slots=True)
class PortalAccessResult:
    status: str
    detail: str


def _log(message: str, log: LogFn | None) -> None:
    if log:
        log(message)


def choose_verification_link(
    links: Iterable[tuple[str, str]],
    *,
    expected_host: str = "",
) -> str:
    """Return the strongest verification link without exposing message content."""
    expected = expected_host.lower().removeprefix("www.")
    ranked: list[tuple[int, str]] = []
    for text, href in links:
        href = (href or "").strip()
        if not href.startswith("https://"):
            continue
        lowered = f"{text} {href}".lower()
        if any(
            term in lowered
            for term in ("unsubscribe", "email-preference", "manage-preference", "privacy-policy")
        ):
            continue
        host = (urlparse(href).hostname or "").lower().removeprefix("www.")
        if expected and not _hosts_share_trust_group(host, expected):
            continue
        score = 0
        for keyword in ("verify", "verification", "confirm", "activate", "complete registration"):
            if keyword in lowered:
                score += 3
        if expected and _hosts_share_trust_group(host, expected):
            score += 2
        if text.strip().lower() in {"verify", "verify email", "confirm", "confirm email"}:
            score += 2
        if score >= 3:
            ranked.append((score, href))
    return max(ranked, default=(0, ""), key=lambda item: item[0])[1]


_ATS_TRUST_GROUPS = (
    ("myworkdayjobs.com", "workday.com"),
    ("greenhouse.io", "greenhouse.com"),
    ("ashbyhq.com",),
    ("taleo.net",),
    ("smartrecruiters.com",),
    ("lever.co",),
)


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _hosts_share_trust_group(first: str, second: str) -> bool:
    first = first.lower().removeprefix("www.")
    second = second.lower().removeprefix("www.")
    if _host_matches(first, second) or _host_matches(second, first):
        return True
    return any(
        any(_host_matches(first, suffix) for suffix in group)
        and any(_host_matches(second, suffix) for suffix in group)
        for group in _ATS_TRUST_GROUPS
    )


def verification_message_matches(
    *,
    company: str,
    sender_email: str,
    message_text: str,
    expected_host: str = "",
) -> bool:
    """Require a plausible employer/ATS sender and company evidence in the message."""
    sender = sender_email.strip().lower()
    if "@" not in sender:
        return False
    sender_host = sender.rsplit("@", 1)[-1]
    if sender_host in {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com"}:
        return False
    company_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", company.lower())
        if len(token) >= 4 and token not in {"limited", "company", "group"}
    ]
    lowered_message = message_text.lower()
    company_evidence = not company_tokens or any(
        token in lowered_message for token in company_tokens
    )
    sender_evidence = (
        not expected_host
        or _hosts_share_trust_group(sender_host, expected_host)
        or any(token in sender_host for token in company_tokens)
    )
    return company_evidence and sender_evidence


def _verification_page_succeeded(page: Any, *, expected_host: str) -> bool:
    text = _page_text(page)
    if any(
        phrase in text
        for phrase in (
            "invalid verification",
            "verification link has expired",
            "unable to verify",
            "verification failed",
        )
    ):
        return False
    if any(
        phrase in text
        for phrase in (
            "email verified",
            "email has been verified",
            "email confirmed",
            "account activated",
            "verification successful",
        )
    ):
        return True
    final_url = page.url or ""
    final_host = (urlparse(final_url).hostname or "").lower()
    lowered_url = final_url.lower()
    still_verifying = any(
        marker in lowered_url
        for marker in ("verify?", "verification?", "activate?", "token=")
    )
    return bool(
        final_host
        and _hosts_share_trust_group(final_host, expected_host)
        and not still_verifying
        and "verify your email" not in text
    )


class GmailBrowserVerifier:
    """Open a recent employer verification link in the signed-in Gmail browser."""

    _mailbox_lock = threading.Lock()

    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = max(15, min(int(timeout_seconds), 300))

    def verify(
        self,
        context: Any,
        *,
        company: str,
        expected_host: str,
        log: LogFn | None = None,
    ) -> PortalAccessResult:
        with self._mailbox_lock:
            page = context.new_page()
            try:
                page.goto(
                    "https://mail.google.com/mail/u/0/#inbox",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(1500)
                if "accounts.google." in (page.url or "").lower() or page.locator(
                    "input[type='password']"
                ).count():
                    return PortalAccessResult(
                        "needs_user_attention",
                        "Gmail is not signed in in the shared browser.",
                    )

                search = page.locator("input[name='q'], input[aria-label*='Search mail' i]").first
                if not search.count():
                    return PortalAccessResult(
                        "needs_user_attention",
                        "The Gmail search box was not available.",
                    )
                safe_company = re.sub(r"[^a-zA-Z0-9 .&'-]", "", company).strip()
                query = (
                    "newer_than:1d {verify verification confirm activate "
                    f'"confirm your email"}} "{safe_company}"'
                )
                search.fill(query)
                search.press("Enter")

                deadline = time.monotonic() + self.timeout_seconds
                while time.monotonic() < deadline:
                    rows = page.locator("tr.zA, div[role='main'] tr[role='main']")
                    if rows.count():
                        for row_index in range(min(rows.count(), 10)):
                            rows = page.locator("tr.zA, div[role='main'] tr[role='main']")
                            if row_index >= rows.count():
                                break
                            rows.nth(row_index).click()
                            page.wait_for_timeout(1000)
                            try:
                                sender_email = (
                                    page.locator("span[email]").first.get_attribute("email") or ""
                                )
                            except Exception:
                                sender_email = ""
                            try:
                                message_text = (
                                    page.locator("div[role='main']").inner_text(timeout=1000) or ""
                                )
                            except Exception:
                                message_text = ""
                            if not verification_message_matches(
                                company=company,
                                sender_email=sender_email,
                                message_text=message_text,
                                expected_host=expected_host,
                            ):
                                page.go_back(wait_until="domcontentloaded", timeout=30000)
                                continue
                            anchors = page.locator("a[href^='https://']")
                            candidates: list[tuple[str, str]] = []
                            for index in range(min(anchors.count(), 80)):
                                anchor = anchors.nth(index)
                                try:
                                    candidates.append(
                                        (
                                            (anchor.inner_text(timeout=300) or "").strip(),
                                            anchor.get_attribute("href") or "",
                                        )
                                    )
                                except Exception:
                                    continue
                            verification_url = choose_verification_link(
                                candidates,
                                expected_host=expected_host,
                            )
                            if verification_url:
                                target = context.new_page()
                                try:
                                    target.goto(
                                        verification_url,
                                        wait_until="domcontentloaded",
                                        timeout=60000,
                                    )
                                    target.wait_for_timeout(1200)
                                    if not _verification_page_succeeded(
                                        target,
                                        expected_host=expected_host,
                                    ):
                                        return PortalAccessResult(
                                            "needs_user_attention",
                                            "The verification link opened, but success was not confirmed.",
                                        )
                                finally:
                                    target.close()
                                _log("Employer email verification completed.", log)
                                return PortalAccessResult("verified", "Employer email verified.")
                            page.go_back(wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(5000)
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                return PortalAccessResult(
                    "needs_user_attention",
                    "No unambiguous employer verification link arrived before the timeout.",
                )
            except Exception as exc:
                return PortalAccessResult(
                    "needs_user_attention",
                    f"Gmail verification could not complete: {type(exc).__name__}.",
                )
            finally:
                try:
                    page.close()
                except Exception:
                    pass


def _page_text(page: Any) -> str:
    try:
        return (page.locator("body").inner_text(timeout=2000) or "").lower()[:8000]
    except Exception:
        return ""


def _looks_like_auth_wall(page: Any, text: str) -> bool:
    try:
        has_password = page.locator("input[type='password']").count() > 0
    except Exception:
        has_password = False
    url = (page.url or "").lower()
    auth_copy = any(
        phrase in text
        for phrase in ("create an account", "sign in", "log in", "register to apply")
    )
    auth_url = any(
        segment in url
        for segment in ("/login", "/signin", "/sign-in", "/register", "/signup", "/auth")
    )
    return has_password and (auth_copy or auth_url)


def _fill_first(page: Any, selectors: tuple[str, ...], value: str) -> bool:
    for selector in selectors:
        field = page.locator(selector).first
        try:
            if field.count() and field.is_visible(timeout=400):
                field.fill(value)
                return True
        except Exception:
            continue
    return False


def _click_auth_action(page: Any, labels: tuple[str, ...]) -> bool:
    for label in labels:
        for selector in (
            f"button:has-text('{label}')",
            f"input[type='submit'][value*='{label}' i]",
            f"a:has-text('{label}')",
        ):
            control = page.locator(selector).first
            try:
                if control.count() and control.is_visible(timeout=400):
                    control.click(timeout=5000)
                    return True
            except Exception:
                continue
    return False


def _accept_account_terms(page: Any) -> None:
    boxes = page.locator("input[type='checkbox']")
    for index in range(min(boxes.count(), 12)):
        box = boxes.nth(index)
        try:
            if not box.is_visible(timeout=300) or box.is_checked():
                continue
            text = box.evaluate(
                "n => (n.closest('label,div,fieldset')?.innerText || '').slice(0,500)"
            ).lower()
            if any(word in text for word in ("marketing", "newsletter", "job alerts")):
                continue
            if any(
                word in text
                for word in (
                    "i certify",
                    "i declare",
                    "i attest",
                    "information is accurate",
                    "electronic signature",
                    "use of ai",
                    "used ai",
                )
            ):
                continue
            if any(word in text for word in ("terms", "privacy", "data processing", "acknowledge")):
                box.check(timeout=3000)
        except Exception:
            continue


def ensure_portal_access(
    page: Any,
    *,
    credentials: PortalCredentials,
    company: str,
    verifier: GmailBrowserVerifier | None,
    accept_required_terms: bool,
    log: LogFn | None = None,
) -> PortalAccessResult:
    """Create or sign into an employer account, then verify it through Gmail."""
    text = _page_text(page)
    if any(term in text for term in ("captcha", "security check", "two-factor", "verification code")):
        return PortalAccessResult(
            "needs_user_attention",
            "The employer account requires CAPTCHA, two-factor authentication, or a code.",
        )

    _fill_first(
        page,
        ("input[type='email']", "input[name*='email' i]", "input[name*='username' i]"),
        credentials.email,
    )
    password_fields = page.locator("input[type='password']")
    for index in range(min(password_fields.count(), 3)):
        try:
            field_control = password_fields.nth(index)
            if field_control.is_visible(timeout=300):
                field_control.fill(credentials.password)
        except Exception:
            continue
    if accept_required_terms:
        _accept_account_terms(page)

    create_labels = ("Create account", "Create Account", "Register", "Sign up", "Sign Up")
    login_labels = ("Sign in", "Sign In", "Log in", "Login")
    action = "create" if _click_auth_action(page, create_labels) else "login"
    if action == "login" and not _click_auth_action(page, login_labels):
        return PortalAccessResult(
            "needs_user_attention",
            "The employer account action could not be identified.",
        )
    page.wait_for_timeout(1800)
    text = _page_text(page)
    if any(term in text for term in ("captcha", "security check", "two-factor", "verification code")):
        return PortalAccessResult(
            "needs_user_attention",
            "The employer account requires CAPTCHA, two-factor authentication, or a code.",
        )

    verification_pending = any(
        term in text
        for term in (
            "verify your email",
            "verification email",
            "confirmation email",
            "check your email",
            "activation link",
        )
    )
    if verification_pending:
        if verifier is None:
            return PortalAccessResult(
                "needs_user_attention",
                "The employer sent an email verification link, but Gmail verification is disabled.",
            )
        host = urlparse(page.url or "").hostname or ""
        verified = verifier.verify(
            page.context,
            company=company,
            expected_host=host,
            log=log,
        )
        if verified.status != "verified":
            return verified
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1000)
        except Exception:
            pass
        _fill_first(
            page,
            ("input[type='email']", "input[name*='email' i]", "input[name*='username' i]"),
            credentials.email,
        )
        for index in range(min(page.locator("input[type='password']").count(), 2)):
            try:
                page.locator("input[type='password']").nth(index).fill(credentials.password)
            except Exception:
                continue
        _click_auth_action(page, login_labels)
        page.wait_for_timeout(1200)

    final_text = _page_text(page)
    if any(term in final_text for term in ("invalid password", "incorrect password", "already exists")):
        return PortalAccessResult(
            "needs_user_attention",
            "The employer account rejected the supplied credentials or already exists.",
        )
    if _looks_like_auth_wall(page, final_text):
        return PortalAccessResult(
            "needs_user_attention",
            "The employer account page did not confirm a successful sign-in.",
        )
    _log("Employer account is ready.", log)
    return PortalAccessResult("ready", "Employer account is ready.")
