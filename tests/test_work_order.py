"""Work-order contracts, call boundaries and citation integrity; no live API calls."""

import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import jieba
from pydantic import ValidationError

from easyrag.domain.work_order import GenerationConfig, WorkOrderRecord, cited_statements
from easyrag.generation.cloud_chat import generate_json
from easyrag.generation.work_order import (WorkOrderGenerator, DraftValidationError,
    build_messages, extractive_draft, parse_model_draft, render_markdown, validate_pack)
from easyrag.retrieval.cloud_models import CloudModelError, CloudBackendUnavailable, DashScopeModels
from easyrag.retrieval.evidence_pack import fingerprint
from easyrag.retrieval.operations import OperationsRunner
from test_operations import node


class WorkOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        incident = json.loads((Path(__file__).resolve().parents[1] /
            "examples/incidents/checkoutservice-incident.v1.json").read_text(encoding="utf-8"))
        incident["retrieval_text"] = "NEVER_USE_PREBUILT_SENTINEL"
        cls.runner = OperationsRunner([
            node("runbook", "manual", "checkoutservice 延迟排查：先检查连接池等待时间，确认下游服务是否超时。"),
            node("case", "history", "checkoutservice 历史案例：连接池等待增加；该案例不能证明当前故障原因。",
                 source_incident_id="past", human_verified=True, verified_by="test-human",
                 verified_at="2023-08-20T00:00:00Z"),
            node("metric", "observed", "checkoutservice 当前窗口时延均值上升；该变化仅为观测，不等于因果。",
                 source_incident_id=incident["incident_id"], service="checkoutservice",
                 metric="checkoutservice_latency", window_start_utc="2023-08-21T07:25:00Z",
                 window_end_utc="2023-08-21T07:34:00Z"),
        ], tokenizer=jieba.Tokenizer(), stopwords=set())
        cls.incident = incident
        cls.record = asyncio.run(cls.runner.run("当前异常如何验证和处置", incident=incident))
        cls.pack = validate_pack(cls.record)

    def generator(self, **kwargs):
        return WorkOrderGenerator(config={"save_intermediates": False}, **kwargs)

    def draft(self):
        return extractive_draft(self.pack, GenerationConfig()).dict()

    def test_extractive_is_review_required_with_complete_trace_and_no_network(self):
        with patch("easyrag.generation.work_order.generate_json", side_effect=AssertionError("network")):
            result = asyncio.run(self.generator().run(self.record))
        parsed = WorkOrderRecord.parse_obj(result)
        self.assertEqual("draft", parsed.status)
        self.assertEqual("unconfirmed", parsed.root_cause_status)
        self.assertFalse(parsed.actions_executed)
        self.assertEqual("pending_human_review", parsed.review_status)
        self.assertTrue(parsed.draft.root_cause_candidates)
        self.assertEqual([], parsed.draft.proposed_actions)
        self.assertIsNone(parsed.draft.impact)
        self.assertEqual("passed", parsed.provenance["citation_integrity"])
        self.assertNotIn("HistoricalCase", parsed.draft.dict())

    def test_disabled_and_empty_pack_do_not_call_cloud_or_fabricate_draft(self):
        empty = asyncio.run(self.runner.run("checkoutservice", incident=self.incident,
            overrides={"sources": {"doc": False, "case": False, "metric": False}}))
        with patch("easyrag.generation.work_order.generate_json", side_effect=AssertionError("network")):
            disabled = asyncio.run(self.generator().run(self.record, overrides={"enabled": False, "mode": "cloud"}))
            no_evidence = asyncio.run(self.generator().run(empty, overrides={"mode": "cloud"}))
        self.assertEqual("generation_disabled", disabled["status"])
        self.assertEqual("insufficient_evidence", no_evidence["status"])
        self.assertIsNone(disabled["draft"])
        self.assertIsNone(no_evidence["work_order_id"])

    def test_tampered_pack_or_evaluation_field_is_rejected_before_model(self):
        changed = copy.deepcopy(self.record)
        changed["pack_id"] = "pack-" + "0" * 16
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            asyncio.run(self.generator().run(changed))
        changed = copy.deepcopy(self.record)
        changed["query_context"]["ground_truth"] = "SECRET_LABEL"
        with self.assertRaises(ValueError):
            asyncio.run(self.generator().run(changed))

    def test_prompt_uses_only_final_evidence_and_honors_rca_switch(self):
        record = asyncio.run(self.runner.run("checkoutservice", incident=self.incident,
            overrides={"pack": {"top_k": 1}}))
        pack = validate_pack(record)
        inputs = json.loads(build_messages(pack, GenerationConfig())[1]["content"])
        self.assertEqual(1, len(inputs["evidence"]))
        self.assertNotIn("routes", inputs)
        self.assertNotIn("query", inputs)
        self.assertNotIn("NEVER_USE_PREBUILT_SENTINEL", json.dumps(inputs))
        self.assertTrue(inputs["rca_candidates"])
        no_rca = json.loads(build_messages(pack, GenerationConfig(include_rca_candidates=False))[1]["content"])
        self.assertEqual([], no_rca["rca_candidates"])
        retrieval_off = asyncio.run(self.runner.run("checkoutservice", incident=self.incident,
            overrides={"query": {"use_rca": False}}))
        self.assertEqual([], json.loads(build_messages(validate_pack(retrieval_off), GenerationConfig())[1]["content"])["rca_candidates"])

    def test_input_budget_fails_without_truncation(self):
        with self.assertRaisesRegex(ValueError, "max_input_chars"):
            build_messages(self.pack, GenerationConfig(max_input_chars=1000))

    def test_unknown_id_fabricated_quote_missing_citation_and_extra_fields_rejected(self):
        for error in ("id", "quote", "missing", "confirmed", "extra"):
            draft = self.draft()
            if error == "id":
                draft["summary"]["citations"][0]["evidence_id"] = "ev-doc-" + "0" * 16
            elif error == "quote":
                draft["summary"]["citations"][0]["quote"] = "THIS QUOTE DOES NOT EXIST"
            elif error == "missing":
                draft["summary"]["citations"] = []
            elif error == "confirmed":
                draft["summary"]["text"] = "当前事件根因已确认。"
            else:
                draft["human_verified"] = True
            with self.assertRaises(DraftValidationError):
                parse_model_draft(draft, [x.evidence for x in self.pack.pack.items], GenerationConfig())

    def test_retrieved_but_unpacked_evidence_cannot_be_cited(self):
        small = validate_pack(asyncio.run(self.runner.run("checkoutservice", incident=self.incident,
            overrides={"pack": {"top_k": 1}})))
        draft = extractive_draft(small, GenerationConfig()).dict()
        chosen = {x.evidence.evidence_id for x in small.pack.items}
        dropped = next(x.evidence for x in self.pack.pack.items if x.evidence.evidence_id not in chosen)
        draft["summary"]["citations"] = [{"evidence_id": dropped.evidence_id, "quote": dropped.content}]
        with self.assertRaisesRegex(DraftValidationError, "citation_not_in_final_pack"):
            parse_model_draft(draft, [x.evidence for x in small.pack.items], GenerationConfig())

    def test_actions_require_risk_rollback_and_approval(self):
        draft = self.draft()
        doc = next(x.evidence for x in self.pack.pack.items if x.evidence.type == "doc")
        action = {"text": "验证后评审处置方案", "citations": [{"evidence_id": doc.evidence_id, "quote": doc.content}],
                  "preconditions": "验证适用版本、下游超时和可用回退方案", "risks": ["可能影响业务"], "rollback": "预先确认回退方案",
                  "requires_approval": True, "execution_status": "not_executed"}
        draft["proposed_actions"] = [action]
        parse_model_draft(draft, [x.evidence for x in self.pack.pack.items], GenerationConfig())
        for field, value in (("requires_approval", False), ("execution_status", "executed"), ("risks", [])):
            bad = copy.deepcopy(draft)
            bad["proposed_actions"][0][field] = value
            with self.assertRaises(DraftValidationError):
                parse_model_draft(bad, [x.evidence for x in self.pack.pack.items], GenerationConfig())

    def test_missing_impact_scope_and_case_only_actions_are_rejected(self):
        draft = self.draft()
        draft["impact"] = draft["summary"]
        with self.assertRaises(DraftValidationError):
            parse_model_draft(draft, [x.evidence for x in self.pack.pack.items], GenerationConfig())
        draft = self.draft()
        case = next(x.evidence for x in self.pack.pack.items if x.evidence.type == "case")
        draft["proposed_actions"] = [{"text": "照搬历史处置", "preconditions": "条件尚待验证",
            "citations": [{"evidence_id": case.evidence_id, "quote": case.content}],
            "risks": ["可能不适用"], "rollback": "评审回退方案"}]
        with self.assertRaisesRegex(DraftValidationError, "require runbook"):
            parse_model_draft(draft, [x.evidence for x in self.pack.pack.items], GenerationConfig())

    def test_generated_executable_shell_commands_and_blocks_are_rejected(self):
        commands = (
            "在redis Pod中执行'kubectl exec -it redis-xxx -- redis-cli info | grep used_memory'。",
            "调用 `kubectl get pods -n online-boutique` 确认实例状态。",
            "redis-cli INFO persistence",
            "运行 curl --fail https://example.com/health 查看结果。",
            "执行 Get-Content -LiteralPath C:/logs/service.log。",
            "执行 bash -c 'echo ready'。",
            "执行 rm -rf /tmp/cache。",
            "运行 docker restart redis 进行修复。",
            "缺少 kubectl debug 官方手册；随后运行 docker restart redis。",
            "执行 systemctl restart redis。",
            "执行 helm upgrade redis ./chart。",
            '执行 python -c "print(1)"。',
            "```shell\nkubectl logs redis-xxx\n```",
            "~~~powershell\nGet-Process\n~~~",
            '```python\nimport os\nos.system("echo ready")\n```',
            "#!/bin/sh\necho ready",
        )
        for command in commands:
            with self.subTest(command=command):
                draft = self.draft()
                draft["verification_steps"][0]["text"] = command
                with self.assertRaisesRegex(DraftValidationError, "shell"):
                    parse_model_draft(draft, [x.evidence for x in self.pack.pack.items], GenerationConfig())

    def test_shell_command_guard_covers_all_model_narrative_fields(self):
        base = self.draft()
        doc = next(x.evidence for x in self.pack.pack.items if x.evidence.type == "doc")
        base["proposed_actions"] = [{"text": "验证后评审处置方案",
            "citations": [{"evidence_id": doc.evidence_id, "quote": doc.content}],
            "preconditions": "确认适用条件", "risks": ["可能影响业务"], "rollback": "评审回退方案"}]
        fields = (("summary", "text"), ("root_cause_candidates", 0, "statement", "text"),
            ("root_cause_candidates", 0, "verification"), ("verification_steps", 0, "text"),
            ("proposed_actions", 0, "text"), ("proposed_actions", 0, "preconditions"),
            ("proposed_actions", 0, "risks", 0), ("proposed_actions", 0, "rollback"),
            ("missing_information", 0), ("limitations", 0))
        for field in fields:
            with self.subTest(field=field):
                draft = copy.deepcopy(base)
                parent = draft
                for key in field[:-1]:
                    parent = parent[key]
                parent[field[-1]] = "kubectl exec redis-xxx -- redis-cli info"
                with self.assertRaisesRegex(DraftValidationError, "shell"):
                    parse_model_draft(draft, [x.evidence for x in self.pack.pack.items], GenerationConfig())

    def test_shell_commands_in_source_quotes_are_preserved(self):
        doc = next(x.evidence for x in self.pack.pack.items if x.evidence.type == "doc")
        quotes = ("原始手册示例：\n```shell\nkubectl debug node/example -it --image=ubuntu:latest\n```",
                  '原始手册示例：\n```python\nimport os\nos.system("docker restart redis")\n```',
                  "原始手册示例：\n```powershell\nGet-Process\n```")
        for quote in quotes:
            with self.subTest(quote=quote):
                evidence = [x.evidence.copy(update={"content": quote}) if x.evidence.evidence_id == doc.evidence_id
                            else x.evidence for x in self.pack.pack.items]
                draft = self.draft()
                draft["verification_steps"] = [{"text": "核对所引手册版本和适用环境，先制定验证方案。",
                    "citations": [{"evidence_id": doc.evidence_id, "quote": quote}]}]
                parsed = parse_model_draft(draft, evidence, GenerationConfig())
                self.assertEqual(quote, parsed.verification_steps[0].citations[0].quote)

    def test_normal_diagnostic_prose_and_tool_names_are_allowed(self):
        descriptions = ("需要检查CPU、查询Pod日志；核对 kubectl 工具版本与 redis-cli 的适用环境。",
            "The kubectl debug documentation does not establish whether Redis persistence is abnormal.",
            "缺少适用版本的 kubectl debug 官方手册，需要补充后再设计验证步骤。",
            "先核对 docker restart 官方手册、systemctl restart 文档以及 Python 工具版本。",
            "The kubectl debug command is documented, but its applicability to this Redis incident remains unverified.",
            "kubectl debug helps troubleshoot pods; it does not establish a root cause.",
            "redis-cli INFO 命令用于查看服务器状态，当前还缺少适用手册。")
        for text in descriptions:
            with self.subTest(text=text):
                draft = self.draft()
                draft["verification_steps"][0]["text"] = text
                parsed = parse_model_draft(draft, [x.evidence for x in self.pack.pack.items], GenerationConfig())
                self.assertEqual(text, parsed.verification_steps[0].text)

    def test_cloud_documentation_description_is_accepted(self):
        draft = self.draft()
        text = "缺少适用版本的 kubectl debug 官方手册，需要补充后再设计验证步骤。"
        draft["verification_steps"][0]["text"] = text
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(draft, ensure_ascii=False)}}], "usage": {"total_tokens": 123}}
        models = DashScopeModels(api_key="unit-test-no-network",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
        result = asyncio.run(self.generator(models=models).run(self.record, overrides={"mode": "cloud"}))
        self.assertEqual("draft", result["status"])
        self.assertEqual(text, result["draft"]["verification_steps"][0]["text"])

    def test_cloud_shell_output_fails_without_saving_a_work_order(self):
        draft = self.draft()
        draft["verification_steps"][0]["text"] = "kubectl exec redis-xxx -- redis-cli info"
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(draft, ensure_ascii=False)}}], "usage": {"total_tokens": 123}}
        models = DashScopeModels(api_key="unit-test-no-network",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
        with tempfile.TemporaryDirectory() as temporary:
            generator = WorkOrderGenerator(models=models, output_dir=temporary)
            with self.assertRaisesRegex(DraftValidationError, "shell"):
                asyncio.run(generator.run(self.record, overrides={"mode": "cloud"}))
            files = list(Path(temporary).glob("*/*"))
            self.assertEqual(["audit.jsonl"], [path.name for path in files])
            events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual("generation_failed", events[-1]["event"])

    def test_missing_backend_explicit_and_failed_run_logs_no_provider_text(self):
        with self.assertRaises(CloudBackendUnavailable):
            asyncio.run(self.generator().run(self.record, overrides={"mode": "cloud"}))
        with tempfile.TemporaryDirectory() as temporary:
            generator = WorkOrderGenerator(models=DashScopeModels(), output_dir=temporary)
            with patch("easyrag.generation.work_order.generate_json", side_effect=CloudModelError("PRIVATE_ERROR_MARKER")):
                with self.assertRaises(CloudModelError):
                    asyncio.run(generator.run(self.record, overrides={"mode": "cloud"}))
            files = list(Path(temporary).glob("*/*"))
            self.assertEqual(["audit.jsonl"], [f.name for f in files])
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("generation_failed", text)
            self.assertNotIn("PRIVATE_ERROR_MARKER", text)

    def test_repeated_runs_save_unique_artifacts_with_stable_content_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            generator = WorkOrderGenerator(output_dir=temporary)
            a, b = (asyncio.run(generator.run(self.record)) for _ in range(2))
            self.assertNotEqual(a["generation_run_id"], b["generation_run_id"])
            self.assertEqual(a["work_order_id"], b["work_order_id"])
            folder = Path(temporary) / a["generation_run_id"]
            self.assertEqual(a, json.loads((folder / "work-order.json").read_text(encoding="utf-8")))
            self.assertEqual(self.record, json.loads((folder / "evidence-pack.json").read_text(encoding="utf-8")))
            self.assertIn("待人工审核", (folder / "work-order.md").read_text(encoding="utf-8"))
            self.assertTrue((folder / "audit.jsonl").exists())
            self.assertFalse((folder / "prompt.json").exists())
            from verify_work_order import audit
            report = audit(folder)
            self.assertEqual("passed", report["citation_integrity"])
            self.assertFalse(report["actions_executed"])
            altered = copy.deepcopy(a)
            altered["draft"]["summary"]["text"] = "手动更改而未记录的正文"
            (folder / "work-order.json").write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                audit(folder)

    def test_valid_cloud_schema_and_prompt_provenance_saved(self):
        with tempfile.TemporaryDirectory() as temporary, patch("easyrag.generation.work_order.generate_json",
                return_value=(self.draft(), {"total_tokens": 123})) as call:
            generator = WorkOrderGenerator(models=DashScopeModels(), output_dir=temporary)
            result = asyncio.run(generator.run(self.record, overrides={"mode": "cloud"}))
            self.assertEqual(1, call.call_count)
            messages = json.loads((Path(temporary) / result["generation_run_id"] / "prompt.json").read_text(encoding="utf-8"))
            self.assertEqual(fingerprint(messages), result["provenance"]["prompt_sha256"])
            self.assertEqual({"total_tokens": 123}, result["provenance"]["usage"])
            self.assertNotIn("key_file", json.dumps(result))

    def test_opt_in_repair_is_bounded_audited_and_counts_all_usage(self):
        invalid = self.draft()
        invalid["summary"]["citations"][0]["quote"] = "THIS QUOTE DOES NOT EXIST"
        valid = self.draft()
        with patch("easyrag.generation.work_order.generate_json", side_effect=[(invalid, {"total_tokens": 11}),
                   (valid, {"total_tokens": 22})]) as call:
            result = asyncio.run(self.generator(models=DashScopeModels()).run(self.record,
                overrides={"mode": "cloud", "repair_attempts": 1}))
        self.assertEqual(2, call.call_count)
        self.assertEqual(33, result["provenance"]["usage"]["total_tokens"])
        self.assertEqual(["rejected", "accepted_by_structural_checks"],
                         [x["outcome"] for x in result["provenance"]["attempts"]])
        with patch("easyrag.generation.work_order.generate_json", return_value=(invalid, {})) as call:
            with self.assertRaises(DraftValidationError):
                asyncio.run(self.generator(models=DashScopeModels()).run(self.record,
                    overrides={"mode": "cloud", "repair_attempts": 1}))
            self.assertEqual(2, call.call_count)

    def test_disabled_save_and_request_overrides_do_not_mutate_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            generator = self.generator(output_dir=temporary)
            asyncio.run(generator.run(self.record, overrides={"include_rca_candidates": False}))
            self.assertTrue(generator.defaults.include_rca_candidates)
            self.assertEqual([], list(Path(temporary).iterdir()))
        for invalid in ({"enabled": "false"}, {"api_host": "https://evil.example"}, {"max_steps": True}):
            with self.assertRaises(ValidationError):
                GenerationConfig().with_overrides(invalid)

    def test_markdown_escapes_untrusted_html_and_images(self):
        record = asyncio.run(self.generator().run(self.record))
        record["draft"]["summary"]["text"] = '<script>alert(1)</script> ![tracking](https://example.com/image)'
        markdown = render_markdown(WorkOrderRecord.parse_obj(record))
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("![tracking]", markdown)
        self.assertIn("&lt;script&gt;", markdown)


class JsonChatTests(unittest.TestCase):
    def call(self, response):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual("json_object", body["response_format"]["type"])
            self.assertFalse(body["enable_thinking"])
            self.assertNotIn("tools", body)
            return httpx.Response(200, json=response)
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-unit-test-not-a-real-secret"}):
            return generate_json(DashScopeModels(transport=httpx.MockTransport(handler)),
                                 [{"role": "user", "content": "JSON"}], GenerationConfig(mode="cloud"))

    def test_json_mode_success_and_usage_allowlist(self):
        result, usage = self.call({"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}],
                                  "usage": {"prompt_tokens": 20, "total_tokens": 30, "internal_secret": "NEVER"}})
        self.assertEqual({"ok": True}, result)
        self.assertEqual({"prompt_tokens": 20, "total_tokens": 30}, usage)

    def test_truncated_invalid_duplicate_and_nonfinite_responses_fail_closed(self):
        for text, finish in (("{}", "length"), ("```json\n{}\n```", "stop"), ('{"x":1,"x":2}', "stop"),
                             ('{"x":NaN}', "stop"), ("[]", "stop")):
            with self.assertRaises(CloudModelError):
                self.call({"choices": [{"finish_reason": finish, "message": {"content": text}}]})


if __name__ == "__main__":
    unittest.main()
