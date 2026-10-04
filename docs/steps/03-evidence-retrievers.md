# 步骤 3：三路 Retriever 与 EvidenceItem

> 后续修订：本页保留步骤 3 的历史演示结果。科研与事件诊断请改用[步骤 4 的 `/v1/evidence/pack`](04-evidence-pack-experiments.md)。旧 `/v1/evidence/search` 不具备完整的事件/时间窗口约束，已在 OpenAPI 中标记弃用。步骤 4 会正确排除这份示例库中不属于当前事件的 MetricSummary，不应把原来的三路同时命中理解为有效的跨模态证据。

## 本步目标

把 Runbook、HistoricalCase、MetricSummary 从“能被检索的数据”提升为“具有统一输出契约、可追踪、可评估的证据”。

```text
用户问题 + current_incident_id
        ├─ DocRetriever    -> EvidenceItem[type=doc]
        ├─ CaseRetriever   -> EvidenceItem[type=case]
        └─ MetricRetriever -> EvidenceItem[type=metric]
                              ↓
                       RetrievalSnapshot
```

三路当前都使用 BM25，但分别建立子索引。这样每路在自己的语料域内计算 IDF 和名次，不会让 Runbook 数量远大于 HistoricalCase 时直接压制案例结果。

## EvidenceItem v1

每个证据至少包含：

| 字段 | 用途 |
|---|---|
| `evidence_id` | 稳定的证据引用 ID，供后续工单引用 |
| `type` | `doc/case/metric` |
| `content` | 实际送入后续融合与生成模块的文本块 |
| `score` | 当前检索器的原始分数 |
| `rank` | 该检索器内部名次 |
| `retriever` | 产生证据的检索器，本步为 `bm25` |
| `source` | 人可读来源 |
| `raw_ref` | 可回看原数据的引用 |
| `metadata` | 白名单化的系统、服务、指标、窗口等信息 |

证据 ID 的计算输入是 `knowledge_id + chunk content`。它不依赖 LlamaIndex 随机 Node ID，因此同一语料和切块结果能够得到相同证据 ID。

## 为什么暂不合并三路 BM25 分数

BM25 分数取决于当前语料库的文档频率、长度分布和参数。三个独立子库产生的原始分数并不天然处于同一尺度，不能简单执行：

```text
all_evidence.sort(key=score)
```

尤其在只有 1—2 条文档的学习语料中，`rank_bm25` 的 IDF 可能使相关文档得到负分。负分不等于“不相关”，只说明这个超小子库中的词频统计不适合跨库解释。因此本步：

- 保留原始 score，便于实验审计；
- 每路保留独立 rank；
- 要求 Query 与候选至少有一个有效中英文/数字 token 重叠；
- 下一步使用 Reciprocal Rank Fusion（RRF）按名次融合，而不是直接比较原始分数。

## 当前事件自排除

`CaseRetriever` 会比较：

```text
case.metadata.source_incident_id == current_incident_id
```

相等时丢弃该案例，防止当前事件检索到自己未来回流形成的答案。这个过滤只适用于历史案例：当前事件对应的 MetricSummary 是合法实时证据，仍允许由 MetricRetriever 返回。

## 检索快照

每次三路检索可以保存为 `RetrievalSnapshot v1`：

```json
{
  "retrieval_id": "ret-...",
  "created_at_utc": "...",
  "query": "...",
  "current_incident_id": "...",
  "results": {
    "doc": [],
    "case": [],
    "metric": []
  }
}
```

运行时快照默认写到 `outputs/retrieval/`，该目录不进入 Git。经过验证的演示快照放在 `examples/retrieval_snapshots/`。快照保留每路名次、原始分数和来源，可用于后续计算 Recall@K、MRR、nDCG、证据支持率和消融实验。

## HTTP 调用

在 `D:\Project\EasyRAG\src` 启动：

```powershell
$env:EASYRAG_CONFIG = "configs/easyrag.operations.windows.yaml"
python api.py
```

请求：

```http
POST /v1/evidence/search
Content-Type: application/json

{
  "query": "checkoutservice 延迟如何验证和处置",
  "current_incident_id": "inc-1692602972-868acb166ba6",
  "save_intermediate": true
}
```

本接口只执行检索，不调用 LLM，不需要 GPU 或 Embedding API，适合独立评估 Retriever。

## 本步验收结果

- 三路 Retriever 均返回统一 EvidenceItem；
- CaseRetriever 排除当前事件，MetricRetriever 保留当前事件观测；
- 纯标点和无关查询不会触发超小语料回退误召回；
- 正文、来源、相对路径、原始引用和稳定 ID 被完整保留；
- 检索快照可以落盘，运行时输出与示例输出分离；
- FastAPI 端到端请求能够同时得到 doc/case/metric 三路结果。

## 参考文献与资料等级

1. Cormack, G. V., Clarke, C. L. A., Buettcher, S. *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods*. SIGIR 2009（信息检索顶级会议；下一步 RRF 的原始论文）. DOI: 10.1145/1571941.1572114. <https://doi.org/10.1145/1571941.1572114>
2. Thakur, N. et al. *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*. NeurIPS 2021 Datasets and Benchmarks Track（高水平检索基准；支持分开评估 sparse、dense 与 reranking）. <https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/hash/65b9eea6e1cc6bb9f0cd2a47751a186f-Abstract-round2.html>
3. Kaufman, S. et al. *Leakage in Data Mining: Formulation, Detection, and Avoidance*. ACM Transactions on Knowledge Discovery from Data, 2012（ACM TKDD 同行评审期刊；支撑当前事件、评测标签隔离）. DOI: 10.1145/2382577.2382579. <https://doi.org/10.1145/2382577.2382579>
4. Shi, Z. et al. *Generate-then-Ground in Retrieval-Augmented Generation for Multi-hop Question Answering*. ACL 2024 Main Conference Long Papers（NLP 顶级会议主会长文；说明生成结论与检索证据显式绑定的重要性）. DOI: 10.18653/v1/2024.acl-long.397. <https://aclanthology.org/2024.acl-long.397/>
5. Es, S. et al. *RAGAs: Automated Evaluation of Retrieval Augmented Generation*. EACL 2024 System Demonstrations（同行评审系统演示论文；用于后续检索相关性与生成忠实度评估）. DOI: 10.18653/v1/2024.eacl-demo.16. <https://aclanthology.org/2024.eacl-demo.16/>
