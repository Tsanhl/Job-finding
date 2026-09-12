"""Explicit native macOS Security backend. No subprocess or plaintext fallback."""

from __future__ import annotations

from urllib.parse import urlsplit


class NativeSecrets:
    def _query(self, service, account):
        import Security as S

        return {
            S.kSecClass: S.kSecClassGenericPassword,
            S.kSecAttrService: service,
            S.kSecAttrAccount: account,
        }

    def _read(self, service, account):
        import Security as S

        q = self._query(service, account)
        q.update({S.kSecReturnData: True, S.kSecMatchLimit: S.kSecMatchLimitOne})
        status, data = S.SecItemCopyMatching(q, None)
        if status == S.errSecItemNotFound:
            return None
        if status != S.errSecSuccess:
            raise RuntimeError("Keychain unavailable or access denied")
        return bytes(data).decode()

    def put(self, service, account, value, *, replace=False):
        import Security as S

        q = self._query(service, account)
        if replace:
            status = S.SecItemUpdate(q, {S.kSecValueData: value.encode()})
            if status == S.errSecSuccess:
                return
            if status != S.errSecItemNotFound:
                raise RuntimeError("Keychain update failed")
        q.update(
            {
                S.kSecValueData: value.encode(),
                S.kSecAttrAccessible: S.kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            }
        )
        status, _ = S.SecItemAdd(q, None)
        if status != S.errSecSuccess:
            raise RuntimeError("Keychain write failed; existing secret preserved")

    def delete(self, service, account):
        import Security as S

        status = S.SecItemDelete(self._query(service, account))
        if status not in (S.errSecSuccess, S.errSecItemNotFound):
            raise RuntimeError("Keychain removal failed")


class CredentialBroker:
    def __init__(self, backend=None):
        self.backend = backend or NativeSecrets()

    async def login(
        self,
        page,
        *,
        origin,
        email,
        secret_ref,
        plan,
        guard,
        username_selector,
        password_selector,
        login_selector,
    ):
        plan.require("credentials")
        guard()
        u = urlsplit(page.url)
        if f"{u.scheme}://{u.netloc}" != origin or u.scheme != "https":
            raise PermissionError("Credential destination mismatch")
        controls = [
            page.locator(s)
            for s in (username_selector, password_selector, login_selector)
        ]
        if any([await c.count() != 1 for c in controls]):
            return {"status": "UNSUPPORTED"}
        secret = self.backend._read(secret_ref, email)
        if secret is None:
            return {"status": "NEEDS_AUTHENTICATION"}
        try:
            guard()
            await controls[0].fill(email, timeout=15000)
            guard()
            await controls[1].fill(secret, timeout=15000)
            guard()
            await controls[2].click(timeout=15000)
            return {"status": "LOGIN_ATTEMPTED"}
        finally:
            secret = None
