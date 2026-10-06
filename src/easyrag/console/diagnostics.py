"""Actionable, credential-free diagnostics and a bounded local event journal."""

import re
import subprocess
import threading
import uuid

from pydantic import ValidationError

from easyrag.console.store import now, read_json, write_json
from easyrag.retrieval.cloud_models import CloudModelError


class ConsoleProblem(ValueError):
    def __init__(self, code, message, hint, *, service=None, details=None):
        super().__init__(message)
        self.problem = {"code": code, "message": message, "hint": hint,
                        "service": service, "details": details or []}


def describe_error(exc):
    if isinstance(exc, ConsoleProblem):
        return exc.problem
    if isinstance(exc, ValidationError):
        # Never include input values, validator contexts or provider response bodies.
        known = {
            "embedding.enabled must be true exactly for dense/hybrid mode": "Dense / Hybrid 必须开启 Embedding，BM25 必须关闭 Embedding。",
            "reranker.candidate_top_k must cover pack.top_k": "重排候选数量不能小于最终证据数量。",
            "Unsupported embedding dimension": "Embedding 维度需为 64/128/256/512/768/1024/1536/2048。",
            "invalid credential format": "密钥格式无效；Qwen 密钥应以 sk- 开头，密钥不能包含空格或换行。",
            "use a loopback HTTP proxy URL": "代理请填写 http://127.0.0.1:端口，或留空直连。",
            "cannot replace and clear the same credential": "不能同时替换和清除同一密钥。",
        }
        fields = [".".join(str(p) for p in e["loc"]) + " · " + known.get(e["msg"], e["type"]) for e in exc.errors()[:12]]
        return {"code": "validation", "message": "字段校验失败", "hint": "检查以下字段的类型、范围或必填项。", "details": fields}
    code, status, service = getattr(exc, "code", None), getattr(exc, "status", None), getattr(exc, "service", None)
    if isinstance(exc, CloudModelError):
        # Legacy adapters already sanitize their errors; inspect, never echo the text.
        legacy = str(exc)
        match = re.search(r"HTTP (\d{3})", legacy)
        status = status or (int(match[1]) if match else None)
        if status in (401, 403):
            message, hint = "API 鉴权失败", "在设置中检查对应服务的 API Key、地域和模型权限。"
        elif status == 429:
            message, hint = "服务限流或额度不足", "检查服务商余额与并发限额，稍后手动重试。"
        elif status in (400, 404, 422):
            message, hint = "模型请求被拒绝", "检查模型名称、输入长度与账号是否支持该模型。"
        elif status:
            message, hint = "云服务返回错误", "查看服务商状态，稍后手动重试。"
        elif code == "timeout" or "Timeout" in legacy:
            code, message, hint = "timeout", "模型请求超时", "检查网络或代理，必要时在设置中增加超时时间。"
        elif code == "network" or "network request failed" in legacy:
            code, message, hint = "network", "无法连接模型服务", "检查网络、代理端口与服务地域。"
        elif "key" in legacy.lower() or "credential" in legacy.lower():
            code, message, hint = "credential", "API Key 未配置或不可读取", "在设置中重新输入并保存对应服务的 API Key。"
        else:
            code, message, hint = "model_validation", "模型输出或引用校验失败", "检查实验记录中的生成审计；调整输入、模型或输出预算后重试。"
        return {"code": code or "provider_http", "message": message, "hint": hint,
                "service": service, "http_status": status, "details": []}
    if isinstance(exc, subprocess.TimeoutExpired):
        return {"code": "rca_timeout", "message": "时序处理超时", "hint": "缩小 CSV 范围或减少 RCA 方法后重试。", "details": []}
    if isinstance(exc, FileNotFoundError):
        return {"code": "not_found", "message": "未找到所需文件或运行环境", "hint": "刷新事件列表；检查 RCA 环境和产物是否存在。", "details": []}
    if isinstance(exc, ValueError):
        return {"code": "invalid_input", "message": "输入或配置未通过校验", "hint": "检查事件数据、实验参数及证据引用。", "details": []}
    return {"code": "internal", "message": "运行发生内部错误", "hint": "记录任务 ID 和失败阶段，检查本地服务与依赖。", "details": []}


def rca_problem(stderr):
    # Match known validation categories; untrusted CSV contents never reach logs.
    text = stderr.decode("utf-8", errors="replace").lower()
    if "conflicting duplicate time columns" in text:
        return ConsoleProblem("rca_conflicting_time_columns", "CSV 的重复时间列不一致",
            "重复的 time 列必须逐行完全一致才能合并；请核对采集来源并修正冲突，不能把第二列当作指标。",
            service="RCA")
    # 兼容旧 RCA 入口，也说明新入口对重复非时间指标的拒绝规则。
    if "duplicate csv column names" in text or "duplicate csv columns are not allowed" in text:
        return ConsoleProblem("rca_duplicate_columns", "CSV 存在重复列名",
            "指标列名必须唯一；新版 RCA 会合并逐行完全一致的重复 time 列，有冲突的时间列和重复指标仍需修正。",
            service="RCA")
    if "time" in text or "timestamp" in text:
        hint = "CSV 需要 time 列，使用按时间递增、无重复的整秒 Unix 时间戳。"
    elif any(word in text for word in ("nan", "finite", "numeric", "float", "convert", "number")):
        hint = "指标列必须为有限数值，请检查空值、文本、NaN 和无穷值。"
    elif "window" in text or "baseline" in text:
        hint = "事件前后需要足够的时间窗口；检查人工时间和 CSV 覆盖范围。"
    elif "modulenotfound" in text or "importerror" in text:
        hint = "RCA Python 环境缺少依赖，请检查服务启动时的 RCA 环境。"
    else:
        hint = "检查 CSV 时间列、数值指标、检测配置和 RCA 运行环境。"
    return ConsoleProblem("rca_input", "CSV / RCA 处理失败", hint, service="RCA")


class Journal:
    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()

    def read(self):
        with self.lock:
            return read_json(self.path) if self.path.exists() else []

    def add(self, level, message, *, phase=None, job_id=None, problem=None):
        with self.lock:
            item = {"id": uuid.uuid4().hex, "time": now(), "level": level, "message": message,
                    "phase": phase, "job_id": job_id, "problem": problem}
            write_json(self.path, [item, *self.read()][:500], replace=True)
            return item
