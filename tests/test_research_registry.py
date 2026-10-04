"""Research admission gates, corpus version isolation and bounded multimodal evidence."""
import base64
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from easyrag.console.store import Catalog, read_json, write_json
from easyrag.console.research import attach, observations, admit_case, annotate
from easyrag.domain.knowledge import KnowledgeDocument
from test_signal_bundle import fixture
import test_console


def runbook(ident="manual-one", system="online-boutique"):
    return KnowledgeDocument("1.0", ident, "runbook", "Pod 检查", "checkoutservice Pod 日志和事件检查", "official", "reference:manual", {"system":system})


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.catalog=Catalog(self.root)
        self.manifest=self.root / "base/manifest.jsonl"
        write_manifest([runbook(), runbook("other-system", "other")], self.manifest)
        self.first=self.catalog.knowledge.publish([self.manifest],reason="test fixture")
        self.event=self.catalog.import_bundle(fixture())["event_id"]
        self.incident,_=self.catalog.event(self.event)

    def tearDown(self): self.temp.cleanup()

    def test_query_pins_version_and_never_unions_unrelated_event_data(self):
        _,old,info=self.catalog.query_corpus(self.event)
        original=(old / "manifest.jsonl").read_bytes()
        write_manifest([runbook(),runbook("manual-new")],self.manifest)
        second=self.catalog.knowledge.publish([self.manifest],reason="new fixture")
        _,new,newinfo=self.catalog.query_corpus(self.event)
        self.assertNotEqual(info["knowledge_version"],newinfo["knowledge_version"])
        self.assertEqual(original,(old / "manifest.jsonl").read_bytes())
        self.assertNotEqual(old,new)
        docs=load_manifest(new / "manifest.jsonl")
        self.assertNotIn("other-system",[d.knowledge_id for d in docs])
        _,imported,_=self.catalog.query_corpus(self.event,mode="event_snapshot")
        self.assertNotIn("manual-new",[d.knowledge_id for d in load_manifest(imported / "manifest.jsonl")])
        _,doc_only,_=self.catalog.query_corpus(system="online-boutique")
        self.assertEqual({"runbook"},{d.knowledge_type for d in load_manifest(doc_only / "manifest.jsonl")})
        (self.catalog.knowledge.version_path(second["version"]) / "manifest.jsonl").write_text("tampered")
        with self.assertRaises(ValueError): self.catalog.query_corpus(self.event)

    def observation(self,kind="log"):
        raw=b'{"message":"Pod restart observed"}'; sha=hashlib.sha256(raw).hexdigest()
        detection=self.incident["detection"]; end=detection["evidence_cutoff_utc"]
        start=datetime.fromtimestamp(detection["timestamp_unix"]-60,timezone.utc).isoformat()
        return {"documents":[KnowledgeDocument("1.0","bounded-"+kind,kind,"窗口日志","checkoutservice Pod restart observed",
            "observed telemetry","asset:"+sha,{"system":self.incident["system"],"source_incident_id":self.incident["incident_id"],
            "window_start_utc":start,"window_end_utc":end,"asset_sha256":sha,"derivation":"verbatim sample"}).to_dict()],
            "assets":[{"sha256":sha,"base64":base64.b64encode(raw).decode()}]}

    def test_attach_enforces_source_window_hash_and_immutable_batches(self):
        payload=self.observation(); bad=copy.deepcopy(payload)
        bad["assets"][0]["sha256"]="0"*64
        with self.assertRaises(ValueError): attach(self.catalog,self.event,bad)
        bad=copy.deepcopy(payload);bad["documents"][0]["metadata"]["window_end_utc"]="2099-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError,"cutoff"): attach(self.catalog,self.event,bad)
        bad=copy.deepcopy(payload);bad["documents"][0]["metadata"]["source_incident_id"]="another-event"
        with self.assertRaisesRegex(ValueError,"incident"): attach(self.catalog,self.event,bad)
        attach(self.catalog,self.event,payload)
        with self.assertRaises(ValueError): attach(self.catalog,self.event,payload)
        _,corpus,_=self.catalog.query_corpus(self.event)
        self.assertIn("log",[d.knowledge_type for d in load_manifest(corpus / "manifest.jsonl")])
        self.assertEqual(1,len(observations(self.catalog,self.event)))

    def case(self):
        return {"case":{"schema_version":"1.0","case_id":"case-unit-test","source_incident_id":self.incident["incident_id"],
            "system":self.incident["system"],"title":"Unit test fixture","symptoms":["test"],"rca_candidates":[],
            "verified_root_cause":"test-only","actions":["test-only"],"evidence_refs":["test-only"],"human_verified":True,
            "verified_by":"test reviewer","verified_at":datetime.now(timezone.utc).isoformat(),"source":"unit test","raw_ref":"test-only"},
            "outcome":"test-only","outcome_evidence":"test-only","attestation":True}

    def test_case_requires_attestation_outcome_and_actual_review_time(self):
        value=self.case();value["attestation"]=False
        with self.assertRaises(ValueError): admit_case(self.catalog,value)
        value=self.case();value["outcome"]=""
        with self.assertRaises(ValueError): admit_case(self.catalog,value)
        value=self.case();value["case"]["verified_at"]="2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError,"backdate"): admit_case(self.catalog,value)
        result=admit_case(self.catalog,self.case())
        self.assertEqual(1,self.catalog.knowledge.description()["counts"]["case"])
        from easyrag.retrieval.operations import eligible
        doc=next(d for d in self.catalog.knowledge.documents() if d.knowledge_type=="case")
        context={"current_incident_id":"different-id","system":self.incident["system"],"detection":self.incident["detection"]}
        self.assertEqual((False,"future_case"),eligible(doc.metadata,"case",context))
        with self.assertRaises(ValueError): admit_case(self.catalog,self.case())


class ResearchAPITests(unittest.TestCase):
    setUp=test_console.ConsoleTests.setUp
    tearDown=test_console.ConsoleTests.tearDown
    post=test_console.ConsoleTests.post
    completed=test_console.ConsoleTests.completed

    def test_document_only_run_and_annotation_validation(self):
        catalog=Catalog(self.root)
        manifest=self.root / "actual/manifest.jsonl";write_manifest([runbook()],manifest)
        version=catalog.knowledge.publish([manifest],reason="test scope")["version"]
        info=self.client.get("/api/knowledge").json();self.assertEqual(version,info["version"])
        job=self.completed(self.post("/api/runs",{"knowledge_system":"online-boutique","query":"checkoutservice Pod 日志检查",
            "retrieval":self.boot["retrieval"], "generation":self.boot["generation"]}))
        ident=job["result"]["generation_run_id"]
        order=self.client.get("/api/orders/"+ident).json()
        ids=[x["evidence"]["evidence_id"] for x in order["pack"]["pack"]["items"]]
        self.assertTrue(ids)
        body={"reviewer":"unit tester","notes":"test-only judgment","relevance":{ids[0]:2}}
        self.assertEqual(201,self.post(f"/api/orders/{ident}/annotations",body).status_code)
        body["relevance"]={"ev-doc-"+"0"*16:2}
        self.assertEqual(422,self.post(f"/api/orders/{ident}/annotations",body).status_code)
