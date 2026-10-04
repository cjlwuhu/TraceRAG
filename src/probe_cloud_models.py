"""Optional, small LIVE API smoke test (billable); never prints credentials."""

import argparse
import json
import time

import numpy as np

from easyrag.retrieval.cloud_models import CloudModelError, DashScopeModels


def main(args):
    provider = DashScopeModels(api_host=args.api_host, key_file=args.key_file, proxy=args.proxy)
    started = time.perf_counter()
    try:
        vectors = provider.embed(["服务请求延迟升高，检查下游超时。", "数据库连接池等待时间异常。"], dimension=1024)
        result = {"embedding_model": "text-embedding-v4", "shape": list(vectors.shape),
                  "finite": bool(np.isfinite(vectors).all()), "norms": np.linalg.norm(vectors, axis=1).tolist()}
        if args.rerank:
            result["rerank_scores"] = provider.rerank("服务请求超时", ["下游请求出现超时日志", "用户修改了头像颜色"])
        result["elapsed_seconds"] = time.perf_counter() - started
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except CloudModelError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file")
    parser.add_argument("--api-host", default="https://dashscope.aliyuncs.com")
    parser.add_argument("--proxy")
    parser.add_argument("--rerank", action="store_true")
    main(parser.parse_args())
