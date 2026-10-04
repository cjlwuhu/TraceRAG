"""Generate machine-readable schemas from the same models used by FastAPI."""
import json
from pathlib import Path

from easyrag.domain.experiment import ExperimentConfig
from easyrag.domain.evidence_pack import EvidencePackRecord
from easyrag.retrieval.query_context import IncidentInput
from easyrag.domain.work_order import GenerationConfig, WorkOrderRecord, WorkOrderDraft


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[1] / "schemas"
    for name, model in [("experiment-config.v1", ExperimentConfig), ("evidence-pack.v1", EvidencePackRecord),
                        ("incident-document.v1.1", IncidentInput), ("generation-config.v1", GenerationConfig),
                        ("work-order-draft.v1", WorkOrderDraft), ("work-order-record.v1", WorkOrderRecord)]:
        schema = {"$schema": "http://json-schema.org/draft-07/schema#", **model.schema()}
        (output / (name + ".schema.json")).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(name)
