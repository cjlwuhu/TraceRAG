"""Service preferences and platform-specific, write-only encrypted credentials."""

import os
import re
import threading
from typing import Literal, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr, StrictBool, conint, constr, root_validator, validator

from easyrag.console.diagnostics import ConsoleProblem
from easyrag.console.store import read_json
from easyrag.console.credential_store import CredentialStore, PREFIX, protect
from easyrag.domain.experiment import StrictModel
from easyrag.retrieval.cloud_models import CloudModelError, DashScopeModels, read_api_key
from easyrag.retrieval.vector_cache import CachedEmbeddings


class Preferences(StrictModel):
    cloud_enabled: StrictBool = False
    generation_provider: Literal["qwen", "glm"] = "qwen"
    qwen_model: Literal["qwen-plus", "qwen-flash"] = "qwen-plus"
    glm_model: constr(strict=True, max_length=80, regex=r"^glm-[a-zA-Z0-9.-]+$") = "glm-4.7"
    qwen_region: Literal["cn", "intl", "us"] = "cn"
    timeout_seconds: conint(strict=True, ge=10, le=120) = 60
    proxy: constr(strict=True, max_length=200) = ""

    @validator("proxy")
    def local_proxy(cls, value):
        if value:
            try:
                u = urlsplit(value)
                valid = (u.scheme in {"http", "https"} and u.hostname in {"127.0.0.1", "localhost", "::1"}
                         and u.port and not u.username and not u.password and u.path in {"", "/"}
                         and not u.query and not u.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("use a loopback HTTP proxy URL")
        return value


class SettingsRequest(Preferences):
    qwen_key: Optional[SecretStr] = None
    glm_key: Optional[SecretStr] = None
    clear_qwen_key: StrictBool = False
    clear_glm_key: StrictBool = False

    @root_validator
    def keys(cls, values):
        for provider in ("qwen", "glm"):
            field = provider + "_key"
            secret = values.get(field)
            if secret is not None:
                value = secret.get_secret_value().strip()
                if value and (not re.fullmatch(r"[A-Za-z0-9_.-]{10,512}", value)
                              or (provider == "qwen" and not value.startswith("sk-"))):
                    raise ValueError("invalid credential format")
                if value and values.get("clear_" + field):
                    raise ValueError("cannot replace and clear the same credential")
                values[field] = SecretStr(value) if value else None
        return values


class GLMChat:
    api_host = "https://open.bigmodel.cn"
    provider = "glm"
    def __init__(self, key, *, proxy=None, timeout_seconds=60, transport=None):
        self.key, self.proxy, self.timeout, self.transport = key, proxy, timeout_seconds, transport

    def _post(self, path, payload):
        payload = dict(payload)
        payload.pop("enable_thinking", None)
        payload["thinking"] = {"type": "disabled"}
        try:
            with httpx.Client(timeout=self.timeout, proxy=self.proxy, transport=self.transport,
                              trust_env=False, follow_redirects=False) as client:
                response = client.post("https://open.bigmodel.cn/api/paas/v4/chat/completions",
                    json=payload, headers={"Authorization": "Bearer " + self.key})
        except httpx.HTTPError as exc:
            raise CloudModelError("GLM network request failed", service="GLM",
                code="timeout" if isinstance(exc, httpx.TimeoutException) else "network") from None
        if response.status_code != 200:
            raise CloudModelError("GLM request rejected", code="provider_http", service="GLM", status=response.status_code)
        try:
            body = response.json()
        except ValueError:
            raise CloudModelError("GLM invalid JSON", service="GLM") from None
        if not isinstance(body, dict):
            raise CloudModelError("GLM invalid response", service="GLM")
        return body


class ServiceSettings:
    def __init__(self, state, server_config, *, allow_cloud=False):
        self.path, self.state = state / "service-settings.json", state
        self.vault = CredentialStore(state)
        self.server_config, self.lock = server_config, threading.RLock()
        # Keep legacy key-file/proxy configuration as defaults. No key is read on page load.
        cloud = server_config.get("cloud_services", {})
        host = cloud.get("api_host", "")
        self.defaults = Preferences(cloud_enabled=allow_cloud,
            qwen_region="intl" if "-intl." in host else "us" if "-us." in host else "cn",
            timeout_seconds=int(cloud.get("timeout_seconds", 60)), proxy=cloud.get("proxy") or "")

    def _document(self):
        return read_json(self.path) if self.path.exists() else {"preferences": self.defaults.dict(), "credentials": {}}

    def preferences(self):
        with self.lock:
            return Preferences.parse_obj(self._document()["preferences"])

    def _status(self, provider, document):
        credentials = document["credentials"]
        if provider in credentials:
            return "saved" if credentials[provider] else "disabled"
        env = (os.getenv("DASHSCOPE_API_KEY") or os.getenv("EASYRAG_DASHSCOPE_KEY_FILE") or
               self.server_config.get("cloud_services", {}).get("key_file")) if provider == "qwen" else (
                   os.getenv("GLM_API_KEY") or os.getenv("ZHIPU_API_KEY"))
        return "environment" if env else "missing"

    def public(self):
        with self.lock:
            document = self._document()
            return {**Preferences.parse_obj(document["preferences"]).dict(),
                    "credentials": {p: self._status(p, document) for p in ("qwen", "glm")},
                    "storage": self.vault.storage}

    def save(self, request):
        with self.lock:
            document = self._document()
            credentials = dict(document["credentials"])
            for provider in ("qwen", "glm"):
                secret = getattr(request, provider + "_key")
                if getattr(request, "clear_" + provider + "_key"):
                    credentials[provider] = None  # Explicitly suppress legacy environment fallback.
                elif secret:
                    allow_create = not any(isinstance(value, str) and value.startswith(PREFIX)
                                           for value in credentials.values())
                    credentials[provider] = self.vault.seal(provider, secret.get_secret_value(), allow_create=allow_create)
            prefs = {name: getattr(request, name) for name in Preferences.__fields__}
            self.vault.write_settings(self.path, {"preferences": prefs, "credentials": credentials})
            return self.public()

    def _key(self, provider, document):
        if provider in document["credentials"]:
            sealed = document["credentials"][provider]
            if sealed:
                return self.vault.unseal(provider, sealed)
        elif provider == "qwen":
            try:
                location = self.server_config.get("cloud_services", {}).get("key_file")
                if location:
                    location = self.state.parents[1] / "src" / location
                return read_api_key(location)
            except CloudModelError:
                pass
        else:
            value = (os.getenv("GLM_API_KEY") or os.getenv("ZHIPU_API_KEY") or "").strip()
            if value:
                return value
        raise ConsoleProblem("credential_missing", f"{'Qwen' if provider == 'qwen' else 'GLM'} API Key 未配置",
                             "打开设置 → 模型服务，输入并保存 API Key。", service=provider)

    def runtime(self, retrieval, generation):
        # Resolve a consistent in-memory snapshot at admission; never save keys into job requests.
        with self.lock:
            document = self._document()
            prefs = Preferences.parse_obj(document["preferences"])
            needs_retrieval = retrieval.embedding.enabled or retrieval.reranker.enabled
            needs_chat = generation.enabled and generation.mode == "cloud"
            if (needs_retrieval or needs_chat) and not prefs.cloud_enabled:
                raise ConsoleProblem("cloud_disabled", "Cloud 模式未开启", "打开设置 → 通用，开启 Cloud 模式。")
            qwen = None
            if needs_retrieval or (needs_chat and not generation.model.startswith("glm-")):
                hosts = {"cn": "https://dashscope.aliyuncs.com", "intl": "https://dashscope-intl.aliyuncs.com",
                         "us": "https://dashscope-us.aliyuncs.com"}
                qwen = DashScopeModels(api_host=hosts[prefs.qwen_region], api_key=self._key("qwen", document),
                    proxy=prefs.proxy or None, timeout_seconds=prefs.timeout_seconds)
            chat = qwen
            if needs_chat and generation.model.startswith("glm-"):
                chat = GLMChat(self._key("glm", document), proxy=prefs.proxy or None, timeout_seconds=prefs.timeout_seconds)
            cache = CachedEmbeddings(qwen, self.state / ("embedding-" + prefs.qwen_region + ".sqlite")) if needs_retrieval else None
            return (qwen, cache), chat
