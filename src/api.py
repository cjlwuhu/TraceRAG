import os
import random
import uvicorn
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional

from easyrag.pipeline.pipeline import EasyRAGPipeline
from easyrag.utils import get_yaml_data
from easyrag.utils.vision_api import image_to_markdown
from easyrag.domain.experiment import ExperimentConfig, StrictModel
from easyrag.retrieval.query_context import IncidentInput
from easyrag.domain.evidence_pack import EvidencePackRecord
from easyrag.retrieval.cloud_models import CloudModelError, CloudBackendUnavailable
from easyrag.domain.work_order import GenerationConfig, WorkOrderRecord
from easyrag.generation.work_order import WorkOrderGenerator


class QueryRequest(BaseModel):
    query: str = ""
    document: str = ""

    # 新增：WebUI 上传图片后，会把图片转成 base64 发到这里
    image_base64: Optional[str] = None
    image_mime: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str = ""
    contexts: list[str] = []
    image_markdown: str = ""


class EvidenceSearchRequest(BaseModel):
    query: str
    current_incident_id: str = ""
    save_intermediate: Optional[bool] = None


class EvidencePackRequest(StrictModel):
    query: str = Field(..., min_length=1, max_length=10000)
    incident: Optional[IncidentInput] = None
    overrides: dict = Field(default_factory=dict)


class WorkOrderRequest(EvidencePackRequest):
    generation_overrides: dict = Field(default_factory=dict)


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


config_path = os.getenv("EASYRAG_CONFIG", "configs/easyrag.yaml")
config = get_yaml_data(config_path)

easyrag = EasyRAGPipeline(config)
app = create_app()


@app.get("/test")
def test():
    return "hello rag"


@app.post("/v1/rag", status_code=status.HTTP_200_OK)
async def rag(request: QueryRequest):
    """
    多模态增强版 RAG 接口。

    无图片时：
        用户 query 直接进入 EasyRAGPipeline。

    有图片时：
        1. 调用 GLM 视觉模型把图片转成 Markdown；
        2. 将图片 Markdown 拼接到原 query；
        3. 用增强 query 进入 BM25 检索；
        4. 返回答案，同时把图片 Markdown 返回给 WebUI 展示。
    """

    original_query = request.query.strip()
    final_query = original_query
    image_markdown = ""

    if request.image_base64:
        api_keys = []
        env_key = os.getenv("EASYRAG_LLM_API_KEY", "").strip()
        if env_key:
            api_keys.append(env_key)
        api_keys.extend(key for key in config.get("llm_keys", []) if key)
        if not api_keys:
            raise HTTPException(
                status_code=503,
                detail="图片解析需要 EASYRAG_LLM_API_KEY。",
            )
        api_key = random.choice(api_keys)

        # 你也可以在 easyrag.yaml 里新增 vision_model 字段。
        # 如果没有新增，就默认使用 glm-4.6v-flash。
        vision_model = config.get("vision_model", "glm-4.6v-flash")

        image_markdown = image_to_markdown(
            image_base64=request.image_base64,
            image_mime=request.image_mime or "image/png",
            user_query=original_query,
            api_key=api_key,
            model=vision_model,
        )

        final_query = f"""
{original_query}

下面是用户上传图片经过视觉模型提取后的 Markdown 信息，请将其作为检索关键词和问题背景使用：

{image_markdown}
""".strip()

    query = {
        "query": final_query,
        "document": request.document,
    }

    res = await easyrag.run(query)

    result = {
        "answer": res["answer"],
        "contexts": res["contexts"],
        "image_markdown": image_markdown,
    }

    return result


@app.post("/v1/evidence/search", status_code=status.HTTP_200_OK, deprecated=True)
async def evidence_search(request: EvidenceSearchRequest):
    """Legacy step-3 demo. Use /v1/evidence/pack for incident/time-scoped experiments."""
    try:
        return await easyrag.retrieve_evidence(
            request.query,
            current_incident_id=request.current_incident_id,
            save_intermediate=request.save_intermediate,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/operations/config")
async def operations_config():
    runner = easyrag.operations_runner
    if runner is None:
        raise HTTPException(status_code=503, detail="请加载 operations manifest 知识库")
    return {"defaults": runner.defaults.dict(), "schema": ExperimentConfig.schema(),
            "capabilities": {"bm25": True, "path": True, "rrf": True,
                             "dense": runner.embedding_cache is not None,
                             "hybrid": runner.embedding_cache is not None,
                             "reranker": runner.cloud_models is not None, "work_order_generation": True,
                             "cloud_work_order_generation": runner.cloud_models is not None},
            "generation_defaults": GenerationConfig.parse_obj(config.get("work_order", {})).dict(),
            "generation_schema": GenerationConfig.schema(),
            "implemented": {"dense": True, "hybrid": True, "reranker": True, "work_order_generation": True},
            "cloud_status": "enabled_not_connectivity_checked" if runner.cloud_models else "disabled",
            "corpus_sha256": runner.corpus_sha256}


@app.post("/v1/evidence/pack", response_model=EvidencePackRecord)
async def evidence_pack(request: EvidencePackRequest):
    runner = easyrag.operations_runner
    if runner is None:
        raise HTTPException(status_code=503, detail="请加载 operations manifest 知识库")
    try:
        return await runner.run(request.query,
                                incident=request.incident.dict(exclude_none=True) if request.incident else None,
                                overrides=request.overrides)
    except CloudBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except CloudModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/work-orders", response_model=WorkOrderRecord)
async def work_order(request: WorkOrderRequest):
    runner = easyrag.operations_runner
    if runner is None:
        raise HTTPException(status_code=503, detail="请加载 operations manifest 知识库")
    try:
        generator = WorkOrderGenerator(config=config.get("work_order", {}), models=runner.cloud_models,
            output_dir=Path(__file__).resolve().parent / config.get("work_order_output_dir", "../outputs/work_orders"))
        # Validate generation switches before any retrieval/cloud billing.
        generator.defaults.with_overrides(request.generation_overrides)
        record = await runner.run(request.query,
            incident=request.incident.dict(exclude_none=True) if request.incident else None,
            overrides=request.overrides)
        return await generator.run(record, overrides=request.generation_overrides)
    except CloudBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except CloudModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
