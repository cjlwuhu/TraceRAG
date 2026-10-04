# 步骤 2：三域运维知识库与索引入口

## 本步结论

RCA 与 EasyRAG 继续作为两个独立项目：RCA 产生事件与时序证据，EasyRAG 管理可检索知识。两者通过版本化 JSON/JSONL 契约连接，不共享 `src`，也不互相导入内部 Python 模块。

本步加入三类知识：

1. `runbook`：运维手册与处置规范；
2. `case`：经过人工确认的历史故障案例；
3. `metric`：限定时间窗口内的时序观测摘要。

三类数据都被转换成 `KnowledgeDocument v1`，然后逐行写入 `manifest.jsonl`。

## 原版 EasyRAG 如何保存文档

原版的权威输入是目录中的 UTF-8 `.txt` 文件。启动时的流程为：

```text
.txt 文件
  -> LlamaIndex Document
  -> SentenceSplitter / HierarchicalNodeParser
  -> TextNode
  -> BM25（内存）
  -> Embedding + Qdrant（仅 Dense 模式）
```

- `.txt`：保存原始正文；目录名与相对路径充当主要元数据。
- `Document/TextNode`：运行时对象，包含正文块和 `file_path`、`dir`、`know_path` 等元数据。
- BM25：进程启动时根据节点重新构建，默认不单独持久化索引。
- Qdrant：Dense 模式下持久化向量、节点文本及元数据；collection 名由配置决定。
- `pathmap.json`：若存在，用于把文件路径映射为知识路径；它不是正文数据库。
- `imgmap_filtered.json`：若存在，用于给节点补充图片描述；它不是向量索引本身。

## 新格式和原版的区别

| 方面 | 原版 | 本项目新增格式 |
|---|---|---|
| 权威语料 | `.txt` 文件 | `manifest.jsonl`，同时兼容原 `.txt` |
| 数据类型 | 主要靠目录推断 | 显式 `runbook/case/metric` |
| 来源追踪 | `file_path/dir` | `source + raw_ref + knowledge_id` |
| 事件关联 | 无统一字段 | `source_incident_id` |
| 案例准入 | 无确认门禁 | 必须 `human_verified=true` |
| 时序数据 | 可能被当普通文本 | 只索引 `MetricSummary`，原始时序由 `raw_ref` 回看 |
| 重建索引 | 扫描 `.txt` | 从版本化 JSONL 确定性重建 |

JSONL 中每行都是一个完整 JSON 对象。例如：

```json
{
  "schema_version": "1.0",
  "knowledge_id": "metric-demo-checkout-latency-001",
  "knowledge_type": "metric",
  "title": "checkoutservice checkoutservice_latency 时序摘要",
  "content": "时序指标摘要……",
  "source": "RCA unittest telemetry fixture",
  "raw_ref": "fixture:rca/re1-ob_checkoutservice_window",
  "metadata": {
    "source_incident_id": "inc-demo-metric-window-001",
    "service": "checkoutservice",
    "metric": "checkoutservice_latency"
  }
}
```

`manifest.jsonl` 保存的是可审查、可版本管理、可重建索引的语料，不保存 Embedding 向量。切换本地 Embedding 或阿里云 API 时，需要按新模型重建向量索引，但不需要改变知识契约。第 6 阶段的新 OperationsRunner 采用 SQLite 向量缓存 + 精确余弦检索作为小规模基线，尚未接入 Qdrant；原版 Dense 路径仍使用 Qdrant。详见[本阶段的后续实现调整](06-cloud-hybrid-retrieval.md)。

## 当前 Incident、Metric 与历史案例的边界

```text
当前 Incident（运行态，不进入 CaseRetriever）
  ├─ RCA 候选
  ├─ MetricSummary（可作为当前证据检索）
  └─ 人工/实验确认
        ↓
HistoricalCase（确认态，允许进入 CaseRetriever）
```

`HistoricalCase` 必须同时具有确认根因、处置动作、证据引用、确认人和带时区的确认时间。步骤 1 生成的活动 `IncidentDocument` 无法通过该门禁。案例元数据保留 `source_incident_id`，下一步 Retriever 会据此排除“当前事件检索自己”。

`MetricSummary` 不保存完整 CSV，而保存窗口、变化方向、可解释统计量与自然语言摘要。示例中的均值和变化量来自 RCA 单元测试时序夹具，只表示观测差异，不表示因果关系或真实根因。

## 构建与运行

在 `D:\Project\EasyRAG` 执行：

```powershell
python src\build_knowledge_corpus.py `
  --runbook examples\windows_knowledge\runbook `
  --case examples\knowledge_sources\historical_case `
  --metric examples\knowledge_sources\metric_summary `
  --output examples\operations_knowledge\manifest.jsonl
```

在 `D:\Project\EasyRAG\src` 启动三域 BM25 API：

```powershell
$env:EASYRAG_CONFIG = "configs/easyrag.operations.windows.yaml"
python api.py
```

请求中的 `document` 可填 `runbook`、`case` 或 `metric`，用于限定知识域。

## 本步验收点

- 三类知识都能转换为 `KnowledgeDocument`；
- 三类知识都能由原 EasyRAG 分块和 BM25 检索；
- 正文 BM25 与 Path BM25 使用同一知识域过滤；
- 未确认案例、活动 Incident、带评测字段的 MetricSummary 均无法进入语料库；
- 原 `.txt` 摄取路径继续可用；
- 本步不需要 GPU，也没有读取或调用 Embedding API 密钥。

## 参考文献与资料等级

1. Robertson, S., Zaragoza, H. *The Probabilistic Relevance Framework: BM25 and Beyond*. Foundations and Trends in Information Retrieval, 2009. DOI: 10.1561/1500000019（信息检索领域权威综述，直接支撑 BM25 基线）. <https://doi.org/10.1561/1500000019>
2. Thakur, N. et al. *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*. NeurIPS 2021 Datasets and Benchmarks Track（高水平基准论文；其结果支持把 BM25 作为稳健基线并单独评估 Dense/Reranker）. <https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/hash/65b9eea6e1cc6bb9f0cd2a47751a186f-Abstract-round2.html>
3. Cormack, G. V., Clarke, C. L. A., Buettcher, S. *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods*. SIGIR 2009（信息检索顶级会议，下一步多路结果融合的原始 RRF 文献）. DOI: 10.1145/1571941.1572114. <https://doi.org/10.1145/1571941.1572114>
4. Es, S. et al. *RAGAs: Automated Evaluation of Retrieval Augmented Generation*. EACL 2024 System Demonstrations（同行评审系统演示论文，适合作为后续忠实度/相关性评估资料，等级低于 EACL 主会长文）. DOI: 10.18653/v1/2024.eacl-demo.16. <https://aclanthology.org/2024.eacl-demo.16/>
