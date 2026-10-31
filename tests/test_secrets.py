import sys
import types

import pytest

from anyllama.secrets import CredentialVault


@pytest.mark.parametrize(
    "platform,module,backend_name",
    [
        ("win32", "keyring.backends.Windows", "WinVaultKeyring"),
        ("darwin", "keyring.backends.macOS", "Keyring"),
        ("linux", "keyring.backends.SecretService", "Keyring"),
    ],
)
def test_credential_store_uses_platform_backend(monkeypatch, platform, module, backend_name):
    class Backend:
        def set_password(self, service, account, password):
            self.saved = (service, account, password)

        def get_password(self, service, account):
            assert (service, account) == self.saved[:2]
            return self.saved[2]

        def delete_password(self, service, account):
            self.deleted = (service, account)

    fake = types.ModuleType(module)
    setattr(fake, backend_name, Backend)
    monkeypatch.setitem(sys.modules, module, fake)
    monkeypatch.setattr(sys, "platform", platform)
    vault = CredentialVault()
    vault.set("test-provider", "test-key")
    assert vault.get("test-provider") == "test-key"
    vault.delete("test-provider")
    assert vault.backend.deleted == ("AnyLlama", "test-provider")


def test_unknown_platform_never_falls_back_to_plaintext(monkeypatch):
    monkeypatch.setattr(sys, "platform", "unsupported")
    vault = CredentialVault()
    with pytest.raises(RuntimeError, match="credential store"):
        vault.set("test-provider", "test-key")
