"""Rank-list fusion with auditable provenance and an exact text budget."""

import hashlib
import json
import math

from easyrag.domain.experiment import ExperimentConfig


def fingerprint(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evidence_block(item: dict) -> str:
    # All provenance is part of the counted generation context.
    return f"[{item['evidence_id']}] type={item['type']} source={item['source']}\n{item['content']}"


def fuse_evidence(routes: dict[str, list[dict]], config: ExperimentConfig, *, rerank=None) -> dict:
    items, contributions = {}, {}
    order = []
    for route, ranked in sorted(routes.items()):
        route_seen = set()
        for rank, item in enumerate(ranked, 1):
            evidence_id = item["evidence_id"]
            if evidence_id in route_seen:
                continue
            route_seen.add(evidence_id)
            if evidence_id in items and (items[evidence_id]["content"], items[evidence_id]["raw_ref"]) != (item["content"], item["raw_ref"]):
                raise ValueError("same evidence_id has conflicting content or provenance")
            if evidence_id not in items:
                items[evidence_id] = item
                contributions[evidence_id] = []
            weight = getattr(config.fusion.weights, item["type"])
            contributions[evidence_id].append({"route": route, "rank": rank, "raw_score": item["score"],
                                               "rrf_contribution": weight / (config.fusion.rrf_k + rank)})

    scores = {eid: sum(c["rrf_contribution"] for c in cs) for eid, cs in contributions.items()}
    if config.fusion.enabled:
        order = sorted(items, key=lambda eid: (-scores[eid], eid))
    else:
        # Deterministic round-robin baseline: never compare cross-route scores.
        for rank in range(max((len(v) for v in routes.values()), default=0)):
            for route in sorted(routes):
                if rank < len(routes[route]):
                    eid = routes[route][rank]["evidence_id"]
                    if eid not in order:
                        order.append(eid)

    selected, blocks, dropped = [], [], []
    rerank_scores = {}
    reranking = {"enabled": config.reranker.enabled, "model": None, "candidates": []}
    if config.reranker.enabled:
        if rerank is None:
            raise ValueError("Enabled reranker requires a scoring backend")
        candidates = order[:config.reranker.candidate_top_k]
        dropped.extend({"evidence_id": eid, "reason": "rerank_candidate_budget"}
                       for eid in order[config.reranker.candidate_top_k:])
        scores_out = rerank([items[eid]["content"] for eid in candidates]) if candidates else []
        if len(scores_out) != len(candidates) or any(type(s) not in (float, int) or not math.isfinite(s) for s in scores_out):
            raise ValueError("Reranker must return one finite score per candidate")
        rerank_scores = dict(zip(candidates, scores_out))
        reranking.update(model=config.reranker.model,
                         candidates=[{"evidence_id": eid, "score": rerank_scores[eid]} for eid in candidates])
        # Stable ties retain the initial fused/round-robin order. Rerank BEFORE packing.
        order = sorted(candidates, key=lambda eid: -rerank_scores[eid])
    for eid in order:
        reason = None
        block = evidence_block(items[eid])
        if len(selected) >= config.pack.top_k:
            reason = "top_k"
        elif len("\n\n".join([*blocks, block])) > config.pack.max_context_chars:
            reason = "context_budget"
        if reason:
            dropped.append({"evidence_id": eid, "reason": reason})
            continue
        selected.append({"evidence": items[eid], "pack_rank": len(selected) + 1,
                         "fusion_score": scores[eid] if config.fusion.enabled else None,
                         "rerank_score": rerank_scores.get(eid),
                         "contributions": contributions[eid]})
        blocks.append(block)
    context = "\n\n".join(blocks)
    return {"strategy": "weighted_rrf" if config.fusion.enabled else "round_robin",
            "items": selected, "context": context, "context_chars": len(context), "dropped": dropped,
            "reranking": reranking}
