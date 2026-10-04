"""Local console boundaries and real offline pipeline; all writes use a temp root."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import yaml
import httpx

from easyrag.console.server import create_app, SignalOptions
from easyrag.console.store import write_json, read_json
from easyrag.console.settings import ServiceSettings, GLMChat
from easyrag.retrieval.cloud_models import CloudModelError, DashScopeModels
from easyrag.console.diagnostics import describe_error, rca_problem
from easyrag.domain.experiment import ExperimentConfig
from easyrag.domain.work_order import GenerationConfig
from easyrag.generation.cloud_chat import generate_json
from easyrag.domain.work_order import cited_statements, WorkOrderDraft
from test_signal_bundle import fixture

ROOT = Path(__file__).resolve().parents[1]


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        settings = yaml.safe_load((ROOT / "src/configs/easyrag.operations.windows.yaml").read_text(encoding="utf-8"))
        settings["operations_output_dir"] = str(self.root / "outputs/operations")
        write_json(self.root / "src/configs/easyrag.operations.windows.yaml", settings)
        shutil.copytree(ROOT / "examples/operations_knowledge", self.root / "examples/operations_knowledge")
        self.app = create_app(self.root)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.client.__enter__()
        self.boot = self.client.get("/api/bootstrap").json()
        self.headers = {"X-Console-Token": self.boot["token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.headers)

    def imported(self):
        response = self.post("/api/events", fixture())
        self.assertEqual(201, response.status_code, response.text)
        return response.json()["event_id"]

    def body(self):
        return {"event_id": self.imported(), "query": "checkoutservice 如何验证",
                "retrieval": self.boot["retrieval"], "generation": self.boot["generation"]}

    def completed(self, response):
        self.assertEqual(202, response.status_code, response.text)
        ident = response.json()["id"]
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            job = self.client.get("/api/jobs/" + ident).json()
            if job["status"] not in {"queued", "running"}:
                self.assertEqual("completed", job["status"], job)
                return job
            time.sleep(.05)
        self.fail("job did not finish")

    def test_local_csrf_origin_host_and_safe_capabilities(self):
        self.assertFalse(self.boot["cloud_enabled"])
        text = json.dumps(self.boot)
        for key in ("api_key", "key_file", "llm_keys", str(self.root)):
            self.assertNotIn(key, text)
        self.assertEqual(403, self.client.post("/api/events", json=fixture()).status_code)
        self.assertEqual(403, self.client.get("/api/bootstrap", headers={"Origin": "https://evil.example"}).status_code)
        self.assertEqual(403, self.client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code)
        self.assertEqual(403, self.client.get("/api/bootstrap", headers={"Sec-Fetch-Site": "cross-site"}).status_code)
        response = self.client.get("/api/events")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual(404, self.client.get("/api/unknown").status_code)
        self.assertEqual(422, self.client.get("/api/orders/not-a-path").status_code)

    def test_strict_json_and_import_validation(self):
        for content in ('{"x":NaN}', '{"x":1,"x":2}', '[]'):
            response = self.client.post("/api/events", content=content,
                headers={**self.headers, "Content-Type": "application/json"})
            self.assertEqual(422, response.status_code)
        response = self.client.post("/api/events", content='{}', headers=self.headers)
        self.assertEqual(415, response.status_code)
        bad = fixture(); bad["ground_truth"] = "answer"
        self.assertEqual(422, self.post("/api/events", bad).status_code)
        ident = self.imported()
        items = self.client.get("/api/events").json()["items"]
        self.assertEqual(ident, items[0]["id"])
        self.assertEqual([], self.client.get("/api/events/" + ident).json()["rca_runs"])
        incident = self.client.get("/api/events/" + ident).json()
        raw = (self.root / "outputs/signal_imports" / ident / "signal-bundle.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), incident["source_refs"][0]["sha256"])

    def test_config_validation_and_cloud_opt_in_before_queue(self):
        body = self.body()
        bad = copy.deepcopy(body); bad["retrieval"]["sources"]["case"] = "false"
        self.assertEqual(422, self.post("/api/runs", bad).status_code)
        bad = copy.deepcopy(body); bad["generation"]["mode"] = "cloud"
        self.assertEqual(503, self.post("/api/runs", bad).status_code)
        bad = copy.deepcopy(body); bad["retrieval"]["save_intermediates"] = False
        self.assertEqual(422, self.post("/api/runs", bad).status_code)
        self.assertEqual([], self.client.get("/api/jobs").json())
        settings = {"retrieval": body["retrieval"], "generation": body["generation"]}
        self.assertEqual(200, self.post("/api/validate-config", settings).status_code)
        settings["cloud_services"] = {"enabled": True}
        self.assertEqual(422, self.post("/api/validate-config", settings).status_code)

    def test_real_offline_run_review_lineage_downloads_and_no_case_promotion(self):
        body = self.body()
        job = self.completed(self.post("/api/runs", body))
        ident = job["result"]["generation_run_id"]
        detail = self.client.get("/api/orders/" + ident).json()
        self.assertTrue(detail["compatible"])
        self.assertTrue(detail["record"]["evidence"])
        self.assertEqual("passed", detail["record"]["provenance"]["citation_integrity"])
        path = self.root / "outputs/work_orders" / ident / "work-order.json"
        original = path.read_bytes()
        draft = detail["record"]["draft"]
        draft["summary"]["text"] = "测试修订：只核对指标观测，不确认根因。"
        count = len(list(cited_statements(WorkOrderDraft.parse_obj(draft))))
        review = {"draft": draft, "reviewer": "TEST_ONLY_NOT_HUMAN_REVIEW", "decision": "needs_revision",
            "notes": "自动化测试记录；不能作为科研人工标注。", "claims": [
                {"claim_index": i, "support": "uncertain"} for i in range(count)]}
        response = self.post(f"/api/orders/{ident}/reviews", review)
        self.assertEqual(201, response.status_code, response.text)
        revision = response.json()
        self.assertEqual(False, revision["case_promoted"])
        self.assertEqual(False, revision["actions_executed"])
        self.assertEqual("unconfirmed", revision["root_cause_status"])
        self.assertEqual(original, path.read_bytes())
        review["parent_revision_id"] = revision["revision_id"]
        second = self.post(f"/api/orders/{ident}/reviews", review).json()
        self.assertNotEqual(second["revision_id"], revision["revision_id"])
        self.assertNotEqual(second["source_record_sha256"], second["parent_sha256"])
        for suffix in ("download/json", "download/md", f"reviews/{second['revision_id']}/download/md"):
            self.assertEqual(200, self.client.get(f"/api/orders/{ident}/{suffix}").status_code)
        review["decision"] = "reviewed_draft"
        self.assertEqual(422, self.post(f"/api/orders/{ident}/reviews", review).status_code)
        review["decision"] = "needs_revision"
        review["draft"]["summary"]["citations"][0]["quote"] = "FAKE_QUOTATION_NOT_IN_EVIDENCE"
        self.assertEqual(422, self.post(f"/api/orders/{ident}/reviews", review).status_code)
        self.assertEqual(1, len(self.client.get("/api/orders").json()))
        # Locally tampered source text cannot acquire a valid review lineage.
        changed = read_json(path)
        changed["draft"]["summary"]["text"] = "Tampered original"
        write_json(path, changed, replace=True)
        self.assertEqual(422, self.post(f"/api/orders/{ident}/reviews", review).status_code)

    def test_generation_and_sources_switches_take_effect(self):
        body = self.body(); body["generation"]["enabled"] = False
        job = self.completed(self.post("/api/runs", body))
        self.assertEqual("generation_disabled", job["result"]["status"])
        body["generation"]["enabled"] = True
        body["retrieval"]["sources"] = {"doc": False, "case": False, "metric": False}
        job = self.completed(self.post("/api/runs", body))
        self.assertEqual("insufficient_evidence", job["result"]["status"])
        self.assertEqual(0, job["result"]["evidence_count"])

    def test_failed_job_sanitizes_library_error(self):
        with patch("easyrag.console.server.build_runner", side_effect=RuntimeError("PRIVATE_SECRET_TEST")):
            response = self.post("/api/runs", self.body())
            ident = response.json()["id"]
            for _ in range(100):
                job = self.client.get("/api/jobs/" + ident).json()
                if job["status"] == "failed":
                    break
                time.sleep(.02)
        self.assertEqual("failed", job["status"])
        self.assertNotIn("PRIVATE_SECRET_TEST", json.dumps(job))

    def test_signal_switch_coherence(self):
        self.assertTrue(SignalOptions().profile()["rca"]["enabled"])
        for opts in ({"detection_enabled": False}, {"trigger_timestamp": 12}, {"baro": False}):
            with self.assertRaises(ValueError):
                SignalOptions(**opts).profile()
        opts = SignalOptions(detection_enabled=False, trigger_timestamp=1700000100, rca_enabled=False)
        self.assertFalse(opts.profile()["rca"]["enabled"])
        self.assertEqual("operator", opts.profile()["trigger"]["source"])

    def test_detection_parameters_reach_rca_profile(self):
        options = SignalOptions(warmup_points=120, consecutive_points=5,
                                minimum_metrics=2, max_gap_seconds=10)
        detection = options.profile()["detection"]
        self.assertEqual(120, detection["warmup_points"])
        self.assertEqual(5, detection["consecutive_points"])
        self.assertEqual(2, detection["minimum_metrics"])
        self.assertEqual(10, detection["max_gap_seconds"])
        self.assertEqual(300, self.boot["signal_options"]["warmup_points"])

    def test_invalid_detection_parameters_are_rejected_before_job_admission(self):
        for field, invalid in (("warmup_points", 19), ("consecutive_points", 0),
                               ("minimum_metrics", True), ("max_gap_seconds", 1.5)):
            response = self.post("/api/telemetry", {"csv": "time,value\n1,2\n",
                "options": {field: invalid}})
            self.assertEqual(422, response.status_code, response.text)
        self.assertEqual([], self.client.get("/api/jobs").json())

    def test_restart_marks_pending_jobs_interrupted(self):
        ident = "job-" + "a" * 32
        write_json(self.root / "outputs/console/jobs" / ident / "job.json", {"id": ident, "status": "running"})
        self.app.state.queue.recover()
        self.assertEqual("interrupted", self.client.get("/api/jobs/" + ident).json()["status"])

    def preferences(self, **changes):
        public = self.client.get("/api/settings").json()
        return {**{k: v for k, v in public.items() if k not in {"credentials", "storage"}}, **changes}

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_settings_encrypted_persistent_write_only_and_clear_blocks_environment(self):
        key = "sk-TEST_ONLY_QWEN_SECRET_123456"
        body = self.preferences(cloud_enabled=True, qwen_key=key, glm_key="TEST_ONLY_GLM_SECRET_123456",
                                generation_provider="glm")
        self.assertEqual(403, self.client.post("/api/settings", json=body).status_code)
        response = self.post("/api/settings", body)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("saved", response.json()["credentials"]["qwen"])
        raw = (self.root / "outputs/console/service-settings.json").read_text()
        for value in (key, "TEST_ONLY_GLM_SECRET_123456"):
            self.assertNotIn(value, raw)
            for path in ("/api/settings", "/api/bootstrap", "/api/logs", "/api/jobs"):
                self.assertNotIn(value, self.client.get(path).text)
        services = ServiceSettings(self.root / "outputs/console", {})
        retrieval = ExperimentConfig.parse_obj({"retrieval": {"mode": "dense"}, "embedding": {"enabled": True}})
        runtime, chat = services.runtime(retrieval, GenerationConfig(mode="cloud", model="glm-4.7"))
        self.assertEqual(key, runtime[0]._api_key)
        self.assertIsInstance(chat, GLMChat)
        self.assertTrue(services.public()["cloud_enabled"])
        # Blank password inputs preserve existing keys.
        self.assertEqual(200, self.post("/api/settings", self.preferences(qwen_key="")).status_code)
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-ENVIRONMENT_TEST_SECRET"}):
            self.post("/api/settings", self.preferences(clear_qwen_key=True))
            self.assertEqual("disabled", services.public()["credentials"]["qwen"])
            with self.assertRaisesRegex(ValueError, "API Key"):
                services.runtime(retrieval, GenerationConfig(enabled=False))
        # An already admitted task retains its original snapshot.
        self.assertEqual(key, runtime[0]._api_key)

    def test_settings_validation_cloud_gate_missing_key_and_log_details(self):
        for changes in ({"proxy": "https://evil.example"}, {"timeout_seconds": 0}, {"glm_key": "bad\nkey"}):
            response = self.post("/api/settings", self.preferences(**changes))
            self.assertEqual(422, response.status_code)
            self.assertEqual("validation", response.json()["detail"]["code"])
        self.post("/api/settings", self.preferences(cloud_enabled=True, clear_glm_key=True))
        body = self.body(); body["generation"].update(mode="cloud", model="glm-4.7")
        response = self.post("/api/runs", body)
        self.assertEqual("credential_missing", response.json()["detail"]["code"])
        self.assertEqual([], self.client.get("/api/jobs").json())
        self.post("/api/settings", self.preferences(cloud_enabled=False))
        response = self.post("/api/runs", body)
        self.assertEqual(503, response.status_code)
        self.assertEqual("cloud_disabled", response.json()["detail"]["code"])
        entries = self.client.get("/api/logs").json()
        self.assertTrue(any(e["problem"] and e["problem"]["code"] == "credential_missing" for e in entries))
        self.assertTrue(any(e["problem"] and e["problem"].get("details") for e in entries))

    def test_provider_errors_keep_http_service_and_phase_without_response_body(self):
        body = self.body()
        with patch("easyrag.console.server.build_runner", side_effect=CloudModelError(
                "DO_NOT_EXPOSE_PROVIDER_BODY", code="provider_http", service="GLM", status=401)):
            response = self.post("/api/runs", body)
            ident = response.json()["id"]
            for _ in range(100):
                job = self.client.get("/api/jobs/" + ident).json()
                if job["status"] == "failed": break
                time.sleep(.02)
        self.assertEqual(401, job["problem"]["http_status"])
        self.assertEqual("GLM", job["problem"]["service"])
        self.assertEqual("建立事件索引与检索", job["problem"]["phase"])
        logs = self.client.get("/api/logs").text
        self.assertIn(ident, logs)
        self.assertNotIn("DO_NOT_EXPOSE_PROVIDER_BODY", logs + json.dumps(job))

    def test_glm_transport_routing_format_and_error_sanitization(self):
        captured = []
        def transport(request):
            captured.append(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
        models = GLMChat("FAKE_GLM_KEY", transport=httpx.MockTransport(transport))
        value, _ = generate_json(models, [{"role": "user", "content": "test"}], GenerationConfig(model="glm-4.7"))
        self.assertTrue(value["ok"])
        self.assertEqual("open.bigmodel.cn", captured[0].url.host)
        payload = json.loads(captured[0].content)
        self.assertNotIn("enable_thinking", payload)
        self.assertEqual({"type": "disabled"}, payload["thinking"])
        self.assertEqual("json_object", payload["response_format"]["type"])
        for status in (401, 403, 429, 503):
            models.transport = httpx.MockTransport(lambda _: httpx.Response(status, text="PRIVATE_PROVIDER_BODY"))
            with self.assertRaises(CloudModelError) as caught:
                models._post("", {})
            problem = describe_error(caught.exception)
            self.assertEqual(status, problem["http_status"])
            self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(problem))

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_console_uses_saved_glm_credential_for_grounded_generation(self):
        body = self.body()
        offline = self.completed(self.post("/api/runs", body))
        original = self.client.get("/api/orders/" + offline["result"]["generation_run_id"]).json()
        self.post("/api/settings", self.preferences(cloud_enabled=True, generation_provider="glm",
            glm_key="TEST_GLM_PIPELINE_KEY_123456"))
        body["generation"].update(mode="cloud", model="glm-4.7")
        captured = []
        def post(models, path, payload):
            captured.append((models.key, payload["model"]))
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(original["record"]["draft"])}}]}
        with patch.object(GLMChat, "_post", post):
            job = self.completed(self.post("/api/runs", body))
        record = self.client.get("/api/orders/" + job["result"]["generation_run_id"]).json()["record"]
        self.assertEqual([("TEST_GLM_PIPELINE_KEY_123456", "glm-4.7")], captured)
        self.assertEqual("https://open.bigmodel.cn", record["provenance"]["api_host"])
        self.assertEqual("passed", record["provenance"]["citation_integrity"])
        for path in ("/api/logs", "/api/jobs", "/api/orders/" + job["result"]["generation_run_id"]):
            self.assertNotIn("TEST_GLM_PIPELINE_KEY_123456", self.client.get(path).text)

    def test_csv_and_network_diagnostics_are_actionable_and_do_not_echo_input(self):
        problem = describe_error(rca_problem(b"ValueError: timestamp invalid SECRET_CSV_ROW"))
        self.assertIn("time", problem["hint"])
        self.assertNotIn("SECRET_CSV_ROW", json.dumps(problem))
        def timeout(_): raise httpx.ReadTimeout("PRIVATE_PROXY_CREDENTIAL")
        models = DashScopeModels(api_key="sk-FAKE_TEST_CREDENTIAL", transport=httpx.MockTransport(timeout))
        with self.assertRaises(CloudModelError) as caught: models._post("/test", {})
        problem = describe_error(caught.exception)
        self.assertEqual("timeout", problem["code"])
        self.assertNotIn("PRIVATE_PROXY_CREDENTIAL", json.dumps(problem))


if __name__ == "__main__":
    unittest.main()
