class CredentialVault:
    """Use Windows Credential Manager explicitly; never fall back to plaintext storage."""

    def __init__(self):
        import sys

        self.backend = None
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            self.backend = WinVaultKeyring()

    def get(self, provider_id: str) -> str | None:
        if not self.backend:
            raise RuntimeError("Windows Credential Manager is unavailable")
        return self.backend.get_password("AnyLlama", provider_id)

    def set(self, provider_id: str, key: str):
        if not self.backend:
            raise RuntimeError("Windows Credential Manager is unavailable")
        self.backend.set_password("AnyLlama", provider_id, key)

    def delete(self, provider_id: str):
        if self.get(provider_id) is not None:
            self.backend.delete_password("AnyLlama", provider_id)
