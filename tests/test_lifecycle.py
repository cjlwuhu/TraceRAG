"""Lifecycle effects against temporary real artifacts; never existing user data."""

import base64
import hashlib
import io
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from urllib.parse import quote

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from easyrag.console.research import attach, annotate, admit_case
from easyrag.console.store import read_json, write_json
import test_research_registry as registry_tests
from test_signal_bundle import fixture
import test_console


class LifecycleTests(unittest.TestCase):
    setUp = test_console.ConsoleTests.setUp
    tearDown = test_console.ConsoleTests.tearDown
    post = test_console.ConsoleTests.post
    imported = test_console.ConsoleTests.imported
    completed = test_console.ConsoleTests.completed

    def delete(self, path):
        return self.client.delete(path, headers=self.headers)

    def observation(self, event, raw=b"exclusive observed log"):
        incident, _ = self.app.state.catalog.event(event)
        sha = hashlib.sha256(raw).hexdigest()
        value = registry_tests.RegistryTests.observation(type("Fixture", (), {"incident": incident})())
        value["documents"][0]["metadata"]["asset_sha256"] = sha
        value["documents"][0]["raw_ref"] = "asset:" + sha
        value["assets"] = [{"sha256": sha, "base64": base64.b64encode(raw).decode()}]
        return value, sha

    def order_job(self, event):
        return self.completed(self.post("/api/runs", {"event_id": event, "query": "checkoutservice 如何验证",
            "retrieval": self.boot["retrieval"], "generation": self.boot["generation"]}))

    def test_event_releases_owned_bytes_preserves_duplicate_and_external_input(self):
        event, duplicate = self.imported(), self.imported()
        catalog = self.app.state.catalog
        payload, sha = self.observation(event)
        attach(catalog, event, payload)
        external = self.root / "data/original.csv"
        external.parent.mkdir(); external.write_bytes(b"original input")
        job_id = "job-" + "a" * 32
        job_folder = catalog.jobs / job_id
        write_json(job_folder / "job.json", {"id": job_id, "kind": "telemetry", "status": "completed",
            "result": {"event_id": event}, "request": {"external_original": str(external)}})
        (job_folder / "telemetry.private.csv").write_bytes(b"uploaded copy")
        write_json(job_folder / "signals/signal-run-unit/signal-bundle.json", {"derived": True})
        targets = [catalog.imports / event, self.root / "outputs/event_evidence" / event,
            job_folder / "telemetry.private.csv", job_folder / "signals", self.root / "outputs/evidence_assets" / sha]
        files = [p for target in targets for p in ([target] if target.is_file() else target.rglob("*")) if p.is_file()]
        expected_bytes = sum(p.stat().st_size for p in files)
        response = self.delete("/api/events/" + event)
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertEqual(expected_bytes, result["reclaimed_bytes"])
        self.assertEqual(len(files), result["deleted_files"])
        self.assertEqual(event, result["deleted_id"])
        for target in targets: self.assertFalse(target.exists(), target)
        self.assertEqual(b"original input", external.read_bytes())
        self.assertEqual(200, self.client.get("/api/events/" + duplicate).status_code)
        self.assertTrue((job_folder / "job.json").exists())
        self.assertEqual(404, self.delete("/api/events/" + event).status_code)

    def test_asset_shared_with_event_or_frozen_snapshot_survives(self):
        catalog = self.app.state.catalog
        first, second = self.imported(), self.imported()
        payload, sha = self.observation(first)
        attach(catalog, first, payload); attach(catalog, second, payload)
        response = self.delete("/api/events/" + first)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, response.json()["retained_shared_assets"])
        asset = self.root / "outputs/evidence_assets" / sha
        self.assertTrue(asset.exists())
        _, snapshot, _ = catalog.query_corpus(second)
        original = (snapshot / "manifest.jsonl").read_bytes()
        response = self.delete("/api/events/" + second)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(asset.exists())
        self.assertEqual(original, (snapshot / "manifest.jsonl").read_bytes())

    def test_unreadable_remaining_reference_keeps_asset(self):
        event = self.imported()
        payload, sha = self.observation(event)
        attach(self.app.state.catalog, event, payload)
        damaged = self.root / "outputs/query_corpora/snapshot-damaged/manifest.jsonl"
        damaged.parent.mkdir(parents=True); damaged.write_text("invalid JSON", encoding="utf-8")
        response = self.delete("/api/events/" + event)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue((self.root / "outputs/evidence_assets" / sha).exists())

    def case_reference(self, mode):
        catalog = self.app.state.catalog
        base = self.root / "base/manifest.jsonl"
        write_manifest([registry_tests.runbook()], base)
        catalog.knowledge.publish([base], reason="unit reference fixture")
        event = self.imported()
        payload, sha = self.observation(event, b"case still owns its supporting raw evidence")
        attach(catalog, event, payload)
        incident, _ = catalog.event(event)
        submission = registry_tests.RegistryTests.case(type("Fixture", (), {"incident": incident})())
        submission["case"]["evidence_refs"] = ["asset:" + sha]
        submission["case"]["raw_ref"] = "synthetic:case-review-text-only"
        if mode == "fragment":
            submission["case"]["evidence_refs"] = ["asset:" + sha + "#original"]
            submission["case"]["raw_ref"] = "/evidence_assets/" + sha
        if mode == "published":
            from easyrag.domain.knowledge import HistoricalCase
            case_manifest = self.root / "case-source/manifest.jsonl"
            write_manifest([HistoricalCase.from_dict(submission["case"]).to_knowledge_document()], case_manifest)
            catalog.knowledge.publish([base, case_manifest], reason="independent case publication")
        else:
            admit_case(catalog, submission)
            if mode == "historical":
                response = self.delete("/api/knowledge/cases/" + submission["case"]["case_id"])
                self.assertEqual(200, response.status_code, response.text)
        response = self.delete("/api/events/" + event)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue((self.root / "outputs/evidence_assets" / sha).exists(), mode)
        self.assertEqual(1, response.json()["retained_shared_assets"])

    def test_published_case_body_keeps_referenced_asset(self):
        self.case_reference("published")

    def test_case_fragment_and_path_reference_keep_asset(self):
        self.case_reference("fragment")

    def test_removed_case_historical_version_keeps_referenced_asset(self):
        self.case_reference("historical")

    def test_event_keeps_generated_order_auditable(self):
        event = self.imported(); job = self.order_job(event)
        ident = job["result"]["generation_run_id"]
        response = self.delete("/api/events/" + event)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, response.json()["retained_orders"])
        self.assertEqual(200, self.client.get("/api/orders/" + ident).status_code)
        from verify_work_order import audit
        self.assertEqual(ident, audit(self.app.state.catalog.orders / ident)["generation_run_id"])

    def test_retained_order_image_is_accessible_only_to_its_own_evidence(self):
        event = self.imported()
        output = io.BytesIO(); Image.new("RGB", (2, 2), "red").save(output, format="PNG")
        raw = output.getvalue()
        payload, sha = self.observation(event, raw)
        payload["documents"][0]["knowledge_type"] = "image"
        attach(self.app.state.catalog, event, payload)
        retrieval = dict(self.boot["retrieval"])
        retrieval["sources"] = {key: key == "image" for key in self.boot["retrieval"]["sources"]}
        job = self.completed(self.post("/api/runs", {"event_id": event, "query": "checkoutservice Pod restart",
            "retrieval": retrieval, "generation": self.boot["generation"]}))
        ident = job["result"]["generation_run_id"]
        unrelated = self.order_job(self.imported())["result"]["generation_run_id"]
        response = self.delete("/api/events/" + event)
        self.assertEqual(200, response.status_code, response.text)
        response = self.client.get(f"/api/orders/{ident}/assets/{sha}")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("image/png", response.headers["content-type"])
        self.assertEqual(raw, response.content)
        self.assertEqual(404, self.client.get(f"/api/orders/{unrelated}/assets/{sha}").status_code)
        self.assertEqual(404, self.client.get(f"/api/orders/{ident}/assets/" + "0" * 64).status_code)
        self.assertEqual(422, self.client.get(f"/api/orders/{ident}/assets/not-a-hash").status_code)

    def test_removing_only_published_case_leaves_a_valid_empty_registry(self):
        catalog = self.app.state.catalog
        event = self.imported(); incident, _ = catalog.event(event)
        case = registry_tests.RegistryTests.case(type("Fixture", (), {"incident": incident})())["case"]
        from easyrag.domain.knowledge import HistoricalCase
        manifest = self.root / "case-only/manifest.jsonl"
        write_manifest([HistoricalCase.from_dict(case).to_knowledge_document()], manifest)
        before = catalog.knowledge.publish([manifest], reason="case-only unit fixture")["version"]
        response = self.delete("/api/knowledge/cases/" + case["case_id"])
        self.assertEqual(200, response.status_code, response.text)
        self.assertNotEqual(before, response.json()["knowledge_version"])
        self.assertEqual([], catalog.knowledge.documents())
        self.assertEqual({}, catalog.knowledge.description()["counts"])

    def test_order_removes_revisions_annotations_jobs_and_owned_retrieval(self):
        catalog = self.app.state.catalog
        event = self.imported(); job = self.order_job(event)
        ident = job["result"]["generation_run_id"]
        order = self.client.get("/api/orders/" + ident).json()
        evidence_id = order["pack"]["pack"]["items"][0]["evidence"]["evidence_id"]
        annotation = annotate(catalog, ident, {"reviewer": "unit tester", "notes": "test-only", "relevance": {evidence_id: 2}})
        write_json(catalog.reviews / ident / ("rev-" + "b" * 32) / "review.json", {"generation_run_id": ident})
        other = catalog.orders / ("gen-" + "c" * 32)
        write_json(other / "work-order.json", {"generation_run_id": other.name, "source_retrieval_run_id": "run-unrelated"})
        retrieval = self.root / "outputs/operations" / (order["record"]["source_retrieval_run_id"] + ".json")
        paths = [catalog.orders / ident, catalog.reviews / ident, catalog.jobs / job["id"],
            self.root / "outputs/annotations" / (annotation["annotation_id"] + ".json"), retrieval]
        expected = sum(p.stat().st_size for target in paths for p in ([target] if target.is_file() else target.rglob("*")) if p.is_file())
        response = self.delete("/api/orders/" + ident)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json()["reclaimed_bytes"])
        for path in paths: self.assertFalse(path.exists(), path)
        self.assertTrue(other.exists())
        from evaluate_human_annotations import evaluate
        self.assertEqual(0, evaluate(self.root)["annotated_packs_by_reviewer"])

    def test_shared_retrieval_survives_order_deletion(self):
        event = self.imported(); job = self.order_job(event)
        ident = job["result"]["generation_run_id"]
        record = self.client.get("/api/orders/" + ident).json()["record"]
        write_json(self.app.state.catalog.orders / ("gen-" + "d" * 32) / "work-order.json",
            {"generation_run_id": "gen-" + "d" * 32, "source_retrieval_run_id": record["source_retrieval_run_id"]})
        response = self.delete("/api/orders/" + ident)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue((self.root / "outputs/operations" / (record["source_retrieval_run_id"] + ".json")).exists())

    def test_research_reference_keeps_retrieval_after_order_deletion(self):
        event = self.imported(); job = self.order_job(event)
        ident = job["result"]["generation_run_id"]
        run_id = self.client.get("/api/orders/" + ident).json()["record"]["source_retrieval_run_id"]
        write_json(self.root / "outputs/research_reports/unit/report.json", {"retrieval_run_id": run_id})
        response = self.delete("/api/orders/" + ident)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue((self.root / "outputs/operations" / (run_id + ".json")).exists())

    def test_historical_case_body_keeps_its_retrieval_record(self):
        catalog = self.app.state.catalog
        manifest = self.root / "base/manifest.jsonl"; write_manifest([registry_tests.runbook()], manifest)
        catalog.knowledge.publish([manifest], reason="unit reference fixture")
        event = self.imported(); job = self.order_job(event)
        ident = job["result"]["generation_run_id"]
        run_id = self.client.get("/api/orders/" + ident).json()["record"]["source_retrieval_run_id"]
        incident, _ = catalog.event(event)
        submission = registry_tests.RegistryTests.case(type("Fixture", (), {"incident": incident})())
        submission["case"]["evidence_refs"] = [run_id]
        admission = admit_case(catalog, submission)
        old_version = catalog.knowledge.description()["version"]
        response = self.delete("/api/knowledge/cases/" + submission["case"]["case_id"])
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse((self.root / "knowledge/case_reviews" / admission["review_id"]).exists())
        self.assertIn(run_id, next(d.content for d in catalog.knowledge.documents(old_version) if d.knowledge_type == "case"))
        response = self.delete("/api/orders/" + ident)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue((self.root / "outputs/operations" / (run_id + ".json")).exists())

    def test_case_inventory_and_removal_publish_new_version_preserve_history(self):
        catalog = self.app.state.catalog
        manifest = self.root / "base/manifest.jsonl"; write_manifest([registry_tests.runbook()], manifest)
        catalog.knowledge.publish([manifest], reason="unit fixture")
        event = self.imported(); incident, _ = catalog.event(event)
        submission = registry_tests.RegistryTests.case(type("Fixture", (), {"incident": incident})())
        submission["case"]["case_id"] = "case.unit-review"
        admission = admit_case(catalog, submission)
        before = catalog.knowledge.description()["version"]
        old = catalog.knowledge.version_path(before) / "manifest.jsonl"
        old_bytes = old.read_bytes()
        _, snapshot, _ = catalog.query_corpus(event); snapshot_bytes = (snapshot / "manifest.jsonl").read_bytes()
        response = self.client.get("/api/knowledge/cases")
        self.assertEqual(200, response.status_code, response.text)
        item = response.json()["items"][0]
        self.assertEqual("case.unit-review", item["case_id"])
        self.assertEqual(incident["incident_id"], item["source_incident_id"])
        self.assertIn("self-attestation", item["identity_assurance"])
        review = self.root / "knowledge/case_reviews" / admission["review_id"]
        expected = sum(p.stat().st_size for p in review.rglob("*") if p.is_file())
        response = self.delete("/api/knowledge/cases/" + quote(item["case_id"], safe=""))
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertEqual(before, result["previous_knowledge_version"])
        self.assertNotEqual(before, result["knowledge_version"])
        self.assertTrue(result["retained_audit_versions"])
        self.assertEqual(expected, result["reclaimed_bytes"])
        self.assertFalse(review.exists())
        self.assertEqual(old_bytes, old.read_bytes())
        self.assertEqual(snapshot_bytes, (snapshot / "manifest.jsonl").read_bytes())
        self.assertEqual([], self.client.get("/api/knowledge/cases").json()["items"])
        self.assertEqual({"runbook"}, {d.knowledge_type for d in catalog.knowledge.documents()})
        self.assertEqual("case.unit-review", next(d.knowledge_id for d in load_manifest(old) if d.knowledge_type == "case"))
        receipts = list((catalog.state / "deletions").glob("delete-*.json"))
        self.assertEqual(1, len(receipts))
        receipt = read_json(receipts[0])
        self.assertEqual(before, receipt["previous_knowledge_version"])
        self.assertEqual(result["knowledge_version"], receipt["knowledge_version"])
        self.assertEqual(["case.unit-review"], receipt["excluded_knowledge_ids"])
        self.assertNotIn("test-only", receipts[0].read_text(encoding="utf-8"))

    def test_query_snapshot_and_admission_exclude_concurrent_deletion(self):
        event = self.imported()
        entered, release, delete_done, worker_release = (threading.Event() for _ in range(4))
        results = {}
        catalog = self.app.state.catalog
        original_query = catalog.query_corpus
        from easyrag.console import server
        original_runner = server.build_runner
        def paused_query(*args, **kwargs):
            entered.set(); release.wait(10); return original_query(*args, **kwargs)
        async def paused_runner(*args, **kwargs):
            import asyncio
            await asyncio.to_thread(worker_release.wait, 10)
            return await original_runner(*args, **kwargs)
        def submit():
            results["run"] = self.post("/api/runs", {"event_id": event, "query": "checkoutservice",
                "retrieval": self.boot["retrieval"], "generation": self.boot["generation"]})
        def remove():
            results["delete"] = self.delete("/api/events/" + event); delete_done.set()
        with patch.object(catalog, "query_corpus", paused_query), patch.object(server, "build_runner", paused_runner):
            writer = threading.Thread(target=submit); writer.start()
            self.assertTrue(entered.wait(5))
            remover = threading.Thread(target=remove); remover.start()
            try:
                self.assertFalse(delete_done.wait(.1))
                release.set(); writer.join(10); remover.join(10)
                self.assertEqual(202, results["run"].status_code, results["run"].text)
                self.assertEqual(409, results["delete"].status_code, results["delete"].text)
                self.assertTrue((catalog.imports / event).exists())
            finally:
                release.set(); worker_release.set(); writer.join(10); remover.join(10)
            self.completed(results["run"])

    def test_configured_persistent_root_link_is_pinned_but_may_not_redirect(self):
        first, second = self.imported(), self.imported()
        # Windows keeps console.lock open for the lifespan; close before relocating.
        self.client.__exit__(None, None, None)
        output_root = self.root / "outputs"
        persistent = self.root / "persistent-state"
        output_root.rename(persistent)
        def link_to(target):
            if os.name == "nt":
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(output_root), str(target)], capture_output=True)
                if result.returncode: self.skipTest("junction unavailable")
            else: output_root.symlink_to(target, target_is_directory=True)
        def unlink_root():
            if os.name == "nt": output_root.rmdir()
            else: output_root.unlink()
        link_to(persistent)
        try:
            from easyrag.console.lifecycle import Lifecycle
            lifecycle = Lifecycle(self.app.state.catalog, self.app.state.queue)
            result = lifecycle.delete_event(first)
            self.assertEqual(first, result["deleted_id"])
            self.assertFalse((persistent / "signal_imports" / first).exists())
            redirected = self.root / "other-state"; redirected.mkdir()
            unlink_root(); link_to(redirected)
            with self.assertRaises(ValueError): lifecycle.delete_event(second)
            self.assertTrue((persistent / "signal_imports" / second / "incident.json").exists())
        finally: unlink_root()

    def test_completed_job_is_not_visible_before_admission_slot_is_released(self):
        queue = self.app.state.queue
        reached, release, observed = threading.Event(), threading.Event(), threading.Event()
        original = queue.update
        results = {}
        def pause_completion(ident, **changes):
            original(ident, **changes)
            if changes.get("status") == "completed":
                reached.set(); release.wait(10)
        with patch.object(queue, "update", pause_completion):
            job = queue.submit("unit-fixture", {}, lambda _: {"ok": True})
            self.assertTrue(reached.wait(5))
            def read_completed():
                results["response"] = self.client.get("/api/jobs/" + job["id"])
                results["pending"] = queue.pending; observed.set()
            reader = threading.Thread(target=read_completed); reader.start()
            try: self.assertFalse(observed.wait(.1), "completed state escaped while the admission slot was still occupied")
            finally: release.set(); reader.join(10)
        self.assertEqual("completed", results["response"].json()["status"])
        self.assertEqual(0, results["pending"])

    def test_pending_job_blocks_all_deletions_without_removing_files(self):
        event = self.imported()
        started, release = threading.Event(), threading.Event()
        def worker(_):
            started.set(); release.wait(10); return {"ok": True}
        self.app.state.queue.submit("unit-fixture", {}, worker)
        self.assertTrue(started.wait(5))
        try:
            for path in ("/api/events/" + event, "/api/orders/gen-" + "a" * 32,
                         "/api/knowledge/cases/unknown"):
                response = self.delete(path)
                self.assertEqual(409, response.status_code, response.text)
                self.assertEqual("artifacts_in_use", response.json()["detail"]["code"])
            self.assertTrue((self.app.state.catalog.imports / event).exists())
        finally: release.set()

    def test_delete_origin_and_token_guard_then_invalid_id(self):
        event = self.imported(); path = "/api/events/" + event
        self.assertEqual(403, self.client.delete(path).status_code)
        self.assertEqual(403, self.client.delete(path, headers={**self.headers, "Origin": "https://evil.example"}).status_code)
        self.assertTrue((self.app.state.catalog.imports / event).exists())
        self.assertEqual(422, self.delete("/api/events/import-invalid").status_code)

    def test_nested_symlink_rejects_deletion_before_any_removal(self):
        event = self.imported(); folder = self.app.state.catalog.imports / event
        external = self.root / "originals"; external.mkdir(); (external / "kept.txt").write_bytes(b"keep")
        link = folder / "unexpected-link"
        try: link.symlink_to(external, target_is_directory=True)
        except OSError as exc: self.skipTest("symlink unavailable: " + str(exc.errno))
        try:
            response = self.delete("/api/events/" + event)
            self.assertEqual(422, response.status_code, response.text)
            self.assertTrue((folder / "incident.json").exists())
            self.assertEqual(b"keep", (external / "kept.txt").read_bytes())
        finally: link.unlink()

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_nested_junction_rejects_deletion_before_any_removal(self):
        event = self.imported(); folder = self.app.state.catalog.imports / event
        external = self.root / "originals"; external.mkdir(); (external / "kept.txt").write_bytes(b"keep")
        link = folder / "unexpected-junction"
        command = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)], capture_output=True)
        if command.returncode: self.skipTest("junction unavailable")
        try:
            response = self.delete("/api/events/" + event)
            self.assertEqual(422, response.status_code, response.text)
            self.assertTrue((folder / "incident.json").exists())
            self.assertEqual(b"keep", (external / "kept.txt").read_bytes())
        finally: link.rmdir()

    def test_attach_and_delete_do_not_leave_resurrected_evidence(self):
        event = self.imported(); payload, _ = self.observation(event)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        results = []
        from easyrag.console import research
        original = research.observations
        def pause(*args):
            entered.set(); release.wait(10); return original(*args)
        def attach_worker():
            results.append(self.post("/api/events/" + event + "/evidence", payload).status_code)
        def delete_worker():
            results.append(self.delete("/api/events/" + event).status_code); finished.set()
        with patch.object(research, "observations", pause):
            writer = threading.Thread(target=attach_worker); writer.start()
            self.assertTrue(entered.wait(5))
            remover = threading.Thread(target=delete_worker); remover.start()
            try: self.assertFalse(finished.wait(.1), "delete raced the attachment writer")
            finally: release.set(); writer.join(10); remover.join(10)
        self.assertEqual([201, 200], results)
        self.assertFalse((self.root / "outputs/event_evidence" / event).exists())
        self.assertFalse((self.app.state.catalog.imports / event).exists())


if __name__ == "__main__": unittest.main()
