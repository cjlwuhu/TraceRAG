"""Snapshot scoped Kubernetes operational references from a pinned upstream commit."""
import hashlib
import json
from pathlib import Path
import urllib.request

from easyrag.adapters.knowledge_corpus import write_manifest
from easyrag.domain.knowledge import KnowledgeDocument

ROOT = Path(__file__).resolve().parents[1]
REVISION = "fcf148c070b140cbd13ec65d5809f0686e680a9c"
PAGES = {
    "debug-running-pod": "排查运行中的 Pod：日志、容器和资源",
    "debug-service": "排查 Service：网络连接、端点与请求失败",
    "determine-reason-pod-failure": "确定 Pod 失败原因与终止信息",
}
SYSTEMS = ("online-boutique", "sock-shop", "train-ticket")


def main():
    raw_dir = ROOT / "data/external/kubernetes" / REVISION
    raw_dir.mkdir(parents=True, exist_ok=True)
    documents, sources = [], []
    for page, title in PAGES.items():
        relative = f"content/zh-cn/docs/tasks/debug/debug-application/{page}.md"
        url = f"https://raw.githubusercontent.com/kubernetes/website/{REVISION}/{relative}"
        path = raw_dir / (page + ".md")
        if not path.exists():
            with urllib.request.urlopen(url, timeout=40) as response: path.write_bytes(response.read())
        raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8")
        if text.startswith("---"): text = text.split("---", 2)[-1].strip()
        source = f"https://github.com/kubernetes/website/blob/{REVISION}/{relative}"
        sources.append({"url": source, "sha256": digest, "file": str(path.relative_to(ROOT))})
        for system in SYSTEMS:
            documents.append(KnowledgeDocument(schema_version="1.0",
                knowledge_id=f"runbook-k8s-{system}-{digest[:16]}", knowledge_type="runbook", title=title,
                content="适用前提：系统运行于 Kubernetes；核对实际版本、权限与环境后使用。\n\n" + text,
                source=source, raw_ref=f"source:sha256:{digest}", metadata={"system": system, "sha256": digest}))
    output = ROOT / "outputs/knowledge_builds/microservices" / REVISION
    if output.exists(): raise ValueError("build already exists; use its immutable manifest")
    write_manifest(documents, output / "corpus/manifest.jsonl")
    (output / "report.json").write_text(json.dumps({"revision": REVISION, "documents": len(documents),
        "sources": sources, "scope": "operational reference; not a confirmed RCAEval deployment configuration or case"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(output / "corpus/manifest.jsonl")


if __name__ == "__main__": main()
