import json
import tempfile
import unittest
from pathlib import Path

from easyrag.adapters.rca_result import build_incident_document, split_metric


class IncidentAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, filename: str, method: str, *, incident_id: str = "case-secret_fault") -> Path:
        payload = {
            "incident_id": incident_id,
            "system": "demo-system",
            "fault_type": "secret_fault",
            "inject_time": 1700000000,
            "method": method,
            "root_causes": [
                {"rank": 8, "metric": "frontend_latency", "score": 0.9},
                {"rank": 9, "metric": "paymentservice_cpu", "score": 0.4},
            ],
            "ground_truth": {
                "service": "secretservice",
                "metric": "secretservice_secretmetric",
            },
            "runtime_seconds": 0.25,
            "metadata": {"window_minutes": 5},
        }
        path = self.root / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_merges_methods_and_removes_evaluation_labels(self) -> None:
        first = self._write("one.json", "method-a")
        second = self._write("two.json", "method-b")

        output = build_incident_document([first, second], top_k=1).to_dict()
        serialized = json.dumps(output, ensure_ascii=False)

        self.assertEqual(["method-a", "method-b"], [run["method"] for run in output["rca_runs"]])
        self.assertEqual(["frontend_latency"], output["observations"]["candidate_metrics"])
        self.assertEqual("benchmark_annotation", output["detection"]["source"])
        self.assertNotIn("secret_fault", serialized)
        self.assertNotIn("secretservice", serialized)
        self.assertNotIn("ground_truth", serialized)
        self.assertNotIn("fault_type", serialized)
        self.assertRegex(output["incident_id"], r"^inc-1700000000-[a-f0-9]{12}$")
        self.assertNotIn("one.json", serialized)

    def test_rejects_mixed_incidents(self) -> None:
        first = self._write("one.json", "method-a", incident_id="case-a")
        second = self._write("two.json", "method-b", incident_id="case-b")

        with self.assertRaisesRegex(ValueError, "same incident"):
            build_incident_document([first, second])

    def test_rejects_duplicate_methods(self) -> None:
        first = self._write("one.json", "method-a")
        second = self._write("two.json", "method-a")

        with self.assertRaisesRegex(ValueError, "duplicate RCA method"):
            build_incident_document([first, second])

    def test_metric_hint_uses_final_underscore(self) -> None:
        self.assertEqual(("checkoutservice", "latency"), split_metric("checkoutservice_latency"))
        self.assertEqual(("service_http_request", "duration"), split_metric("service_http_request_duration"))
        self.assertEqual((None, None), split_metric("latency"))


if __name__ == "__main__":
    unittest.main()
