"""Run explicit offline queries against the live local console and retain receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import requests
import yaml
from easyrag.console.store import write_json
from verify_work_order import audit

ROOT=Path(__file__).resolve().parents[1]

def main(args):
    base=f"http://127.0.0.1:{args.port}";session=requests.Session()
    boot=session.get(base+"/api/bootstrap",timeout=15).json()
    session.headers["X-Console-Token"]=boot["token"]
    knowledge=session.get(base+"/api/knowledge",timeout=30).json()
    modes=[{"name":"aiops_document_only","knowledge_system":"zte-aiops2024",
            "query":"交换机端口故障应该检查哪些告警和日志"},
        {"name":"multisource_event","event_id":args.event,"query":"日志、调用链耗时、服务拓扑和指标图如何相互验证，Pod 应如何排查"}]
    receipts=[]
    for mode in modes:
        name=mode.pop("name")
        retrieval=dict(boot["retrieval"])
        if name=="multisource_event": retrieval.update(yaml.safe_load((ROOT/"src/configs/experiments/multisource.yaml").read_text()))
        retrieval["retrieval"]["mode"]="bm25"; retrieval["embedding"]["enabled"]=False;retrieval["reranker"]["enabled"]=False
        generation={**boot["generation"],"enabled":True,"mode":"extractive","save_intermediates":True}
        response=session.post(base+"/api/runs",json={**mode,"retrieval":retrieval,"generation":generation},timeout=60)
        response.raise_for_status(); job=response.json();deadline=time.monotonic()+300
        while job["status"] in {"queued","running"} and time.monotonic()<deadline:
            time.sleep(.8);job=session.get(base+"/api/jobs/"+job["id"],timeout=30).json()
        if job["status"]!="completed": raise RuntimeError("console query failed: "+json.dumps(job,ensure_ascii=False))
        result=job["result"]
        checked=audit(ROOT/"outputs/work_orders"/result["generation_run_id"])
        if result["evidence_count"]==0: raise ValueError("expected actual matching evidence")
        receipts.append({"mode":name,"job_id":job["id"],"result":result,"audit":checked})
        print(name,job["id"],result["evidence_count"],flush=True)
    documents=session.get(base+f"/api/events/{args.event}/evidence",timeout=20).json()["items"]
    image=next(d for d in documents if d["knowledge_type"]=="image")
    sha=image["metadata"]["asset_sha256"]
    asset=session.get(base+f"/api/events/{args.event}/assets/{sha}",timeout=20)
    asset.raise_for_status()
    if hashlib.sha256(asset.content).hexdigest()!=sha: raise ValueError("image preview hash differs")
    write_json(args.output,{"knowledge":knowledge,"queries":receipts,"image_preview_sha256":sha,
        "purpose":"Actual corpus and API integration verification; not relevance or correctness evaluation"})
    print(args.output)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--event",required=True);parser.add_argument("--output",type=Path,required=True)
    main(parser.parse_args())
