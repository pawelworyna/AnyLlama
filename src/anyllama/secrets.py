class CredentialVault:
    """Use an OS credential store explicitly; never fall back to plaintext storage."""

    def __init__(self):
        import sys

        self.backend = None
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            self.backend = WinVaultKeyring()
        elif sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            self.backend = Keyring()
        elif sys.platform.startswith("linux"):
            from keyring.backends.SecretService import Keyring

            self.backend = Keyring()

    def get(self, provider_id: str) -> str | None:
        if not self.backend:
            raise RuntimeError("The system credential store is unavailable")
        return self.backend.get_password("AnyLlama", provider_id)

    def set(self, provider_id: str, key: str):
        if not self.backend:
            raise RuntimeError("The system credential store is unavailable")
        self.backend.set_password("AnyLlama", provider_id, key)

    def delete(self, provider_id: str):
        if self.get(provider_id) is not None:
            self.backend.delete_password("AnyLlama", provider_id)
