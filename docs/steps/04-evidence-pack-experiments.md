# 步骤 4：可配置的证据检索、融合与消融记录

验收日期：2026-09-10。环境：Windows / `D:\miniconda\envs\fastapi_env\python.exe`，Python 3.12.13、Pydantic 1.10.22。本步没有安装新依赖、读取 API 密钥或调用外部模型。

## 1. 本步解决什么问题

步骤 3 解决了“每路检索返回同一种证据结构”。本步解决“针对这个事件，哪些证据有资格参与检索，以及怎样组合成可审计的上下文”。

```text
IncidentDocument + 用户问题 + 本次配置覆盖
  → QueryContext（选择 RCA 候选，丢弃预拼接文本）
  → 事件/系统/时间/确认状态过滤
  → doc / case / metric 各自的 body + 可选 path BM25
  → 带来源贡献记录的 RRF 或 round-robin 合并
  → 去重 + Top-K + 字符预算 → EvidencePack
  → 独立 JSON 运行记录；后续供工单生成和网页展示
```

这是检索层，不等于完整的 RAG 生成系统。RCA 和 EasyRAG 仍独立管理源码、独立测试，通过版本化 JSON 交换数据；共享 conda 环境不要求共享 `src`。

## 2. 当前的开关及其真实含义

配置入口：`src/configs/easyrag.operations.windows.yaml` 的 `operations`，与原 EasyRAG 顶层的历史配置分开。

| 字段 | 默认值 | 实际控制的行为 |
|---|---|---|
| `sources.doc/case/metric` | 全 true | 是否运行对应知识域的检索 |
| `query.use_incident` | true | 是否把系统、选中的 RCA 信息加入查询文本 |
| `query.use_rca` | true | 是否加入 RCA 候选；**不是 RCA 算法执行开关** |
| `query.rca_top_k` | 3 | 每种 RCA 方法取前 K 个候选，再对查询词去重 |
| `retrieval.mode` | bm25 | 本步只实现 BM25；传 dense 会报错，不会悄悄退回 BM25 |
| `retrieval.path_enabled` | true | 是否额外检索 `know_path` 字段 |
| `retrieval.per_route_top_k` | 5 | 每个知识域、每个字段最多取几条 |
| `fusion.enabled` | true | true 用加权 RRF；false 用确定性轮询合并 |
| `fusion.rrf_k` | 60 | 平滑名次差异的常数 |
| `fusion.weights.doc/case/metric` | 1.0 | 各知识域的贡献权重；禁用一路请用 sources 开关 |
| `pack.top_k` | 6 | 证据包最多保留几条去重后的证据 |
| `pack.max_context_chars` | 12000 | 正文、引用头和分隔符共同计数的字符预算 |
| `save_intermediates` | true（本机 YAML） | 保存每次查询与证据包 |

关闭 `use_incident` 时，即使 `use_rca` 仍为 true 也不做 RCA 扩展。两者均只作用于查询文本：事件 ID、系统与时间仍用于准入过滤，不能为了消融而关闭数据完整性保护。`observations` 当前源于 RCA，关闭 RCA 扩展时也不会偷偷使用这些派生信息。

`ExperimentConfig` 对未知键、字符串 `"false"`、越界参数和非有限值报错；每次请求生成独立配置，不修改服务器默认值。JSON Schema 供后续网页动态构建表单，运行时的跨字段校验仍由 Python 模型负责。

## 3. 修正了哪些设计问题

### 先限制证据范围，再检索

不能先在全库取 Top-K 再丢弃不合格结果，否则未来案例或不相关事件可能占满候选。新路径先过滤节点，在合格节点上建立各字段的 BM25 统计，然后排序、截断。

- 历史案例：要求人工确认字段；排除当前事件；有事件上下文时排除其他系统、以及确认时间晚于当前检测时刻的案例。
- 时序摘要：必须与当前事件 ID、系统一致，并与事件窗口重叠；无事件上下文时不返回 metric。
- 手册：若显式指定系统，则不能与当前系统冲突；无系统标注的通用手册仍可使用。
- 评测字段如 `ground_truth`、`fault_type` 不能进入事件/知识输入；也不能通过 JSONL 元数据覆盖 `knowledge_type` 等核心字段。

这不是完整的泄漏审计：自然语言里人为写入的答案、同一事故的别名/重复样本仍需数据集管理解决。时序窗口当前用于离线回溯（检测点前后窗口），不是“检测时刻零延迟可见”的在线设定。

### 明确 BM25 变体

步骤 3 使用的 `rank_bm25` 在极小子库中可能产生负 IDF。本步增加 `PositiveIDFBM25`，IDF 使用：

```text
log(1 + (N - df + 0.5) / (df + 0.5))
```

公式来自 [Lucene BM25Similarity 官方实现文档](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html)。TF/长度归一化仍使用 `rank_bm25`，参数 `k1=1.5, b=0.75`；因此不能写成“与 Lucene 默认实现完全一致”。原有 BM25 模式保留，新运维路径使用 `bm25_type=2`。过滤后每次重建小索引的实现优先保证原型正确性，暂不作为高吞吐服务设计。

### RRF 不是比较原始分数

```text
score(evidence) = Σ weight(domain) / (rrf_k + rank_in_route)
```

同一证据在正文与知识路径中命中时只输出一份，但保留两个 route 的名次、原始分数与 RRF 贡献。三个知识域不同，不应把跨域汇总说成“三种独立证据都确认了同一个根因”。默认权重 1 是工程起点，不是已通过实验学到的最优权重。[RRF 原始论文](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)提供按名次融合的方法和 k=60 的依据；当前加权形式是可调工程扩展。

关闭融合的对照是按 route 名称排序后轮询、去重，保持同一证据预算，不按不可比的 BM25 原始分数混排。此时 `fusion_score=null`；保留的 `rrf_contribution` 仅作诊断，不参与选序。

### 证据包与原始记录分工

`pack.items` 保留完整证据及引用；`pack.context` 是带引用 ID 的文本。超预算的证据整条跳过，记录原因，不切掉来源或截取半条证据。预算单位目前是字符，不是 token，后续接入生成模型时需要额外做模型 token 预算。

每次保存包括：最终查询、选中的 RCA 候选、实际配置、各路排名、过滤原因、融合贡献、语料/配置/相关核心代码的 SHA-256、关键依赖版本、时间和耗时。

- `pack_id`：相同记录内容与版本产生稳定 ID；它包括实验配置，并不只是最终正文的哈希。
- `run_id`：每次都是独立 UUID；重复实验不覆盖。
- 代码哈希覆盖检索核心文件，不代替完整 Git 提交/环境锁定；正式实验仍需保存提交与依赖清单。
- API 只暴露实验参数，不把 `llm_keys` 等完整服务配置发送给网页。

## 4. 修改的代码清单

| 文件 | 学习重点 / 变更 |
|---|---|
| `src/easyrag/domain/experiment.py` | 配置模型、校验、逐请求覆盖 |
| `src/easyrag/retrieval/query_context.py` | 事件输入校验与无标签查询构建 |
| `src/easyrag/retrieval/operations.py` | 准入过滤、字段检索、实验留痕的主流程 |
| `src/easyrag/retrieval/evidence_pack.py` | 去重、加权 RRF、轮询、预算 |
| `src/easyrag/domain/evidence_pack.py` | HTTP/落盘结构及引用正文一致性校验 |
| `src/easyrag/domain/knowledge.py` | 补上直接导入 JSONL 时的案例准入与元数据保护 |
| `src/easyrag/custom/retrievers.py` | 增加正 IDF 变体，保留旧模式 |
| `src/easyrag/retrieval/evidence_retrievers.py` | 提取共享节点到 EvidenceItem 的转换函数 |
| `src/easyrag/pipeline/pipeline.py`、`src/api.py` | 挂接新 runner；新增配置/证据包接口；旧检索接口标记弃用 |
| `src/run_operations.py`、`src/run_ablation.py` | 单次查询、同语料多配置/多重复实验 |
| `src/export_operations_schemas.py`、`schemas/*pack*`、`schemas/experiment-config*` | 从真实模型导出 Schema |
| `src/configs/experiments/*.yaml` | 五种相对默认配置的消融方案 |
| `tests/test_operations.py`、`tests/test_operations_api.py` | 12 项流程/批量测试与 4 项真实应用接口测试 |
| `examples/operations_knowledge/manifest.jsonl`、`adapters/knowledge_corpus.py` | 保留内容修复 JSONL 排版；报错明确指出逐行要求 |

本阶段没有修改 RCA 算法代码，也未改动用户原有视觉模型逻辑和密钥配置。

## 5. 自己复现

在 PowerShell 执行（已激活 `fastapi_env`）：

```powershell
Set-Location D:\Project\EasyRAG
python src\run_operations.py --incident examples\incidents\checkoutservice-incident.v1.json --query "checkoutservice 延迟如何验证和处置"
python src\run_operations.py --incident examples\incidents\checkoutservice-incident.v1.json --query "checkoutservice 延迟如何验证和处置" --profile src\configs\experiments\no_rca.yaml
python src\run_ablation.py --incident examples\incidents\checkoutservice-incident.v1.json --query "checkoutservice 延迟如何验证和处置" --repeats 2
```

一键消融默认运行 `full/no_rca/question_only/doc_only/no_path/no_fusion` 六组。这里的 full 只代表本步已实现能力全开，不代表最终多模态系统全开。批量命令强制留存记录，输出到 `outputs/ablations/batch-*/manifest.json` 和同目录 `runs/*.json`。

启动 API（只监听本机）：

```powershell
Set-Location D:\Project\EasyRAG\src
$env:EASYRAG_CONFIG = "configs/easyrag.operations.windows.yaml"
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

`GET /v1/operations/config` 获取安全的默认值、Schema 和已实现能力；`POST /v1/evidence/pack` 接收以下结构，`incident` 必须填完整的 IncidentDocument JSON，而非文件路径：

```text
{query: 用户问题, incident: IncidentDocument, overrides: {query: {use_rca: false}}}
```

可在另一个 PowerShell 窗口直接调用：

```powershell
$incident = Get-Content -Raw -Encoding UTF8 D:\Project\EasyRAG\examples\incidents\checkoutservice-incident.v1.json | ConvertFrom-Json
$body = @{query="checkoutservice 延迟如何验证和处置"; incident=$incident; overrides=@{query=@{use_rca=$false}}} | ConvertTo-Json -Depth 30
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/evidence/pack -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

本阶段仅新增供未来控制台使用的接口，没有实现新的网页控制台。旧 `/v1/rag` 与旧 WebUI 仍属于原流程；新实验开关不会自动控制它们。

## 6. 本轮真实验收与边界

- EasyRAG：30 项 unittest 通过，包含真实 `api.py`、知识摄取和检索的 FastAPI TestClient 请求；不是用假的 runner 替代正常路径。
- RCA：18 项 unittest 通过。
- 六组配置各重复两次，共 12 份完整 JSON；同配置两次 `pack_id` 一致，`run_id` 不同。
- 本轮批次：`outputs/ablations/batch-0a43cb6514f345a7a79b8f15e3bbd84c/manifest.json`。
- full：1 条 doc + 1 条 case、790 字符；doc_only：1 条 doc、397 字符。no_path/no_fusion 改变排序；no_rca/question_only 改变查询但此小库返回的证据集合相同。
- metric 为 0：已有摘要的事件 ID 和窗口不属于当前事件。测试另设合法同事件摘要验证能召回；下一步再接入当前真实事件的摘要，不能修改夹具 ID 来假装接通。
- 手册是教学编写、案例是 synthetic、时序是单元测试夹具；本批次不是科研效果评测。用户问题中已指定服务名，后续自动化基准须另定义不依赖真实标签的问题构造规则。
- 本例检测时间来自 benchmark annotation，尚无在线异常检测器。
- Windows 仍有 jieba/setuptools 弃用提示与可选 `resource` 模块提示，本轮无测试失败；不代表共享环境内所有可选组件已完成兼容性验证。

没有相关性标注时不计算/虚构 Recall、nDCG 或“准确率提升”；重复执行也不等于有独立统计样本。正式科研还需要按事故/时间分割、真实或审定语料、相关性标注、固定开发集调参及独立测试集。

## 7. 参考文献与资料层级

BibTeX 见 [`../references/stage04.bib`](../references/stage04.bib)。按“方法直接相关、来源可靠”选文献，不用期刊名气代替实验论证；下面不把短文、独立赛道与主会长文混为同一级。

1. Cormack, Clarke, Büttcher. *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods*. **SIGIR 2009，2 页短文**。信息检索顶级会议的经典方法来源，用于融合公式；不是本项目的效果保证。DOI: 10.1145/1571941.1572114。[作者论文](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)
2. Robertson, Zaragoza. *The Probabilistic Relevance Framework: BM25 and Beyond*. **Foundations and Trends in Information Retrieval, 3(4), 333–389, 2009**。领域权威综述，适合支撑 BM25 理论及变体说明。DOI: 10.1561/1500000019。[作者论文](https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf)
3. Thakur et al. *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*. **NeurIPS 2021 Datasets and Benchmarks Track**。高水平检索基准论文，正式引用应保留独立赛道名称；用于后续 sparse/dense/reranker 的分项评测设计。[官方论文页](https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/hash/65b9eea6e1cc6bb9f0cd2a47751a186f-Abstract-round2.html)
4. Apache Lucene **9.12.1 BM25Similarity 官方文档**。工程实现资料，不是同行评审论文；仅用于追溯正 IDF 公式。[官方文档](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html)

建议阅读顺序：先看 `query_context.py` 的字段选择，再看 `eligible()`，然后用两路小排名手算 RRF，最后比较一份 full 与 no_rca 的 JSON。下一阶段目标是真实时序摘要与受控的异常检测入口，补齐当前 metric 空缺及检测时间来源的边界。
