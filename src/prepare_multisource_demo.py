"""Build a real, label-free RE2 integration example (not a quality benchmark)."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outputs/optional_runtime"))
import pandas as pd
from easyrag.adapters.knowledge_corpus import write_manifest
from easyrag.console.store import Catalog, read_json, write_json
from easyrag.console.research import put_asset, attach
from easyrag.domain.knowledge import KnowledgeDocument


def utc(seconds):
    return datetime.fromtimestamp(float(seconds), timezone.utc).isoformat().replace("+00:00", "Z")


def main(args):
    source = args.data
    # Fixed checksums prevent silently changing a demonstration under the same revision.
    expected = {"logs.parquet": "e1a25e50c8b0beae4b2886df54d86d2f28877d0b611fed98411e9c0b0c77dad3",
        "metrics.parquet": "f46b3d354b234e37c424f54a005329b61aac83d15dcdd46b5642e55f390ab25a",
        "traces.parquet": "56ca8f6dbeb76fab2d33faeca54bce8f40e9c5491ebd76404ebb2dfd83409367"}
    catalog = Catalog(ROOT)
    for name, sha in expected.items():
        if put_asset(ROOT, (source / name).read_bytes()) != sha: raise ValueError("source checksum changed: " + name)
    folder = ROOT / "outputs/multisource" / ("demo-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    metrics = pd.read_parquet(source / "metrics.parquet")
    metrics.to_csv(folder / "telemetry.private.csv", index=False)
    result = subprocess.run([sys.executable, str(args.rca / "scripts/run_signal_workflow.py"),
        "--telemetry", str(folder / "telemetry.private.csv"), "--output-dir", str(folder / "signals"),
        "--preprocessing", "causal_ffill5_zero_v1"], cwd=args.rca, capture_output=True,
        check=True, timeout=600)
    (folder / "rca.stdout.log").write_bytes(result.stdout)
    bundle_path = next((folder / "signals").glob("signal-run-*/signal-bundle.json"))
    bundle = read_json(bundle_path)
    if bundle["analysis"]["status"] != "complete": raise ValueError("RE2 example has no complete detected event")
    imported = catalog.import_bundle(bundle)
    incident, _ = catalog.event(imported["event_id"])
    center = incident["detection"]["timestamp_unix"]
    start = center - incident["detection"]["window_minutes"] * 60
    end = datetime.fromisoformat(incident["detection"]["evidence_cutoff_utc"].replace("Z", "+00:00")).timestamp()
    docs = []
    def document(kind, title, content, sha, selector, derivation, media="application/vnd.apache.parquet", **metadata):
        identity = hashlib.sha256((incident["incident_id"] + kind + title + content).encode()).hexdigest()[:24]
        item = KnowledgeDocument("1.0", "re2-" + kind + "-" + identity, kind, title, content,
            "RCAEval RE2 bounded observation", "asset:" + sha + "#" + selector,
            {"system": incident["system"], "source_incident_id": incident["incident_id"],
             "window_start_utc": utc(start), "window_end_utc": utc(end),
             "asset_sha256": sha, "asset_media_type": media, "derivation": derivation, **metadata})
        item.validate(); docs.append(item)
    logs = pd.read_parquet(source / "logs.parquet")
    logs = logs[(logs.timestamp >= start) & (logs.timestamp < end)].copy()
    for service, rows in logs.groupby("container_name", sort=True):
        messages = rows.message.astype(str).value_counts().head(5)
        text = f"日志观测；服务 {service}；窗口内 {len(rows)} 行。高频消息（最多五项，截断到 240 字）：\n"
        text += "\n".join(f"{int(count)} 次：{message[:240]}" for message, count in messages.items())
        text += "\n频次仅描述该窗口，不代表错误率或已确认根因。"
        document("log", f"{service} 日志窗口摘要", text, expected["logs.parquet"],
            f"timestamp=[{start},{end});container_name={service}", "groupby container_name; top5 message counts; no labels", service=str(service))
    traces = pd.read_parquet(source / "traces.parquet")
    # Jaeger JSON startTime and duration are microseconds. Use span completion, not just start, for the cutoff.
    starts = pd.to_numeric(traces.startTime, errors="raise") / 1e6
    ends = starts + pd.to_numeric(traces.duration, errors="raise") / 1e6
    traces = traces[(starts >= start) & (ends <= end) & (traces.duration >= 0)].copy()
    for service, rows in traces.groupby("serviceName", sort=True):
        duration = rows.duration.astype(float) / 1000
        text = f"调用链观测；服务 {service}；完整 span {len(rows)} 条。耗时中位数 {duration.median():.4g} ms，P95 {duration.quantile(.95):.4g} ms。"
        text += "\nstatusCode 原值频次：" + json.dumps(dict(Counter(str(v) for v in rows.statusCode)), ensure_ascii=False)
        text += "。未把该字段直接解释成 HTTP 失败率；耗时统计不能单独证明根因。"
        document("trace", f"{service} 调用链耗时摘要", text, expected["traces.parquet"],
            f"startTime/1e6>={start};(startTime+duration)/1e6<={end};serviceName={service}",
            "Jaeger microsecond duration -> milliseconds; median and p95 of completed spans", service=str(service))
    parents = {(str(row.traceID), str(row.spanID)): str(row.serviceName) for row in traces.itertuples()}
    edges = Counter()
    for row in traces.itertuples():
        parent = parents.get((str(row.traceID), str(row.parentSpanID)))
        if parent and parent != str(row.serviceName): edges[(parent, str(row.serviceName))] += 1
    document("topology", "当前窗口服务调用拓扑", "服务调用观测（边是父子 span 关联，不是因果结论）：\n" +
        "\n".join(f"{a} -> {b}: {count} spans" for (a, b), count in sorted(edges.items())),
        expected["traces.parquet"], f"completed-spans=[{start},{end}];join=(traceID,parentSpanID)->(traceID,spanID)",
        "Join parent spans within same trace and same bounded window; cross-service edges only")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    selected = [c["metric"] for r in bundle["rca_runs"] for c in r["candidates"] if c["metric"] in metrics.columns][:3]
    if not selected: selected = list(metrics.columns.drop("time"))[:3]
    window = metrics[(metrics.time >= start) & (metrics.time < end)]
    fig, axes = plt.subplots(len(selected), 1, figsize=(10, 2.2 * len(selected)), squeeze=False, layout="constrained")
    descriptions = []
    for ax, name in zip(axes[:, 0], selected):
        ax.plot(window.time - center, window[name], linewidth=1.4, color="#23766c")
        ax.axvline(0, color="#b45645", linestyle="--", linewidth=1)
        ax.set_ylabel(name, fontsize=8); ax.grid(alpha=.2)
        values = window[name].dropna()
        descriptions.append(f"{name}：min={values.min():.5g}, max={values.max():.5g}")
    axes[-1, 0].set_xlabel("Seconds relative to detected alarm (0)")
    fig.suptitle("Observed metrics — RCA candidates remain unconfirmed", fontsize=12)
    chart = folder / "observed-metrics.png"; fig.savefig(chart, dpi=140); plt.close(fig)
    chart_sha = put_asset(ROOT, chart.read_bytes())
    document("image", "检测窗口指标图及确定性图注", "时序图展示当前 RCA 候选的观测指标，零点为检测器告警时刻。\n" +
        "\n".join(descriptions) + "\n图注来自绘图数据的统计量，未调用 OCR 或视觉模型；曲线变化不是根因确认。",
        chart_sha, "full-image", "matplotlib chart from bounded metrics; deterministic numeric caption; no visual diagnosis", "image/png")
    write_manifest(docs, folder / "manifest.jsonl")
    receipt = attach(catalog, imported["event_id"], {"documents": [d.to_dict() for d in docs], "assets": []})
    _, corpus, info = catalog.query_corpus(imported["event_id"])
    report = {**imported, "directory": str(folder), "corpus": str(corpus), "snapshot": info, "attachment": receipt,
        "observation_counts": dict(Counter(d.knowledge_type for d in docs)), "bounded_rows": {"logs": len(logs), "traces": len(traces)},
        "sources_sha256": expected, "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": ["One RE2 integration case; no multimodal accuracy estimate", "No annotation/injection labels used", "Image is a telemetry plot with deterministic caption, not OCR/vision model validation"]}
    write_json(folder / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/external/rcaeval-re2")
    parser.add_argument("--rca", type=Path, default=ROOT.parent / "RCA")
    main(parser.parse_args())
