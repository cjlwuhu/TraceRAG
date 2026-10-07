"""Single-user loopback console; credentials are write-only through settings."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import hashlib
from ipaddress import ip_address
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Literal, Optional
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, StrictBool, ValidationError, confloat, conint, constr
import yaml

from easyrag.console.store import Catalog, locate, now, read_json, write_json
from easyrag.console.lifecycle import Lifecycle, LOCK, locked_call, serialized
from easyrag.console.settings import ServiceSettings, SettingsRequest
from easyrag.console.diagnostics import ConsoleProblem, Journal, describe_error, rca_problem
from easyrag.domain.experiment import ExperimentConfig, StrictModel
from easyrag.domain.work_order import GenerationConfig, WorkOrderRecord, WorkOrderDraft, cited_statements
from easyrag.generation.work_order import WorkOrderGenerator, DraftValidationError, parse_model_draft, render_markdown, markdown_escape
from easyrag.retrieval.evidence_pack import fingerprint
from easyrag.retrieval.cloud_models import CloudModelError
from run_operations import build_runner
from verify_work_order import audit as audit_work_order


class RunRequest(StrictModel):
    event_id: Optional[constr(strict=True, regex=r"^import-[a-f0-9]{32}$")] = None
    corpus_mode: Literal["current", "event_snapshot"] = "current"
    knowledge_system: Optional[constr(strict=True, min_length=1, max_length=100)] = None
    query: constr(strict=True, strip_whitespace=True, min_length=1, max_length=10000)
    retrieval: ExperimentConfig = Field(default_factory=ExperimentConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)


class ConfigRequest(StrictModel):
    retrieval: ExperimentConfig
    generation: GenerationConfig


class ProbeRequest(StrictModel):
    service: Literal["qwen", "glm"]


class SignalOptions(StrictModel):
    system: constr(strict=True, strip_whitespace=True, min_length=1, max_length=100) = "online-boutique"
    detection_enabled: StrictBool = True
    threshold: confloat(gt=0, le=1000) = 6.0
    warmup_points: conint(strict=True, ge=20, le=100000) = 300
    consecutive_points: conint(strict=True, ge=1, le=10000) = 3
    minimum_metrics: conint(strict=True, ge=1, le=100000) = 1
    max_gap_seconds: conint(strict=True, ge=1, le=86400) = 5
    trigger_timestamp: Optional[conint(strict=True, ge=0)] = None
    rca_enabled: StrictBool = True
    baro: StrictBool = True
    epsilon: StrictBool = False
    summaries_enabled: StrictBool = True
    preprocessing: Literal["strict", "causal_ffill5_zero_v1"] = "strict"

    def profile(self):
        if self.detection_enabled and self.trigger_timestamp is not None:
            raise ConsoleProblem("signal_options", "检测模式与人工时间冲突", "开启异常检测时清空人工事件时间。")
        if not self.detection_enabled and self.trigger_timestamp is None:
            raise ConsoleProblem("signal_options", "缺少人工事件时间", "关闭异常检测时，请填写整秒 Unix 事件时间。")
        if self.rca_enabled and not (self.baro or self.epsilon):
            raise ConsoleProblem("signal_options", "未选择 RCA 方法", "至少启用 BARO 或 ε-Diagnosis。")
        return {"system": self.system,
            # 参数原样传给时序项目，并写入每次任务记录，便于对照实验复现。
            "detection": {"enabled": self.detection_enabled, "threshold": self.threshold,
                          "warmup_points": self.warmup_points,
                          "consecutive_points": self.consecutive_points,
                          "minimum_metrics": self.minimum_metrics,
                          "max_gap_seconds": self.max_gap_seconds},
            "trigger": {"source": "operator", "timestamp_unix": self.trigger_timestamp},
            "rca": {"enabled": self.rca_enabled, "methods": {"baro": self.baro, "epsilon": self.epsilon}},
            "summaries": {"enabled": self.summaries_enabled}}


class TelemetryRequest(StrictModel):
    csv: constr(strict=True, min_length=1, max_length=6000000)
    options: SignalOptions = Field(default_factory=SignalOptions)


class CsvNormalizationSummary(StrictModel):
    policy: Literal["duplicate_time_identical_v1"]
    raw_rows: conint(strict=True, ge=1)
    original_columns: conint(strict=True, ge=2)
    normalized_columns: conint(strict=True, ge=2)
    dropped_column_indices_zero_based: list[conint(strict=True, ge=0)]


class ClaimReview(StrictModel):
    claim_index: conint(strict=True, ge=0)
    support: Literal["supported", "unsupported", "uncertain"]
    note: constr(strict=True, max_length=1600) = ""


class ReviewRequest(StrictModel):
    draft: WorkOrderDraft
    reviewer: constr(strict=True, strip_whitespace=True, min_length=1, max_length=100)
    decision: Literal["needs_revision", "reviewed_draft"]
    notes: constr(strict=True, strip_whitespace=True, min_length=1, max_length=5000)
    claims: list[ClaimReview] = Field(..., min_items=1, max_items=30)
    parent_revision_id: Optional[constr(strict=True, regex=r"^rev-[a-f0-9]{32}$")] = None


class JobQueue:
    """One worker, bounded admission; restart marks interrupted jobs, never retries cloud."""
    def __init__(self, catalog, journal):
        self.catalog = catalog
        self.journal = journal
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ops-console")
        self.lock = LOCK
        self.pending = 0

    def recover(self):
        for path in self.catalog.jobs.glob("job-*/job.json"):
            item = read_json(path)
            if item["status"] in {"queued", "running"}:
                item.update(status="interrupted", phase="服务曾中断；请检查已有产物后手动重试", updated_at_utc=now())
                write_json(path, item, replace=True)
                self.journal.add("warning", "服务中断，任务未自动重试", job_id=item["id"], phase=item["phase"])

    def get(self, ident):
        return read_json(locate(self.catalog.jobs, ident, "job") / "job.json")

    def update(self, ident, **changes):
        value = self.get(ident)
        value.update(changes, updated_at_utc=now())
        write_json(self.catalog.jobs / ident / "job.json", value, replace=True)
        if "phase" in changes:
            self.journal.add("error" if changes.get("status") == "failed" else "info",
                changes.get("error", changes["phase"]), phase=changes["phase"], job_id=ident,
                problem=changes.get("problem"))

    def submit(self, kind, request, worker):
        with self.lock:
            if self.pending >= 4:
                raise HTTPException(429, "队列已满，请等待已有实验完成")
            ident = "job-" + uuid.uuid4().hex
            write_json(self.catalog.jobs / ident / "job.json", {"id": ident, "kind": kind,
                "status": "queued", "phase": "排队等待", "created_at_utc": now(),
                "updated_at_utc": now(), "request": request})
            self.pending += 1
            self.journal.add("info", "任务已提交", job_id=ident, phase="排队等待")

        def execute():
            terminal = {}
            try:
                self.update(ident, status="running", phase="准备输入")
                result = worker(ident)
                terminal = {"status": "completed", "phase": "完成", "result": result}
            except Exception as exc:
                problem = describe_error(exc)
                problem["phase"] = self.get(ident).get("phase")
                terminal = {"status": "failed", "phase": "运行失败", "error_type": type(exc).__name__,
                    "error": problem["message"], "problem": problem,
                    "generation_run_id": getattr(exc, "generation_run_id", None)}
            finally:
                with self.lock:
                    try:
                        self.update(ident, **terminal)
                    finally:
                        self.pending -= 1
        self.pool.submit(execute)
        return self.get(ident)


async def payload(request, model=None):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "仅接受 application/json")
    chunks, length = [], 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > 8_000_000:
            raise HTTPException(413, "上传上限为 8 MB")
        chunks.append(chunk)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("non-finite JSON value")
    try:
        data = json.loads(b"".join(chunks), object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(data, dict):
            raise ValueError("JSON object required")
        return model.parse_obj(data) if model else data
    except ValidationError as exc:
        raise HTTPException(422, describe_error(exc)) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "JSON 或字段校验失败；请检查类型、范围和额外字段") from None


def _normalized_hostname(value):
    if not isinstance(value, str) or not value or len(value) > 253 or not re.fullmatch(r"[A-Za-z0-9.:-]+", value):
        raise ValueError("Allowed hosts must be exact hostnames without schemes, ports, or wildcards")
    try:
        return str(ip_address(value))
    except ValueError:
        if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                   for label in value.split(".")):
            raise ValueError("Allowed hosts must be exact hostnames without schemes, ports, or wildcards") from None
        return value.lower()


def create_app(root=None, *, allow_cloud=False, rca_root=None, rca_python=None, allowed_hosts=()):
    if isinstance(allowed_hosts, (str, bytes)) or allowed_hosts is None:
        raise ValueError("Allowed hosts must be a collection of exact hostnames")
    hostnames = {"127.0.0.1", "localhost", "::1"}
    hostnames.update(_normalized_hostname(value) for value in allowed_hosts)
    root = Path(root) if root else Path(__file__).resolve().parents[3]
    config_path = root / "src/configs/easyrag.operations.windows.yaml"
    settings = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    rca_root = Path(rca_root) if rca_root else root.parent / "RCA"
    rca_python = str(rca_python or sys.executable)
    catalog, token = Catalog(root), secrets.token_urlsafe(32)
    journal = Journal(catalog.state / "logs.json")
    services = ServiceSettings(catalog.state, settings, allow_cloud=allow_cloud)
    queue = JobQueue(catalog, journal)
    lifecycle = Lifecycle(catalog, queue)

    @asynccontextmanager
    async def lifespan(app):
        # An OS lease prevents a second local process from interrupting live jobs.
        catalog.state.mkdir(parents=True, exist_ok=True)
        lease = (catalog.state / "console.lock").open("a+b")
        if os.name == "nt":
            import msvcrt
            lease.seek(0, 2)
            if lease.tell() == 0:
                lease.write(b"0")
                lease.flush()
            lease.seek(0)
            msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            queue.recover()
            yield
        finally:
            await asyncio.to_thread(queue.pool.shutdown, wait=True)
            lease.close()

    app = FastAPI(title="Network operations research console", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.catalog, app.state.queue = catalog, queue
    app.state.services = services
    app.state.lifecycle = lifecycle

    @app.middleware("http")
    async def local_boundary(request, call_next):
        hosts = request.headers.getlist("host")
        host = hosts[0] if len(hosts) == 1 else ""
        hostname = None
        if re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[A-Fa-f0-9:.]+\])(?::[0-9]{1,5})?", host):
            try:
                authority = urlsplit("http://" + host)
                hostname = _normalized_hostname(authority.hostname or "")
                _ = authority.port  # Validate the numeric port range before admitting the Host.
            except ValueError:
                hostname = None
        if hostname not in hostnames:
            return JSONResponse({"detail": "仅允许本机或显式配置的主机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{host}":
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
                request.headers.get("x-console-token", ""), token):
            return JSONResponse({"detail": "操作令牌无效，请刷新页面"}, status_code=403)
        try:
            response = await call_next(request)
        except Exception as exc:
            problem = describe_error(exc)
            journal.add("error", problem["message"], phase=request.url.path, problem=problem)
            response = JSONResponse({"detail": problem}, status_code=500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        problem = describe_error(exc)
        journal.add("error", problem["message"], phase=request.url.path, problem=problem)
        return JSONResponse({"detail": problem}, status_code=503 if problem["code"] == "cloud_disabled" else 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        problem = exc.detail if isinstance(exc.detail, dict) else {
            "code": "http_" + str(exc.status_code), "message": exc.detail, "hint": "", "details": []}
        journal.add("error", problem["message"], phase=request.url.path, problem=problem)
        return JSONResponse({"detail": problem}, status_code=exc.status_code)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        problem = describe_error(exc)
        journal.add("error", problem["message"], phase=request.url.path, problem=problem)
        return JSONResponse({"detail": problem}, status_code=404)

    @app.get("/api/bootstrap")
    def bootstrap():
        prefs = services.preferences()
        generation = GenerationConfig.parse_obj(settings.get("work_order", {})).dict()
        generation["model"] = prefs.glm_model if prefs.generation_provider == "glm" else prefs.qwen_model
        return {"token": token, "cloud_enabled": prefs.cloud_enabled,
            "telemetry_enabled": (rca_root / "scripts/run_signal_workflow.py").is_file(),
            "retrieval": ExperimentConfig.parse_obj(settings.get("operations", {})).dict(),
            "generation": generation,
            "retrieval_schema": ExperimentConfig.schema(), "generation_schema": GenerationConfig.schema(),
            "signal_options": SignalOptions().dict(), "signal_schema": SignalOptions.schema(),
            "mode": "local_single_user", "history_limit": 100}

    @app.get("/api/settings")
    def get_settings():
        return services.public()

    @app.post("/api/settings")
    async def save_settings(request: Request):
        data = await payload(request, SettingsRequest)
        result = await asyncio.to_thread(services.save, data)
        journal.add("info", "服务设置已保存", phase="设置")
        return result

    @app.get("/api/logs")
    def logs():
        return journal.read()

    @app.post("/api/settings/test")
    async def test_service(request: Request):
        data = await payload(request, ProbeRequest)
        prefs = services.preferences()
        retrieval = ExperimentConfig()
        generation = GenerationConfig(enabled=data.service == "glm", mode="cloud", model=prefs.glm_model)
        if data.service == "qwen":
            retrieval = ExperimentConfig.parse_obj({"retrieval": {"mode": "dense"}, "embedding": {"enabled": True}})
        runtime, chat = services.runtime(retrieval, generation)
        def probe():
            if data.service == "qwen":
                runtime[0].embed(["connection test"], dimension=64, text_type="query")
            else:
                from easyrag.generation.cloud_chat import generate_json
                generate_json(chat, [{"role": "user", "content": 'Return JSON: {"ok":true}'}], generation)
        await asyncio.to_thread(probe)
        journal.add("info", data.service.upper() + " 连接测试通过", phase="连接测试")
        return {"ok": True, "service": data.service}

    @app.get("/api/events")
    @serialized
    def events():
        return catalog.events()

    @app.delete("/api/events/{ident}")
    def delete_event(ident: str):
        return lifecycle.delete_event(ident)

    @app.delete("/api/orders/{ident}")
    def delete_order(ident: str):
        return lifecycle.delete_order(ident)

    @app.get("/api/knowledge/cases")
    def cases():
        return lifecycle.cases()

    @app.delete("/api/knowledge/cases/{ident:path}")
    def delete_case(ident: str):
        return lifecycle.delete_case(ident)

    @app.get("/api/knowledge")
    @serialized
    def knowledge():
        info = catalog.knowledge.description()
        # Public UI gets version/counts, not server filesystem source paths.
        return {k: v for k, v in info.items() if k != "sources"}

    @app.post("/api/validate-config")
    async def validate_config(request: Request):
        return (await payload(request, ConfigRequest)).dict()

    @app.post("/api/knowledge/cases", status_code=201)
    async def admit_human_case(request: Request):
        from easyrag.console.research import admit_case
        return await asyncio.to_thread(admit_case, catalog, await payload(request))

    @app.get("/api/events/{ident}/evidence")
    @serialized
    def event_evidence(ident: str):
        from easyrag.console.research import observations
        return {"items": [d.to_dict() for d in observations(catalog, ident)]}

    @app.post("/api/events/{ident}/evidence", status_code=201)
    async def attach_event_evidence(ident: str, request: Request):
        from easyrag.console.research import attach
        return await asyncio.to_thread(attach, catalog, ident, await payload(request))

    def image_response(sha):
        from easyrag.console.research import asset_path
        from PIL import Image
        path = asset_path(catalog.root, sha)
        with Image.open(path) as im:
            fmt = im.format
            if fmt not in {"PNG", "JPEG"}: raise HTTPException(422, "仅预览 PNG/JPEG 图像")
            im.verify()
        return Response(path.read_bytes(), media_type="image/png" if fmt == "PNG" else "image/jpeg")

    @app.get("/api/events/{ident}/assets/{sha}")
    @serialized
    def image_asset(ident: str, sha: str):
        from easyrag.console.research import observations
        if not re.fullmatch(r"[a-f0-9]{64}", sha): raise ValueError("invalid asset hash")
        found = [d for d in observations(catalog, ident) if d.knowledge_type == "image" and d.metadata["asset_sha256"] == sha]
        if not found: raise HTTPException(404, "该事件没有这张图像")
        return image_response(sha)

    @app.get("/api/orders/{ident}/assets/{sha}")
    @serialized
    def order_image_asset(ident: str, sha: str):
        if not re.fullmatch(r"[a-f0-9]{64}", sha): raise ValueError("invalid asset hash")
        folder, data = order_data(ident)
        audit_work_order(folder)
        record = WorkOrderRecord.parse_obj(data)
        pack = read_json(folder / "evidence-pack.json")
        recorded = {e.evidence_id for e in record.evidence if e.type == "image" and e.metadata.get("asset_sha256") == sha}
        found = any(item["evidence"]["evidence_id"] in recorded and item["evidence"]["type"] == "image"
                    and item["evidence"]["metadata"].get("asset_sha256") == sha for item in pack["pack"]["items"])
        if not found: raise HTTPException(404, "该工单没有这张图像")
        return image_response(sha)

    @app.post("/api/orders/{ident}/annotations", status_code=201)
    async def annotate_relevance(ident: str, request: Request):
        from easyrag.console.research import annotate
        return await asyncio.to_thread(annotate, catalog, ident, await payload(request))

    @app.get("/api/events/{ident}")
    @serialized
    def event(ident: str):
        incident, _ = catalog.event(ident)
        return incident

    @app.post("/api/events", status_code=201)
    async def import_event(request: Request):
        data = await payload(request)
        return await asyncio.to_thread(locked_call, catalog.import_bundle, data)

    @app.post("/api/telemetry", status_code=202)
    async def telemetry(request: Request):
        data = await payload(request, TelemetryRequest)
        csv_bytes = data.csv.encode("utf-8")
        profile = data.options.profile()
        if not (rca_root / "scripts/run_signal_workflow.py").is_file():
            raise HTTPException(503, "RCA 文件接口尚未配置")
        def work(ident):
            folder = catalog.jobs / ident
            # Ignore original upload filenames entirely; they may contain benchmark labels.
            (folder / "telemetry.private.csv").write_bytes(csv_bytes)
            write_json(folder / "signal-profile.json", profile)
            queue.update(ident, phase="时序检测 → RCA → 指标摘要")
            completed = subprocess.run([rca_python, str(rca_root / "scripts/run_signal_workflow.py"),
                "--telemetry", str(folder / "telemetry.private.csv"),
                "--profile", str(folder / "signal-profile.json"), "--output-dir", str(folder / "signals"),
                "--preprocessing", data.options.preprocessing],
                cwd=rca_root, capture_output=True, timeout=600, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if completed.returncode:
                raise rca_problem(completed.stderr)
            paths = list((folder / "signals").glob("signal-run-*/signal-bundle.json"))
            if len(paths) != 1:
                raise ValueError("RCA must produce exactly one bundle")
            bundle = read_json(paths[0])
            # 只公开处理计数；列名、单元格和本机路径留在 RCA 的私有审计产物。
            normalization_path = paths[0].parent / "normalization-report.json"
            extra = {}
            if normalization_path.is_file():
                report = read_json(normalization_path)
                summary = CsvNormalizationSummary.parse_obj({
                    key: report[key] for key in CsvNormalizationSummary.__fields__})
                extra["csv_normalization"] = summary.dict()
            # 没有告警或观测窗口不完整时只返回阶段状态，不能生成貌似完整的工单。
            if bundle["analysis"].get("status") != "complete":
                return {"rag_ready": False, "detection": bundle["detection"], "analysis": bundle["analysis"], **extra}
            queue.update(ident, phase="导入已完成事件")
            return {"rag_ready": True, **catalog.import_bundle(bundle), **extra}
        return await asyncio.to_thread(queue.submit, "telemetry", {"options": data.options.dict(),
                            "csv_sha256": hashlib.sha256(csv_bytes).hexdigest()}, work)

    @app.post("/api/runs", status_code=202)
    async def run(request: Request):
        data = await payload(request, RunRequest)
        return await asyncio.to_thread(submit_run, data)

    @serialized
    def submit_run(data):
        incident, corpus, corpus_info = catalog.query_corpus(data.event_id, mode=data.corpus_mode, system=data.knowledge_system)
        cloud_requested = (data.retrieval.embedding.enabled or data.retrieval.reranker.enabled or
                           (data.generation.enabled and data.generation.mode == "cloud"))
        runtime, chat = services.runtime(data.retrieval, data.generation)
        if not data.retrieval.save_intermediates or not data.generation.save_intermediates:
            raise HTTPException(422, "网页实验必须保存留痕；请启用两个 save_intermediates 开关")
        async def pipeline(ident):
            queue.update(ident, phase="建立事件索引与检索")
            runner = await build_runner(config_path, corpus, allow_cloud=cloud_requested, cloud_runtime=runtime)
            pack = await runner.run(data.query, incident=incident, overrides=data.retrieval.dict())
            queue.update(ident, phase="生成待审核工单")
            generator = WorkOrderGenerator(models=chat, output_dir=catalog.orders)
            order = await generator.run(pack, overrides=data.generation.dict())
            return {"generation_run_id": order["generation_run_id"], "pack_id": pack["pack_id"],
                    "evidence_count": len(pack["pack"]["items"]), "status": order["status"], "corpus": corpus_info}
        return queue.submit("rag", {**data.dict(), "corpus": corpus_info}, lambda ident: asyncio.run(pipeline(ident)))

    @app.get("/api/jobs")
    @serialized
    def jobs():
        items = [read_json(path) for path in catalog.jobs.glob("job-*/job.json")]
        return sorted(items, key=lambda item: item.get("created_at_utc", ""), reverse=True)[:100]

    @app.get("/api/jobs/{ident}")
    @serialized
    def job(ident: str):
        return queue.get(ident)

    def order_data(ident):
        folder = locate(catalog.orders, ident, "gen")
        return folder, read_json(folder / "work-order.json")

    @app.get("/api/orders")
    @serialized
    def orders():
        items = []
        paths = sorted(catalog.orders.glob("gen-*/work-order.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
        for path in paths:
            folder, data = order_data(path.parent.name)
            try:
                WorkOrderRecord.parse_obj(data)
                compatible = True
            except ValidationError:
                compatible = False
            items.append({"id": data["generation_run_id"], "created_at_utc": data["created_at_utc"],
                "incident_id": data["incident_id"], "status": data["status"], "mode": data["config"]["mode"],
                "compatible": compatible, "has_engineering_review": (folder / "engineering-review.json").is_file()})
        return items

    @app.get("/api/orders/{ident}")
    @serialized
    def order(ident: str):
        folder, data = order_data(ident)
        try:
            WorkOrderRecord.parse_obj(data)
            compatible = True
        except ValidationError:
            compatible = False
        review = folder / "engineering-review.json"
        return {"record": data, "compatible": compatible,
            "pack": read_json(folder / "evidence-pack.json"),
            "engineering_review": read_json(review) if review.is_file() else None,
            "revisions": [read_json(p) for p in sorted((catalog.reviews / ident).glob("rev-*/review.json"))]}

    @app.get("/api/orders/{ident}/download/{format}")
    @serialized
    def download(ident: str, format: Literal["json", "md"]):
        folder, _ = order_data(ident)
        return Response((folder / ("work-order." + format)).read_bytes(),
            media_type="application/json" if format == "json" else "text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="' + ident + "." + format + '"'})

    @app.post("/api/orders/{ident}/reviews", status_code=201)
    async def review(ident: str, request: Request):
        data = await payload(request, ReviewRequest)
        return await asyncio.to_thread(save_review, ident, data)

    @serialized
    def save_review(ident, data):
        folder, original = order_data(ident)
        record = WorkOrderRecord.parse_obj(original)
        audit_work_order(folder)  # Hashes, saved prompt, final evidence and Markdown must still agree.
        if record.status != "draft":
            raise HTTPException(422, "只有兼容的工单草稿可以复核")
        draft = parse_model_draft(data.draft.dict(), record.evidence, record.config)
        count = len(list(cited_statements(draft)))
        if sorted(x.claim_index for x in data.claims) != list(range(count)):
            raise HTTPException(422, "每个修改后事实条目必须且只能有一个复核标注")
        if data.decision == "reviewed_draft" and any(x.support != "supported" for x in data.claims):
            raise HTTPException(422, "仍有不确定/不支持条目，只能保存为需修订")
        parent_hash = fingerprint(original)
        if data.parent_revision_id:
            parent = read_json(locate(catalog.reviews / ident, data.parent_revision_id, "rev") / "review.json")
            parent_hash = fingerprint(parent)
        revision = "rev-" + uuid.uuid4().hex
        artifact = {"schema_version": "1.0", "revision_id": revision, "generation_run_id": ident,
            "created_at_utc": now(), "source_record_sha256": fingerprint(original), "parent_sha256": parent_hash,
            **data.dict(), "reviewer_identity": "self_asserted_local_user", "root_cause_status": "unconfirmed",
            "actions_executed": False, "case_promoted": False}
        artifact["revision_sha256"] = fingerprint(artifact)
        destination = catalog.reviews / ident / revision
        write_json(destination / "review.json", artifact)
        edited = record.copy(update={"draft": draft})
        markdown = ("# 人工修订稿（原始生成记录未修改）\n\n"
            + "修订 ID：" + revision + "\n\n"
            + "复核人（本地自报身份）：" + markdown_escape(data.reviewer) + "\n\n"
            + "复核状态：" + data.decision + "\n\n"
            + "意见：" + markdown_escape(data.notes) + "\n\n"
            + "条目支持标注：" + "; ".join(f"{x.claim_index + 1}: {x.support}" for x in data.claims) + "\n\n"
            + "下方生成 ID / 工单 ID 标识来源原稿，不是修订稿的内容哈希。\n\n"
            + render_markdown(edited))
        (destination / "review.md").write_text(markdown, encoding="utf-8")
        return artifact

    @app.get("/api/orders/{ident}/reviews/{revision}/download/{format}")
    @serialized
    def download_review(ident: str, revision: str, format: Literal["json", "md"]):
        order_data(ident)
        folder = locate(catalog.reviews / ident, revision, "rev")
        return Response((folder / ("review." + format)).read_bytes(),
            media_type="application/json" if format == "json" else "text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="' + revision + "." + format + '"'})

    @app.exception_handler(CloudModelError)
    async def cloud_error(request, exc):
        problem = describe_error(exc)
        journal.add("error", problem["message"], phase=request.url.path, problem=problem)
        return JSONResponse({"detail": problem}, status_code=422 if isinstance(exc, DraftValidationError) else 502)

    # Mount last: unknown API paths must not fall through to an HTML SPA response.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    def unknown_api(path: str):
        raise HTTPException(404, "未知接口")

    dist = root / "web/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="console")
    else:
        @app.get("/")
        def not_built():
            return Response("Frontend not built. Run npm ci and npm run build in web/.", status_code=503)
    return app
