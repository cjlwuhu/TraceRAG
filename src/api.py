"""Optional loopback Operations API; the Vue console uses operations_console.py."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import Field
import uvicorn
import yaml

from easyrag.domain.experiment import ExperimentConfig, StrictModel
from easyrag.domain.evidence_pack import EvidencePackRecord
from easyrag.domain.work_order import GenerationConfig, WorkOrderRecord
from easyrag.generation.work_order import WorkOrderGenerator
from easyrag.retrieval.cloud_models import CloudModelError, CloudBackendUnavailable
from easyrag.retrieval.query_context import IncidentInput
from run_operations import build_runner


class EvidencePackRequest(StrictModel):
    query: str = Field(..., min_length=1, max_length=10000)
    incident: Optional[IncidentInput] = None
    overrides: dict = Field(default_factory=dict)


class WorkOrderRequest(EvidencePackRequest):
    generation_overrides: dict = Field(default_factory=dict)


def create_app(config_path=None) -> FastAPI:
    config_path = Path(config_path or os.environ.get("EASYRAG_CONFIG") or
        Path(__file__).parent / "configs/easyrag.operations.windows.yaml").resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    @asynccontextmanager
    async def lifespan(app):
        app.state.operations_runner = await build_runner(config_path)
        try:
            yield
        finally:
            app.state.operations_runner = None

    app = FastAPI(title="TraceRAG Operations API", lifespan=lifespan)
    app.state.operations_runner = None

    def runner():
        value = app.state.operations_runner
        if value is None:
            raise HTTPException(status_code=503, detail="请加载 operations manifest 知识库")
        return value

    @app.get("/v1/operations/config")
    async def operations_config():
        value = runner()
        return {"defaults": value.defaults.dict(), "schema": ExperimentConfig.schema(),
            "capabilities": {"bm25": True, "path": True, "rrf": True,
                "dense": value.embedding_cache is not None,
                "hybrid": value.embedding_cache is not None,
                "reranker": value.cloud_models is not None, "work_order_generation": True,
                "cloud_work_order_generation": value.cloud_models is not None},
            "generation_defaults": GenerationConfig.parse_obj(config.get("work_order", {})).dict(),
            "generation_schema": GenerationConfig.schema(),
            "implemented": {"dense": True, "hybrid": True, "reranker": True, "work_order_generation": True},
            "cloud_status": "enabled_not_connectivity_checked" if value.cloud_models else "disabled",
            "corpus_sha256": value.corpus_sha256}

    @app.post("/v1/evidence/pack", response_model=EvidencePackRecord)
    async def evidence_pack(request: EvidencePackRequest):
        try:
            return await runner().run(request.query,
                incident=request.incident.dict(exclude_none=True) if request.incident else None,
                overrides=request.overrides)
        except CloudBackendUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except CloudModelError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/v1/work-orders", response_model=WorkOrderRecord)
    async def work_order(request: WorkOrderRequest):
        value = runner()
        try:
            generator = WorkOrderGenerator(config=config.get("work_order", {}), models=value.cloud_models,
                output_dir=Path(__file__).parent / config.get("work_order_output_dir", "../outputs/work_orders"))
            # Validate switches before retrieval can issue billable cloud requests.
            generator.defaults.with_overrides(request.generation_overrides)
            record = await value.run(request.query,
                incident=request.incident.dict(exclude_none=True) if request.incident else None,
                overrides=request.overrides)
            return await generator.run(record, overrides=request.generation_overrides)
        except CloudBackendUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except CloudModelError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
