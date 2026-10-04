import json
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from prepare_aiops_corpus import read_documents, verify_sources
from download_aiops_data import download
from easyrag.domain.knowledge import HistoricalCase
from easyrag.retrieval.operations import eligible


class DataPreparationTests(unittest.TestCase):
    def lock(self):
        return {"source_url": "https://example.org/dataset", "revision": "a" * 40,
                "system": "zte-aiops2024", "files": {"data.zip": {"sha256": "b" * 64, "size": 100}}}

    def test_import_deduplicates_empty_pages_and_excludes_questions(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "data.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("data/director/a.txt", "# 查询状态\n检查状态")
                z.writestr("data/director/b.txt", "# 查询状态\r\n检查状态\n")
                z.writestr("data/rcp/a.txt", "# 查询状态\n检查状态")
                z.writestr("data/director/empty.txt", " \n")
                z.writestr("question.jsonl", '{"answer": "do not ingest"}')
            docs, entries, counts = read_documents(archive, self.lock())
            self.assertEqual(2, len(docs))
            self.assertEqual(1, counts["duplicate_excluded"])
            self.assertEqual(1, counts["empty_excluded"])
            self.assertEqual(4, len(entries))
            self.assertTrue(all(d.knowledge_type == "runbook" for d in docs))
            context = {"current_incident_id": "inc-1", "system": "online-boutique", "detection": None}
            self.assertEqual((False, "different_system"), eligible(docs[0].metadata, "doc", context))

    def test_unsafe_member_and_unhydrated_lfs_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "data.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../escape.txt", "do not import")
            with self.assertRaisesRegex(ValueError, "unsafe archive"):
                read_documents(archive, self.lock())
            archive.write_text("version https://git-lfs.github.com/spec/v1")
            with self.assertRaisesRegex(ValueError, "checksum/size mismatch"):
                verify_sources(Path(folder), self.lock())

    def test_pending_review_cannot_be_imported_as_confirmed_history(self):
        # This checks the admission boundary independently of the packet renderer.
        payload = {"schema_version": "1.0", "case_id": "case-test", "source_incident_id": "inc-test",
                   "system": "online-boutique", "title": "Pending", "symptoms": ["Observed change"],
                   "rca_candidates": [], "verified_root_cause": "operator claim", "actions": ["operator claim"],
                   "evidence_refs": ["telemetry:test"], "human_verified": False,
                   "verified_by": "test", "verified_at": "2026-09-25T00:00:00Z",
                   "source": "unit test", "raw_ref": "fixture:test"}
        with self.assertRaisesRegex(ValueError, "unverified incident"):
            HistoricalCase.from_dict(payload)

    def test_download_verifies_before_replacing_lfs_pointer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            content = b"small verified archive"
            lock = self.lock()
            lock["files"] = {"data.zip": {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}}
            config = root / "source.json"
            config.write_text(json.dumps(lock))
            target = root / "data.zip"
            pointer = b"version https://git-lfs.github.com/spec/v1\n"
            target.write_bytes(pointer)
            with patch("download_aiops_data.urllib.request.urlopen", return_value=io.BytesIO(b"bad download")):
                with self.assertRaisesRegex(ValueError, "checksum/size mismatch"):
                    download(root, config)
            self.assertEqual(pointer, target.read_bytes())
            with patch("download_aiops_data.urllib.request.urlopen", return_value=io.BytesIO(content)):
                download(root, config)
            self.assertEqual(content, target.read_bytes())
