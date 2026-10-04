"""Validated per-request experiment settings, also exposed to the web client."""

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, confloat, conint, root_validator


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        allow_mutation = False
        allow_inf_nan = False


class Sources(StrictModel):
    doc: StrictBool = True
    case: StrictBool = True
    metric: StrictBool = True
    log: StrictBool = False
    trace: StrictBool = False
    topology: StrictBool = False
    image: StrictBool = False


class QueryOptions(StrictModel):
    use_incident: StrictBool = True
    use_rca: StrictBool = True
    rca_top_k: conint(strict=True, ge=1, le=50) = 3


class RetrievalOptions(StrictModel):
    mode: Literal["bm25", "dense", "hybrid"] = "bm25"
    path_enabled: StrictBool = True
    per_route_top_k: conint(strict=True, ge=1, le=100) = 5


class EmbeddingOptions(StrictModel):
    enabled: StrictBool = False
    model: Literal["text-embedding-v4"] = "text-embedding-v4"
    dimension: conint(strict=True) = 1024

    @root_validator(skip_on_failure=True)
    def supported_dimension(cls, values):
        if values["dimension"] not in {64, 128, 256, 512, 768, 1024, 1536, 2048}:
            raise ValueError("Unsupported embedding dimension")
        return values


class RerankerOptions(StrictModel):
    enabled: StrictBool = False
    model: Literal["gte-rerank-v2"] = "gte-rerank-v2"
    candidate_top_k: conint(strict=True, ge=1, le=100) = 20


class SourceWeights(StrictModel):
    doc: confloat(gt=0, le=100) = 1.0
    case: confloat(gt=0, le=100) = 1.0
    metric: confloat(gt=0, le=100) = 1.0
    log: confloat(gt=0, le=100) = 1.0
    trace: confloat(gt=0, le=100) = 1.0
    topology: confloat(gt=0, le=100) = 1.0
    image: confloat(gt=0, le=100) = 1.0


class FusionOptions(StrictModel):
    enabled: StrictBool = True
    rrf_k: conint(strict=True, ge=1, le=1000) = 60
    weights: SourceWeights = Field(default_factory=SourceWeights)


class PackOptions(StrictModel):
    top_k: conint(strict=True, ge=1, le=100) = 6
    max_context_chars: conint(strict=True, ge=1, le=100000) = 12000


class ExperimentConfig(StrictModel):
    sources: Sources = Field(default_factory=Sources)
    query: QueryOptions = Field(default_factory=QueryOptions)
    retrieval: RetrievalOptions = Field(default_factory=RetrievalOptions)
    embedding: EmbeddingOptions = Field(default_factory=EmbeddingOptions)
    reranker: RerankerOptions = Field(default_factory=RerankerOptions)
    fusion: FusionOptions = Field(default_factory=FusionOptions)
    pack: PackOptions = Field(default_factory=PackOptions)
    save_intermediates: StrictBool = False

    @root_validator(skip_on_failure=True)
    def coherent_modules(cls, values):
        if (values["retrieval"].mode != "bm25") != values["embedding"].enabled:
            raise ValueError("embedding.enabled must be true exactly for dense/hybrid mode")
        if values["reranker"].enabled and values["reranker"].candidate_top_k < values["pack"].top_k:
            raise ValueError("reranker.candidate_top_k must cover pack.top_k")
        return values

    def with_overrides(self, overrides: dict | None) -> "ExperimentConfig":
        # Never mutate a process-wide config during a request.
        def merge(base, patch):
            result = dict(base)
            for key, value in patch.items():
                result[key] = (
                    merge(result[key], value)
                    if isinstance(value, dict) and isinstance(result.get(key), dict)
                    else value
                )
            return result

        return ExperimentConfig.parse_obj(merge(self.dict(), overrides or {}))
