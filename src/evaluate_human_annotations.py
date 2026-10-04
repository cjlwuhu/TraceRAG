"""Score saved human judgments only; missing judgments stay null, never automatic truth."""
import argparse
from collections import Counter
import json
from pathlib import Path
from easyrag.console.store import read_json
from easyrag.console.knowledge_registry import digest

def evaluate(root):
    latest = {}
    for path in sorted((root / "outputs/annotations").glob("annotation-*.json")):
        item=read_json(path);key=(item["generation_run_id"],item["reviewer"])
        pack=root / "outputs/work_orders" / item["generation_run_id"] / "evidence-pack.json"
        if digest(pack) != item["pack_sha256"]: raise ValueError("annotated pack changed")
        if key not in latest or item["created_at_utc"] > latest[key]["created_at_utc"]: latest[key]=item
    complete=[x for x in latest.values() if x["complete_pool"]]
    def precision(threshold):
        return sum(sum(v>=threshold for v in x["relevance"].values())/len(x["relevance"]) for x in complete)/len(complete) if complete else None
    reviews={}
    for path in (root / "outputs/console/reviews").glob("gen-*/rev-*/review.json"):
        item=read_json(path); key=(item["generation_run_id"],item["reviewer"])
        if key not in reviews or item["created_at_utc"] > reviews[key]["created_at_utc"]: reviews[key]=item
    supports=Counter(c["support"] for r in reviews.values() for c in r["claims"])
    return {"annotated_packs_by_reviewer":len(latest), "fully_judged_packs_by_reviewer":len(complete),
        "macro_precision_over_retrieved_pool_relevance_ge_1":precision(1),
        "macro_precision_over_retrieved_pool_relevance_eq_2":precision(2),
        "claim_judgments":dict(supports), "claim_supported_fraction":supports["supported"]/sum(supports.values()) if supports else None,
        "limitations":["Local reviewers are self-asserted; inspect provenance and exclude UI-test fixtures before research use",
            "Incomplete pools are excluded from precision denominators, counts reported separately",
            "Recall and global ranking quality cannot be inferred from retrieved-only pools",
            "Different questions/runs/reviewers are not an independent random sample"]}

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument("--output",type=Path)
    args=parser.parse_args();value=evaluate(args.root);text=json.dumps(value,ensure_ascii=False,indent=2)+"\n"
    if args.output:
        with args.output.open("x",encoding="utf-8") as stream:stream.write(text)
    print(text)
