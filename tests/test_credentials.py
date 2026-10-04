"""Credential regressions for the optional legacy GLM helper; no SDK/network."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]


class LegacyCredentialTests(unittest.TestCase):
    def load_helper(self):
        sdk = ModuleType("zhipuai")
        self.complete = mock.Mock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="safe reply"))]))
        self.factory = mock.Mock(return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.complete))))
        sdk.ZhipuAI = self.factory
        # The only torch use is the separately optional local GPU function.
        torch = ModuleType("torch")
        spec = importlib.util.spec_from_file_location(
            "credential_test_llm", ROOT / "src/easyrag/utils/llm_utils.py")
        helper = importlib.util.module_from_spec(spec)
        modules = mock.patch.dict("sys.modules", {"zhipuai": sdk, "torch": torch})
        modules.start()
        self.addCleanup(modules.stop)
        spec.loader.exec_module(helper)
        return helper

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_import_does_not_construct_provider_client(self):
        self.load_helper()
        self.assertEqual(self.factory.call_count, 0)

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_credential_fails_before_provider_construction(self):
        helper = self.load_helper()
        with self.assertRaisesRegex(ValueError, "GLM_API_KEY|EASYRAG_LLM_API_KEY"):
            helper.zhipu_generate("question")
        self.assertEqual(self.factory.call_count, 0)

    @mock.patch.dict(os.environ, {"GLM_API_KEY": "fake-primary-env",
                                "EASYRAG_LLM_API_KEY": "fake-fallback-env"}, clear=True)
    def test_text_generation_uses_glm_environment_and_preserves_payload(self):
        helper = self.load_helper()
        self.assertEqual(helper.zhipu_generate("question", "glm-test", "system"), "safe reply")
        # Boolean checks never expose any accidentally reintroduced credential.
        self.assertTrue(self.factory.call_args.kwargs.get("api_key") == "fake-primary-env")
        self.assertEqual(self.complete.call_args.kwargs, {
            "model": "glm-test", "max_tokens": 2048,
            "messages": [{"role": "system", "content": "system"},
                         {"role": "user", "content": "question"}],
        })

    @mock.patch.dict(os.environ, {"EASYRAG_LLM_API_KEY": "fake-fallback-env"}, clear=True)
    def test_generation_accepts_easyrag_environment_fallback(self):
        helper = self.load_helper()
        self.assertEqual(helper.zhipu_generate("question"), "safe reply")
        self.assertTrue(self.factory.call_args.kwargs.get("api_key") == "fake-fallback-env")

    @mock.patch.dict(os.environ, {"GLM_API_KEY": "fake-primary-env"}, clear=True)
    def test_vision_preserves_payload_without_printing_provider_response(self):
        helper = self.load_helper()
        content = [{"type": "text", "text": "image question"}]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            reply = helper.zhipu_chat_vision(content, "glm-vision-test", "system")
        self.assertEqual(reply, "safe reply")
        self.assertEqual(captured.getvalue(), "")
        self.assertEqual(self.complete.call_args.kwargs["messages"], [
            {"role": "system", "content": [{"type": "text", "text": "system"}]},
            {"role": "user", "content": content},
        ])

    def test_default_configuration_contains_no_provider_credentials(self):
        config = yaml.safe_load((ROOT / "src/configs/easyrag.yaml").read_text(encoding="utf-8"))
        self.assertTrue(not config.get("llm_keys"), "default configuration must not contain credentials")


if __name__ == "__main__":
    unittest.main()
