"""CSV / signal bundle → isolated corpus → evidence pack → audited draft.

RCA runs in its own interpreter. Each run owns its artifacts and never writes
the active knowledge pointer or promotes a generated draft into a real case.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

import yaml

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from easyrag.adapters.signal_bundle import SignalEnvelope, convert_signal_bundle
from easyrag.domain.experiment import ExperimentConfig
from easyrag.domain.work_order import GenerationConfig
from easyrag.generation.work_order import WorkOrderGenerator
from easyrag.retrieval.evidence_pack import fingerprint


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "src/configs/easyrag.operations.windows.yaml"
DEFAULT_MANIFEST = ROOT / "examples/operations_knowledge/manifest.jsonl"
DEFAULT_OUTPUT = ROOT / "outputs/pipelines"


class PipelineProblem(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def bad_number(value):
        raise ValueError("nonfinite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_number)


def _python(value):
    executable = str(value or sys.executable)
    found = shutil.which(executable)
    if found:
        return os.path.abspath(Path(found).expanduser())
    path = Path(executable).expanduser()
    if path.is_file():
        return os.path.abspath(path)
    raise PipelineProblem("rca_python_missing", "找不到 RCA Python；请用 --rca-python 指定已配置的 Python 可执行文件。")


def _rca_paths(rca_root, rca_python):
    root = Path(rca_root or ROOT.parent / "RCA").expanduser().resolve()
    script = root / "scripts/run_signal_workflow.py"
    if not script.is_file():
        raise PipelineProblem("rca_missing", "未找到 RCA 文件接口；请准备独立 RCA 项目并用 --rca-root 指定其根目录。")
    return root, script, _python(rca_python)


def _execute(command, *, cwd, timeout):
    # 参数数组不经过 shell；路径中的空格和上传文件名都不能变成命令。
    # 子进程输出只留长度和最多 4096 字节的哈希，错误文本可能包含原 CSV 或密钥。
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            result = subprocess.run(command, cwd=cwd, stdout=out, stderr=err, timeout=timeout,
                check=False, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except subprocess.TimeoutExpired:
            raise PipelineProblem("rca_timeout", "RCA 子进程超时；请检查数据规模、算法配置或调整 --timeout-seconds。") from None
        except OSError:
            raise PipelineProblem("rca_launch_failed", "RCA 子进程无法启动；请检查 Python 可执行文件和项目目录权限。") from None
        stats = {"returncode": result.returncode}
        for name, stream in (("stdout", out), ("stderr", err)):
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(0)
            prefix = stream.read(4096)
            stats[name + "_bytes"] = size
            stats[name + "_prefix_sha256"] = hashlib.sha256(prefix).hexdigest()
        return stats


def check_rca(rca_root=None, rca_python=None, *, signal_profile=None, timeout_seconds=30):
    """Real subprocess imports for the chosen RCA profile; no cloud calls or data writes."""
    try:
        root, script, executable = _rca_paths(rca_root, rca_python)
        profile = str(Path(signal_profile).resolve()) if signal_profile else ""
        if profile and not Path(profile).is_file():
            raise PipelineProblem("profile_missing", "找不到信号配置；请检查 --signal-profile。")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise PipelineProblem("invalid_options", "超时时间必须是有限正数。")
        # 只输出版本和有限状态；不输出环境变量、异常原文或凭据内容。
        code = """import importlib.metadata as metadata,json,pathlib,sys
try:
 root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root))
 import numpy,pandas,pydantic,sklearn,yaml
 from src.signal_config import SignalConfig
 from src.signal_workflow import build_signal_bundle,load_telemetry
 config=SignalConfig.parse_obj(yaml.safe_load((root/'configs/signal_workflow.yaml').read_text(encoding='utf-8')))
 if sys.argv[2]:config=config.with_overrides(yaml.safe_load(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8')))
 methods=[]
 if config.rca.enabled and config.rca.methods.baro:
  from scripts.run_rcaeval import load_official_baro
  load_official_baro();methods.append('baro')
 if config.rca.enabled and config.rca.preprocessing=='rcaeval_re1':
  from scripts.run_rcaeval import load_official_preprocess
  load_official_preprocess()
 names=['numpy','pandas','pydantic','scikit-learn','PyYAML']
 if config.rca.enabled and config.rca.methods.epsilon:
  from pyrca.analyzers.epsilon_diagnosis import EpsilonDiagnosis
  names.append('sfr-pyrca');methods.append('epsilon')
 print(json.dumps({'status':'ready','versions':{n:metadata.version(n) for n in names},'methods':methods}))
except Exception:
 print(json.dumps({'status':'not_ready'}));sys.exit(1)
"""
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                completed = subprocess.run([executable, "-c", code, str(root), profile], cwd=root,
                    stdout=out, stderr=err, timeout=timeout_seconds, check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            except subprocess.TimeoutExpired:
                raise PipelineProblem("rca_timeout", "RCA 环境检查超时；请确认解释器可正常启动。") from None
            except OSError:
                raise PipelineProblem("rca_launch_failed", "无法启动 RCA Python；请检查 --rca-python。") from None
            out.seek(0)
            raw = out.read(8193)
        if completed.returncode or len(raw) > 8192:
            raise PipelineProblem("rca_dependencies", "RCA 依赖或固定源码未准备好；请按 RCA README 配置环境和 vendor/RCAEval 后重试。")
        try:
            result = _json(raw)
            if result.get("status") != "ready":
                raise ValueError("not ready")
        except (ValueError, UnicodeError, AttributeError):
            raise PipelineProblem("rca_dependencies", "RCA 依赖检查未返回有效结果；请检查独立 RCA 项目版本。") from None
        return {**result, "message": "RCA CLI、所选算法及 Python 依赖可导入；尚未执行数据分析。"}
    except PipelineProblem as exc:
        return {"status": "not_ready", "code": exc.code, "message": exc.message}


def _bundle(raw):
    try:
        value = _json(raw)
        envelope = SignalEnvelope.parse_obj(value)
        stable = {k: v for k, v in value.items() if k != "bundle_id"}
        stable["rca_runs"] = [{k: v for k, v in r.items() if k != "runtime_seconds"} for r in value["rca_runs"]]
        if "signal-" + fingerprint(stable)[:20] != envelope.bundle_id:
            raise ValueError("fingerprint mismatch")
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise PipelineProblem("bundle_invalid", "信号包格式、标签隔离或指纹校验失败；请重新运行 RCA 导出有效信号包。") from None


def _corpus(base_manifest, incident, summaries):
    documents = load_manifest(base_manifest)
    known, excluded_observations, excluded_systems, excluded_cases = {}, 0, 0, 0
    for document in documents:
        # 当前事件只接受本信号包的观测；长期库的其他事件观测一律排除。
        if document.knowledge_type not in {"runbook", "case"}:
            excluded_observations += 1
            continue
        if document.metadata.get("system") not in {None, incident["system"]}:
            excluded_systems += 1
            continue
        if document.knowledge_type == "case" and (document.source.startswith("synthetic") or
                document.raw_ref.startswith("fixture:") or document.metadata.get("verified_by", "").startswith("demo")):
            excluded_cases += 1
            continue
        if document.knowledge_id in known and known[document.knowledge_id].to_dict() != document.to_dict():
            raise ValueError("conflicting knowledge ID")
        known[document.knowledge_id] = document
    for summary in summaries:
        document = summary.to_knowledge_document()
        if document.knowledge_id in known and known[document.knowledge_id].to_dict() != document.to_dict():
            raise ValueError("conflicting metric ID")
        known[document.knowledge_id] = document
    return sorted(known.values(), key=lambda d: d.knowledge_id), {
        "documents": len(known), "metric_summaries": len(summaries),
        "excluded_other_event_observations": excluded_observations,
        "excluded_other_systems": excluded_systems, "excluded_synthetic_cases": excluded_cases,
        "base_knowledge_is_not_automatically_verified": True}


async def run_pipeline(*, telemetry=None, bundle=None, query="当前异常如何验证和处置",
        config_path=DEFAULT_CONFIG, base_manifest=DEFAULT_MANIFEST, output_root=DEFAULT_OUTPUT,
        rca_root=None, rca_python=None, signal_profile=None, preprocessing="strict",
        timeout_seconds=600, retrieval_profile=None, generation_profile=None, allow_cloud=False):
    """Return a persisted public status record; failures never include input/traceback text."""
    pipeline_id = "pipeline-" + uuid.uuid4().hex
    folder = None
    report = {"schema_version": "1.0", "pipeline_id": pipeline_id, "status": "running",
        "created_at_utc": now(), "input_kind": "telemetry" if telemetry else "bundle",
        "cloud_allowed": bool(allow_cloud), "fingerprints": {}, "stages": [], "artifacts": {},
        "limitations": ["检测结果和 RCA 排名是待验证候选，引用审计不证明根因正确。",
            "本流水线不会执行处置、修改长期知识入口或创建正式历史案例。"]}
    active_stage = None

    def stage(name):
        nonlocal active_stage
        if active_stage and active_stage["status"] == "running":
            active_stage.update(status="complete", finished_at_utc=now())
        active_stage = {"name": name, "status": "running", "started_at_utc": now()}
        report["stages"].append(active_stage)
        _save(folder / "pipeline.json", report)

    def finish(status):
        if active_stage:
            active_stage.update(status="failed" if status == "failed" else "complete", finished_at_utc=now())
        report.update(status=status, finished_at_utc=now())
        try:
            if folder is None:
                raise OSError("output initialization failed")
            _save(folder / "pipeline.json", report)
        except OSError:
            # 输出路径错误/磁盘不可写时也返回有限 JSON；不能靠再次写同一路径恢复。
            report.update(status="failed", persisted=False, error={
                "code": "artifact_write_failed", "message": "无法保存运行记录；请检查 --output-dir 是否为可写目录及磁盘空间。"})
        return report

    try:
        folder = Path(output_root).expanduser().resolve() / pipeline_id
        folder.mkdir(parents=True, exist_ok=False)
        stage("inputs")
        if bool(telemetry) == bool(bundle):
            raise PipelineProblem("invalid_options", "请仅提供 --telemetry 或 --bundle 其中之一。")
        if not isinstance(query, str) or not query.strip() or len(query) > 10000:
            raise PipelineProblem("invalid_options", "问题不能为空且不能超过 10000 字符。")
        if preprocessing not in {"strict", "causal_ffill5_zero_v1"}:
            raise PipelineProblem("invalid_options", "请选择受支持的时序预处理策略。")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise PipelineProblem("invalid_options", "超时时间必须是有限正数。")
        source = Path(telemetry or bundle).expanduser().resolve()
        if not source.is_file():
            raise PipelineProblem("input_missing", "找不到输入文件；请检查 --telemetry 或 --bundle 指定的文件。")
        input_bytes = source.read_bytes()
        report["fingerprints"].update(input_sha256=hashlib.sha256(input_bytes).hexdigest(), query_sha256=fingerprint(query))
        paths = {"input": str(source), "config": str(Path(config_path).resolve()),
            "base_manifest": str(Path(base_manifest).resolve()), "output_directory": str(folder)}
        _save(folder / "source-paths.private.json", paths)
        if telemetry:
            stage("telemetry_detection_rca")
            root, script, executable = _rca_paths(rca_root, rca_python)
            local_input = folder / ("telemetry.private" + source.suffix.lower())
            local_input.write_bytes(input_bytes)
            command = [executable, str(script), "--telemetry", str(local_input),
                "--output-dir", str(folder / "signals"), "--preprocessing", preprocessing]
            if signal_profile:
                profile = Path(signal_profile).expanduser().resolve()
                if not profile.is_file():
                    raise PipelineProblem("profile_missing", "找不到信号配置；请检查 --signal-profile。")
                # 冻结本次参数；后续外部配置修改不会改变已保存的运行输入。
                profile_bytes = profile.read_bytes()
                (folder / "signal-profile.private.yaml").write_bytes(profile_bytes)
                command.extend(["--profile", str(folder / "signal-profile.private.yaml")])
                paths["signal_profile"] = str(profile)
                report["fingerprints"]["signal_profile_sha256"] = hashlib.sha256(profile_bytes).hexdigest()
            paths.update(rca_root=str(root), rca_python=executable, command=command)
            _save(folder / "source-paths.private.json", paths)
            report["subprocess"] = await asyncio.to_thread(_execute, command, cwd=root, timeout=timeout_seconds)
            if report["subprocess"]["returncode"]:
                raise PipelineProblem("rca_failed", "RCA 运行失败；请先用 --check 检查依赖，再在独立 RCA 项目核对 CSV 与信号配置。")
            bundles = list((folder / "signals").glob("signal-run-*/signal-bundle.json"))
            if len(bundles) != 1:
                raise PipelineProblem("rca_output_invalid", "RCA 必须产出一份信号包；请检查 CLI 版本及输出目录。")
            raw = bundles[0].read_bytes()
        else:
            if signal_profile:
                raise PipelineProblem("invalid_options", "--signal-profile 仅用于 CSV 分析；信号包复用其已保存配置。")
            _save(folder / "source-paths.private.json", paths)
            raw = input_bytes
        stage("signal_contract")
        payload = _bundle(raw)
        (folder / "signal-bundle.json").write_bytes(raw)
        report["artifacts"]["signal_bundle"] = "signal-bundle.json"
        report["fingerprints"].update(signal_bundle_sha256=hashlib.sha256(raw).hexdigest(),
            telemetry_sha256=payload["telemetry"].get("sha256"), pipeline_code_sha256=_digest(Path(__file__)))
        report["signal"] = {"bundle_id": payload["bundle_id"], "analysis_status": payload["analysis"].get("status"),
            "detection_status": payload["detection"].get("status"),
            "required_analysis_delay_seconds": payload["analysis"].get("required_analysis_delay_seconds")}
        status = payload["analysis"].get("status")
        if status == "no_event":
            return finish("no_event")
        if status in {"awaiting_observation_window", "insufficient_reference", "insufficient_points"}:
            return finish("awaiting")
        if status != "complete":
            raise PipelineProblem("bundle_invalid", "信号包包含不支持的分析状态；请检查 RCA 导出版本。")
        try:
            incident, summaries = convert_signal_bundle(payload, hashlib.sha256(raw).hexdigest())
        except (ValueError, TypeError, KeyError):
            raise PipelineProblem("bundle_invalid", "完整事件的身份、时间窗口或摘要校验失败。") from None
        stage("incident_corpus")
        _save(folder / "incident.json", incident)
        report["artifacts"]["incident"] = "incident.json"
        base = Path(base_manifest).resolve()
        if not base.is_file():
            raise PipelineProblem("knowledge_missing", "找不到知识清单；请用 --base-manifest 指定有效 manifest.jsonl。")
        documents, report["corpus"] = _corpus(base, incident, summaries)
        manifest = folder / "corpus/manifest.jsonl"
        if documents:
            write_manifest(documents, manifest)
        else:
            manifest.parent.mkdir(parents=True)
            manifest.write_bytes(b"")
        report["fingerprints"].update(base_manifest_sha256=_digest(base), corpus_sha256=_digest(manifest))
        report["artifacts"]["corpus_manifest"] = "corpus/manifest.jsonl"
        stage("configuration")
        config = Path(config_path).resolve()
        settings = yaml.safe_load(config.read_text(encoding="utf-8"))
        # 只复制实际消费者需要的配置；旧框架 llm_keys 等凭据不落入运行配置。
        local_settings = {key: settings[key] for key in ("chunk_size", "chunk_overlap", "split_type", "stopwords_path",
            "cloud_services", "operations") if key in settings}
        local_settings["operations_output_dir"] = str(folder / "retrieval")
        local_settings["data_path"] = str(manifest.parent)
        if local_settings.get("cloud_services"):
            local_settings["cloud_services"] = {**local_settings["cloud_services"],
                "cache_path": str(folder / "embedding_cache.sqlite")}
        retrieval = ExperimentConfig.parse_obj(settings.get("operations", {}))
        generation = GenerationConfig.parse_obj(settings.get("work_order", {}))
        for name, profile in (("retrieval", retrieval_profile), ("generation", generation_profile)):
            if profile:
                override = yaml.safe_load(Path(profile).read_text(encoding="utf-8"))
                if name == "retrieval":
                    retrieval = retrieval.with_overrides(override)
                else:
                    generation = generation.with_overrides(override)
                report["fingerprints"][name + "_profile_sha256"] = _digest(profile)
                paths[name + "_profile"] = str(Path(profile).resolve())
        if not retrieval.save_intermediates or not generation.save_intermediates:
            raise PipelineProblem("invalid_options", "流水线需要保存证据与工单；请启用检索和生成的 save_intermediates。")
        if not allow_cloud and (retrieval.embedding.enabled or retrieval.reranker.enabled or
                (generation.enabled and generation.mode == "cloud")):
            raise PipelineProblem("cloud_disabled", "本次流水线默认离线；云配置需显式添加 --cloud 或选择离线配置。")
        report["fingerprints"].update(config_sha256=_digest(config), retrieval_config_sha256=fingerprint(retrieval.dict()),
            generation_config_sha256=fingerprint(generation.dict()))
        local_config = folder / "runtime-config.private.json"
        _save(local_config, local_settings)
        _save(folder / "source-paths.private.json", paths)
        stage("rag_retrieval")
        from run_operations import build_runner
        runner = await build_runner(local_config, manifest.parent, allow_cloud=allow_cloud)
        pack = await runner.run(query, incident=incident, overrides=retrieval.dict())
        report["retrieval"] = {"run_id": pack["run_id"], "pack_id": pack["pack_id"], "evidence_count": len(pack["pack"]["items"])}
        report["artifacts"]["evidence_pack"] = "retrieval/" + pack["run_id"] + ".json"
        stage("work_order")
        generator = WorkOrderGenerator(config=generation.dict(), models=runner.cloud_models, output_dir=folder / "work_orders")
        order = await generator.run(pack)
        order_folder = folder / "work_orders" / order["generation_run_id"]
        report["work_order"] = {"generation_run_id": order["generation_run_id"], "status": order["status"],
            "work_order_id": order["work_order_id"]}
        report["artifacts"]["work_order_directory"] = "work_orders/" + order["generation_run_id"]
        stage("independent_audit")
        from verify_work_order import audit
        report["audit"] = audit(order_folder)
        _save(folder / "verification.json", report["audit"])
        report["artifacts"]["verification"] = "verification.json"
        return finish("complete")
    except Exception as exc:
        if isinstance(exc, PipelineProblem):
            report["error"] = {"code": exc.code, "message": exc.message}
        else:
            report["error"] = {"code": "pipeline_failed", "message": "流水线校验或计算失败；请核对当前阶段的输入、知识清单和配置。", "error_type": type(exc).__name__}
        return finish("failed")
