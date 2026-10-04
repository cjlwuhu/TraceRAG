"""Prepare an unconfirmed review packet from a validated signal bundle.

This command cannot sign a human review or promote a case. Its template is
deliberately rejected by the existing HistoricalCase admission validator.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from easyrag.adapters.signal_bundle import load_signal_bundle


ROOT = Path(__file__).resolve().parents[1]


def make_packet(bundle_path, output_root):
    incident, summaries = load_signal_bundle(bundle_path)
    directory = output_root / ("review-" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    candidates = sorted({c["metric"] for run in incident["rca_runs"] for c in run["candidates"]})
    template = {
        "schema_version": "1.0", "case_id": "case-" + incident["incident_id"],
        "source_incident_id": incident["incident_id"], "system": incident["system"],
        "title": "待人工核验的基准回放事件 " + incident["incident_id"],
        "symptoms": [s.summary for s in summaries], "rca_candidates": candidates,
        "verified_root_cause": "", "actions": [], "evidence_refs": [s.raw_ref for s in summaries],
        "human_verified": False, "verified_by": "", "verified_at": "",
        "source": "RCAEval telemetry replay; pending review, not a resolved production incident",
        "raw_ref": "signal:sha256:" + hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
    }
    packet = {
        "schema_version": "1.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "review_status": "pending_human_review", "human_verified": False, "case_promoted": False,
        "actions_executed": False, "incident_id": incident["incident_id"],
        "required_review": ["核对原始时序与指标单位", "区分统计关联、算法候选与已证实机制",
            "提供确认根因的独立证据；不能仅以算法首位或模型回答作证明",
            "记录真实处置及结果；数据集未提供处置时保持未知，不编造扩容/重启记录",
            "填写实际审核人和带时区的实际审核时间；不可倒填到基准事件发生时间"],
        "blockers": ["没有人工确认", "没有已核验的处置记录", "没有已确认的故障机制"],
        "note": "本材料仅由助手整理。工单语言复核、数据集注入标签与生产根因/处置确认是不同状态。",
        "temporal_policy": "2026 年审核不能作为 2023 年事件当时可用历史案例；当前门禁保持不变。",
        "files": {},
    }
    artifacts = {"incident.json": incident, "metric-summaries.json": [s.__dict__ for s in summaries],
                 "historical-case.pending.json": template}
    for name, data in artifacts.items():
        text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        (directory / name).write_text(text, encoding="utf-8", newline="\n")
        packet["files"][name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (directory / "review.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 历史案例入库前核验材料", "", f"事件：`{incident['incident_id']}`", "",
        "状态：待人工审核；尚未确认根因、未记录已执行处置、未进入案例库。", "",
        "## 已有观测", "", "| 指标 | 参考均值 | 观测均值 | 原始数据引用 |", "|---|---:|---:|---|"]
    for summary in summaries:
        stats = summary.statistics
        lines.append(f"| {summary.metric} | {stats.get('normal_mean', '未知')} | {stats.get('abnormal_mean', '未知')} | `{summary.raw_ref}` |")
    lines += ["", "## 需要人工补充", ""] + [f"- [ ] {item}" for item in packet["required_review"]]
    lines += ["", "现有 benchmark 只有时序和注入标注，不能证明采取了什么处置、是否恢复。",
        "`historical-case.pending.json` 故意保留空白确认字段及 human_verified=false，不能导入 HistoricalCase。",
        "现阶段没有自动签署或案例回流命令。先完成真实审核，再实现带来源与时间审计的正式入库。", ""]
    (directory / "review.md").write_text("\n".join(lines), encoding="utf-8")
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/case_review_queue")
    args = parser.parse_args()
    print(make_packet(args.bundle, args.output_root))
