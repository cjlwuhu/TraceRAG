"""流水线的真实离线产物、子进程边界及事件隔离回归。"""

import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from test_signal_bundle import fixture, signed
from verify_work_order import audit


ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pipeline test ")
        self.home = Path(self.temp.name)
        self.bundle = self.home / "signal input.json"
        self.bundle.write_text(json.dumps(fixture(), ensure_ascii=False), encoding="utf-8")
        self.outputs = self.home / "outputs"

    def tearDown(self):
        self.temp.cleanup()

    def api(self):
        # 明确把“尚未实现”报告为失败，红灯阶段不让导入异常掩盖测试意图。
        self.assertIsNotNone(importlib.util.find_spec("easyrag.orchestration"), "缺少单命令流水线")
        from easyrag.orchestration.pipeline import run_pipeline, check_rca
        return run_pipeline, check_rca

    def run_bundle(self, **kwargs):
        run, _ = self.api()
        return asyncio.run(run(bundle=self.bundle, output_root=self.outputs,
            query="checkoutservice_latency 如何验证", **kwargs))

    def test_bundle_creates_local_artifacts_and_passes_independent_audit(self):
        # 错误的目录配置或省略审计会破坏本次运行的可复现产物。
        report = self.run_bundle()
        self.assertEqual("complete", report["status"])
        folder = self.outputs / report["pipeline_id"]
        self.assertEqual(report, json.loads((folder / "pipeline.json").read_text(encoding="utf-8")))
        order_folder = folder / report["artifacts"]["work_order_directory"]
        checked = audit(order_folder)
        self.assertEqual("passed", checked["citation_integrity"])
        self.assertFalse(checked["actions_executed"])
        self.assertEqual("unconfirmed", json.loads((order_folder / "work-order.json").read_text(encoding="utf-8"))["root_cause_status"])
        self.assertEqual(hashlib.sha256(self.bundle.read_bytes()).hexdigest(), report["fingerprints"]["input_sha256"])
        for path in report["artifacts"].values():
            self.assertFalse(Path(path).is_absolute())
        self.assertNotIn(str(self.home), json.dumps(report))
        self.assertFalse((self.home / "knowledge/active.json").exists())

    def test_base_manifest_excludes_other_events_systems_and_teaching_cases(self):
        # 若直接合并整份历史 manifest，则会把其他事件或教学根因泄漏到新工单。
        documents = load_manifest(ROOT / "examples/operations_knowledge/manifest.jsonl")
        runbook = next(d for d in documents if d.knowledge_type == "runbook").to_dict()
        foreign = copy.deepcopy(runbook)
        foreign["knowledge_id"] = "runbook-other-system"
        foreign["metadata"]["system"] = "other-system"
        from easyrag.domain.knowledge import KnowledgeDocument
        documents.append(KnowledgeDocument.from_dict(foreign))
        manifest = self.home / "base.jsonl"
        write_manifest(documents, manifest)
        report = self.run_bundle(base_manifest=manifest)
        selected = load_manifest(self.outputs / report["pipeline_id"] / report["artifacts"]["corpus_manifest"])
        self.assertEqual({"metric", "runbook"}, {d.knowledge_type for d in selected})
        self.assertNotIn("runbook-other-system", {d.knowledge_id for d in selected})
        self.assertEqual(["metric-fixture"], [d.knowledge_id for d in selected if d.knowledge_type == "metric"])
        self.assertEqual(1, report["corpus"]["excluded_synthetic_cases"])

    def test_no_event_and_incomplete_window_never_retrieve(self):
        for status, expected in (("no_event", "no_event"), ("awaiting_observation_window", "awaiting")):
            with self.subTest(status=status):
                value = fixture()
                value["analysis"]["status"] = status
                value["metric_summaries"] = []
                if status == "no_event":
                    value["incident_id"] = None
                    value["detection"] = {"status": "no_alarm", "source": "anomaly_detector", "timestamp_unix": None}
                self.bundle.write_text(json.dumps(signed(value)), encoding="utf-8")
                with patch("run_operations.build_runner", side_effect=AssertionError("不应检索")):
                    report = self.run_bundle()
                self.assertEqual(expected, report["status"])
                self.assertNotIn("work_order_directory", report["artifacts"])
                self.assertFalse((self.outputs / report["pipeline_id"] / "work_orders").exists())

    def test_repeated_runs_preserve_previous_artifacts(self):
        first = self.run_bundle()
        path = self.outputs / first["pipeline_id"] / "pipeline.json"
        before = path.read_bytes()
        second = self.run_bundle()
        self.assertNotEqual(first["pipeline_id"], second["pipeline_id"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(first["retrieval"]["pack_id"], second["retrieval"]["pack_id"])

    def test_missing_rca_has_actionable_sanitized_failure_record(self):
        run, check = self.api()
        missing = self.home / "SECRET_PRIVATE_RCA"
        result = check(missing, sys.executable)
        self.assertEqual("not_ready", result["status"])
        self.assertIn("RCA", result["message"])
        self.assertNotIn("SECRET_PRIVATE_RCA", json.dumps(result))
        telemetry = self.home / "SECRET_INPUT.csv"
        telemetry.write_text("time,x\n1,SECRET_CSV_ROW\n", encoding="utf-8")
        report = asyncio.run(run(telemetry=telemetry, rca_root=missing,
            rca_python=sys.executable, output_root=self.outputs))
        self.assertEqual("failed", report["status"])
        self.assertEqual("rca_missing", report["error"]["code"])
        self.assertNotIn("SECRET", json.dumps(report))

    def fake_rca(self, source):
        root = self.home / "RCA with spaces"
        script = root / "scripts/run_signal_workflow.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(source, encoding="utf-8")
        return root

    def test_rca_failure_and_timeout_do_not_echo_process_output(self):
        run, _ = self.api()
        telemetry = self.home / "input.csv"
        telemetry.write_text("time,x\n1,7\n", encoding="utf-8")
        for source, code, seconds in (("import sys\nprint('SECRET_CSV_ROW', file=sys.stderr)\nsys.exit(7)\n", "rca_failed", 5),
                                      ("import time\ntime.sleep(5)\n", "rca_timeout", .1)):
            with self.subTest(code=code):
                root = self.fake_rca(source)
                report = asyncio.run(run(telemetry=telemetry, rca_root=root,
                    rca_python=sys.executable, output_root=self.outputs, timeout_seconds=seconds))
                self.assertEqual("failed", report["status"])
                self.assertEqual(code, report["error"]["code"])
                self.assertNotIn("SECRET_CSV_ROW", json.dumps(report))

    def test_csv_subprocess_handles_spaces_and_profile_arguments(self):
        # 子进程真实读取含空格路径；遗漏参数或 shell 字符串拼接会导致失败。
        run, _ = self.api()
        source = """import argparse,json,pathlib,uuid
p=argparse.ArgumentParser()
p.add_argument('--telemetry');p.add_argument('--profile');p.add_argument('--output-dir');p.add_argument('--preprocessing')
a=p.parse_args()
assert pathlib.Path(a.telemetry).read_text() == 'time,x\\n1,7\\n'
assert json.loads(pathlib.Path(a.profile).read_text())['system']=='online-boutique'
assert a.preprocessing=='strict'
folder=pathlib.Path(a.output_dir)/('signal-run-'+uuid.uuid4().hex);folder.mkdir(parents=True)
(folder/'signal-bundle.json').write_bytes(pathlib.Path(__file__).parents[1].joinpath('payload.json').read_bytes())
"""
        root = self.fake_rca(source)
        (root / "payload.json").write_bytes(self.bundle.read_bytes())
        telemetry = self.home / "CSV with spaces.csv"
        telemetry.write_text("time,x\n1,7\n", encoding="utf-8")
        profile = self.home / "profile input.json"
        profile.write_text('{"system":"online-boutique"}', encoding="utf-8")
        report = asyncio.run(run(telemetry=telemetry, rca_root=root, rca_python=sys.executable,
            signal_profile=profile, preprocessing="strict", output_root=self.outputs,
            query="checkoutservice_latency 如何验证"))
        self.assertEqual("complete", report["status"])
        self.assertIn("telemetry_sha256", report["fingerprints"])

    def test_tampered_unready_bundle_fails_before_being_accepted_as_no_event(self):
        value = fixture()
        value["analysis"]["status"] = "no_event"
        self.bundle.write_text(json.dumps(value), encoding="utf-8")
        report = self.run_bundle()
        self.assertEqual("failed", report["status"])
        self.assertEqual("bundle_invalid", report["error"]["code"])

    def test_unwritable_output_returns_sanitized_json_without_traceback(self):
        blocked = self.home / "PRIVATE_OUTPUT_FILE"
        blocked.write_text("existing content", encoding="utf-8")
        completed = subprocess.run([sys.executable, str(ROOT / "src/run_pipeline.py"),
            "--bundle", str(self.bundle), "--output-dir", str(blocked)],
            capture_output=True, timeout=40)
        self.assertEqual(1, completed.returncode)
        self.assertTrue(completed.stdout, "输出目录错误仍必须返回 JSON 回执")
        receipt = json.loads(completed.stdout)
        self.assertEqual("failed", receipt["status"])
        self.assertFalse(receipt["persisted"])
        self.assertNotIn("Traceback", completed.stderr.decode("utf-8", errors="replace"))
        self.assertNotIn("PRIVATE_OUTPUT_FILE", completed.stdout.decode("utf-8"))
        self.assertEqual("existing content", blocked.read_text(encoding="utf-8"))

    def test_cli_stdout_is_one_json_receipt_and_preserves_error_exit_code(self):
        # 原框架导入/摄取的 print 不应污染供脚本消费的 JSON 回执。
        for arguments, expected_code, status in (
                (["--bundle", str(self.bundle), "--output-dir", str(self.outputs)], 0, "complete"),
                (["--check", "--rca-root", str(self.home / "MISSING_SECRET_RCA")], 1, "not_ready")):
            with self.subTest(status=status):
                completed = subprocess.run([sys.executable, str(ROOT / "src/run_pipeline.py"), *arguments],
                    capture_output=True, timeout=40)
                self.assertEqual(expected_code, completed.returncode)
                try:
                    receipt = json.loads(completed.stdout)
                except ValueError:
                    self.fail("CLI stdout 混入非 JSON 文本")
                self.assertEqual(status, receipt["status"])
                self.assertNotIn("MISSING_SECRET_RCA", completed.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
