"""一条命令完成时序信号、RCA、检索、工单草稿与独立审计。"""

import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys

# Windows 重定向时可能默认使用 CP936；JSON 回执统一 UTF-8，便于脚本解析中文。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 兼容旧摄取模块的调试 print；CLI stdout 始终只有一份可解析 JSON。
with open(os.devnull, "w") as _discarded, redirect_stdout(_discarded):
    from easyrag.orchestration.pipeline import (
        DEFAULT_CONFIG, DEFAULT_MANIFEST, DEFAULT_OUTPUT, check_rca, run_pipeline,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--telemetry", type=Path, help="显式时序 CSV/JSON 文件，由独立 RCA CLI 分析")
    source.add_argument("--bundle", type=Path, help="复用既有 signal-bundle.json，不重新执行 RCA")
    parser.add_argument("--check", action="store_true", help="仅检查 RCA CLI 和所选算法的 Python 依赖")
    parser.add_argument("--rca-root", type=Path, help="独立 RCA 项目根目录，默认同级 RCA")
    parser.add_argument("--rca-python", help="RCA Python 可执行文件，默认当前 Python")
    parser.add_argument("--signal-profile", type=Path, help="RCA 信号配置覆盖 YAML/JSON，仅用于 CSV 或 --check")
    parser.add_argument("--preprocessing", choices=["strict", "causal_ffill5_zero_v1"], default="strict")
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--query", default="当前异常如何验证和处置")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_MANIFEST,
        help="长期知识 JSONL；默认教学手册基线，教学 case 和其他事件观测会排除")
    parser.add_argument("--retrieval-profile", type=Path)
    parser.add_argument("--generation-profile", type=Path)
    parser.add_argument("--cloud", action="store_true", help="显式允许云端调用；仍需有效配置及凭据")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="流水线输出父目录")
    args = parser.parse_args(argv)
    if args.check:
        if args.telemetry or args.bundle:
            parser.error("--check 不能与 --telemetry 或 --bundle 同时使用")
        result = check_rca(args.rca_root, args.rca_python, signal_profile=args.signal_profile,
            timeout_seconds=min(args.timeout_seconds, 60))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ready" else 1
    if not args.telemetry and not args.bundle:
        parser.error("请提供 --telemetry 或 --bundle；环境自检使用 --check")
    with open(os.devnull, "w") as discarded, redirect_stdout(discarded):
        result = asyncio.run(run_pipeline(telemetry=args.telemetry, bundle=args.bundle,
            query=args.query, config_path=args.config, base_manifest=args.base_manifest,
            output_root=args.output_dir, rca_root=args.rca_root, rca_python=args.rca_python,
            signal_profile=args.signal_profile, preprocessing=args.preprocessing,
            timeout_seconds=args.timeout_seconds, retrieval_profile=args.retrieval_profile,
            generation_profile=args.generation_profile, allow_cloud=args.cloud))
    # 公开回执只含相对产物路径、状态、指纹；原输入路径另存私有记录。
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
