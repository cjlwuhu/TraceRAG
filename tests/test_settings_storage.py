"""Credential persistence and corruption checks use synthetic keys and temp roots."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyrag.console.settings import ServiceSettings, SettingsRequest
from easyrag.domain.experiment import ExperimentConfig
from easyrag.domain.work_order import GenerationConfig
from easyrag.console.credential_store import CredentialStore
from easyrag.console.diagnostics import ConsoleProblem


@unittest.skipUnless(os.name == "posix", "Real POSIX credential permissions")
class LinuxSettingsTests(unittest.TestCase):
    def test_ui_keys_are_encrypted_persistent_and_usable_on_linux(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "console"
            services = ServiceSettings(state, {})
            qwen = "sk-SYNTHETIC_LINUX_QWEN_NOT_A_REAL_KEY_1234"
            glm = "SYNTHETIC_LINUX_GLM_NOT_A_REAL_KEY_1234"
            public = services.save(SettingsRequest(cloud_enabled=True, qwen_key=qwen, glm_key=glm))
            self.assertEqual("linux_aesgcm", public["storage"])
            self.assertEqual({"qwen": "saved", "glm": "saved"}, public["credentials"])
            raw = services.path.read_bytes()
            for value in (qwen, glm):
                self.assertNotIn(value.encode(), raw)
                self.assertNotIn(value, json.dumps(public))
            reopened = ServiceSettings(state, {})
            retrieval = ExperimentConfig.parse_obj({"retrieval": {"mode": "dense"}, "embedding": {"enabled": True}})
            runtime, chat = reopened.runtime(retrieval, GenerationConfig(mode="cloud", model="glm-4.7"))
            self.assertEqual(qwen, runtime[0]._api_key)
            self.assertEqual(glm, chat.key)
            self.assertEqual(0o600, services.path.stat().st_mode & 0o777)
            self.assertEqual(0o600, (state / "credential-store.key").stat().st_mode & 0o777)

    def test_key_permissions_and_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            vault = CredentialStore(state, windows=False)
            sealed = vault.seal("qwen", "sk-SYNTHETIC_PERMISSION_TEST_1234")
            vault.key_path.chmod(0o644)
            with self.assertRaises(ConsoleProblem):
                vault.unseal("qwen", sealed)
            vault.key_path.chmod(0o600)
            original = state / "original.key"
            vault.key_path.rename(original)
            vault.key_path.symlink_to(original)
            with self.assertRaises(ConsoleProblem):
                vault.unseal("qwen", sealed)
            vault.key_path.unlink()
            os.link(original, vault.key_path)
            with self.assertRaises(ConsoleProblem):
                vault.unseal("qwen", sealed)


class EncryptedStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.vault = CredentialStore(self.state, windows=False)
        self.key = "sk-SYNTHETIC_NOT_A_REAL_QWEN_SECRET_1234"

    def test_ciphertext_is_randomized_bound_to_provider_and_authenticated(self):
        first = self.vault.seal("qwen", self.key)
        second = self.vault.seal("qwen", self.key)
        self.assertNotEqual(first, second)
        reopened = CredentialStore(self.state, windows=False)
        self.assertEqual(self.key, reopened.unseal("qwen", first))
        self.assertNotIn(self.key, first)
        with self.assertRaises(ConsoleProblem):
            reopened.unseal("glm", first)
        damaged = first[:15] + ("A" if first[15] != "A" else "B") + first[16:]
        with self.assertRaises(ConsoleProblem):
            reopened.unseal("qwen", damaged)
        with self.assertRaises(ConsoleProblem):
            reopened.unseal("qwen", "OLD_WINDOWS_DPAPI_BLOB")

    def test_binary_master_key_preserves_control_bytes(self):
        # Windows CRT text mode treats Ctrl-Z as EOF and translates CR/LF.
        master = b"\x1a\r\n" + bytes(range(29))
        self.vault.key_path.write_bytes(master)
        self.vault.key_path.chmod(0o600)
        self.assertEqual(master, self.vault._master_key())
        sealed = self.vault.seal("qwen", self.key)
        self.assertEqual(self.key, self.vault.unseal("qwen", sealed))

    def test_missing_or_corrupt_master_key_never_rotates_on_read(self):
        sealed = self.vault.seal("qwen", self.key)
        self.vault.key_path.unlink()
        with self.assertRaises(ConsoleProblem):
            self.vault.unseal("qwen", sealed)
        self.assertFalse(self.vault.key_path.exists())
        self.vault.key_path.write_bytes(b"damaged key")
        self.vault.key_path.chmod(0o600)
        with self.assertRaises(ConsoleProblem):
            self.vault.unseal("qwen", sealed)
        self.assertEqual(b"damaged key", self.vault.key_path.read_bytes())

    def services(self):
        with patch("easyrag.console.settings.CredentialStore", side_effect=lambda state: CredentialStore(state, windows=False)):
            return ServiceSettings(self.state, {})

    def test_replacement_preserves_other_key_and_clear_suppresses_environment(self):
        services = self.services()
        glm = "SYNTHETIC_NOT_A_REAL_GLM_SECRET_1234"
        services.save(SettingsRequest(cloud_enabled=True, qwen_key=self.key, glm_key=glm))
        master = self.vault.key_path.read_bytes()
        services.save(SettingsRequest(cloud_enabled=True, qwen_key="sk-SYNTHETIC_REPLACEMENT_5678", glm_key=""))
        reopened = self.services()
        document = reopened._document()
        self.assertEqual(glm, reopened._key("glm", document))
        self.assertEqual("sk-SYNTHETIC_REPLACEMENT_5678", reopened._key("qwen", document))
        self.assertEqual(master, self.vault.key_path.read_bytes())
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-SYNTHETIC_ENVIRONMENT_1234"}):
            reopened.save(SettingsRequest(clear_qwen_key=True))
            self.assertEqual("disabled", self.services().public()["credentials"]["qwen"])
            with self.assertRaises(ConsoleProblem):
                self.services()._key("qwen", self.services()._document())

    def test_missing_master_blocks_replace_until_explicit_clear_and_preserves_document(self):
        services = self.services()
        services.save(SettingsRequest(qwen_key=self.key, glm_key="SYNTHETIC_GLM_SECRET_1234"))
        before = services.path.read_bytes()
        self.vault.key_path.unlink()
        with self.assertRaises(ConsoleProblem):
            services.save(SettingsRequest(qwen_key="sk-SYNTHETIC_REPLACEMENT_5678"))
        self.assertEqual(before, services.path.read_bytes())
        self.assertFalse(self.vault.key_path.exists())
        services.save(SettingsRequest(clear_qwen_key=True, clear_glm_key=True))
        services.save(SettingsRequest(qwen_key=self.key))
        self.assertEqual(self.key, services._key("qwen", services._document()))
