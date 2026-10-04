"""Server/CLI-only settings; never accepted as HTTP experiment overrides."""

from pathlib import Path
from typing import Optional

from pydantic import StrictBool, confloat

from easyrag.domain.experiment import StrictModel
from easyrag.retrieval.cloud_models import DashScopeModels
from easyrag.retrieval.vector_cache import CachedEmbeddings


class CloudServices(StrictModel):
    enabled: StrictBool = False
    api_host: str = "https://dashscope.aliyuncs.com"
    key_file: Optional[str] = None
    proxy: Optional[str] = None
    timeout_seconds: confloat(gt=0, le=120) = 60
    cache_path: str = "../outputs/embedding_cache.sqlite"


def build_cloud_runtime(settings=None, *, allow_cloud=None, base_dir=None):
    settings = CloudServices.parse_obj(settings or {})
    if not (settings.enabled if allow_cloud is None else allow_cloud):
        return None, None
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parents[2]
    key_file = (root / settings.key_file).resolve() if settings.key_file else None
    models = DashScopeModels(api_host=settings.api_host, key_file=key_file,
                            proxy=settings.proxy, timeout_seconds=settings.timeout_seconds)
    return models, CachedEmbeddings(models, (root / settings.cache_path).resolve())
