# 当前实现流程图

核对日期：2026-09-23。图对应当前 RCA 与 EasyRAG 的事件驱动主链路，采用黑白灰、矢量文字与正交连线；没有把规划功能画成已完成模块。

## 文件

| 文件 | 用途 |
| --- | --- |
| `rca-rag-workflow.svg` | 论文插图；文字转为矢量路径，跨设备无需安装中文字体 |
| `rca-rag-workflow.drawio` | diagrams.net / draw.io 可编辑源文件；节点、文字、连线均为独立对象，节点内元素已分组 |
| `rca-rag-workflow-editable.svg` | 保留文字的 SVG，便于修改术语；使用微软雅黑与 Consolas 字体 |
| `rca-rag-workflow.png` | 1640 × 2230 预览；论文优先插入 SVG |
| `build_workflow_figure.py` | 统一生成两个 SVG 与 draw.io，检查文字宽度、节点高度及 XML 格式 |
| `render_workflow_figure.cjs` | 使用项目 Playwright 与本机 Edge 生成 PNG 预览 |

该图包含 24 个模块，信息较多，建议以通栏或整页插图使用。SVG 可以无损缩放；编辑 draw.io 后可重新导出 SVG。中文字体缺失时，直接使用已转路径的主 SVG。

## 建议图注

**图 X　RCA–RAG 诊断与工单生成流程。** 原始时序数据经过异常检测、时间窗口划分、可选根因候选分析及独立指标摘要计算，通过 signal-bundle 文件转换为统一故障事件和指标知识。运维手册、已确认历史案例及指标摘要统一存储于 JSONL manifest。检索在系统、事件与时间约束下进行多路召回、融合、可选重排与证据打包，随后生成带引用的工单草稿并进行结构及引用校验。人工复核作为独立版本保存。实线表示数据流，虚线表示实验配置或可选向量服务。

## 模块与代码依据

以下 RCA 路径相对于 `D:\Project\RCA`，其余路径相对于 `D:\Project\EasyRAG`。

| 图中模块 | 实现位置与关键对象 |
| --- | --- |
| 时序校验、异常检测、窗口划分 | RCA `src/signal_workflow.py`：`validate_telemetry`、`detect_first_alarm`、`bounded_windows`；配置为 `SignalConfig` |
| 根因分析与独立摘要 | 同文件：`run_rca`、`summarize_metrics`、`build_signal_bundle`；支持 BARO 与 PyRCA `EpsilonDiagnosis`，指标摘要不依赖 RCA 候选筛选 |
| 事件与指标契约转换 | `src/easyrag/adapters/signal_bundle.py`：`SignalEnvelope`、`convert_signal_bundle`；返回事件字典及 `MetricSummary` 列表 |
| 统一故障事件 | `src/easyrag/domain/incident.py`：`IncidentDocument`，包含 `Detection`、`Observations`、`RcaRun`、`RootCauseCandidate`、`SourceRef` |
| 三域知识转换 | `src/easyrag/domain/knowledge.py`：`KnowledgeDocument`、`HistoricalCase`、`MetricSummary`；`src/easyrag/adapters/knowledge_corpus.py`：`build_knowledge_corpus`、`write_manifest`、`load_manifest` |
| 导入和事件语料快照 | `src/easyrag/console/store.py`：`Catalog.import_bundle`，将基准 manifest 与当前指标摘要合并为事件专属语料 |
| 语料读取与分块 | `src/easyrag/pipeline/ingestion.py`：`read_data`、`KnowledgeAwareParser` |
| 查询与准入过滤 | `src/easyrag/retrieval/query_context.py`：`build_query_context`；`src/easyrag/retrieval/operations.py`：`eligible`、`OperationsRunner` |
| 检索、融合与证据包 | `OperationsRunner`、`fuse_evidence`、`ExperimentConfig`、`EvidenceItem`、`EvidencePackRecord`；配置及得分随记录保存 |
| 工单生成与验证 | `WorkOrderGenerator`、`GenerationConfig`、`parse_model_draft`、`check_citations`、`WorkOrderDraft`、`WorkOrderRecord` |
| 人工复核 | `ReviewRequest`、`ClaimReview`；复核版本不覆盖原始生成记录 |
| 控制与日志 | `src/easyrag/console/`：`JobQueue`、`Catalog`、`ServiceSettings`、`Journal` |

图中的 v1 简写对应 schema 版本 1.0；主链路 `IncidentDocument` 使用版本 1.1。图中的 Doc / Case / Metric 表示检索证据域，主调度类是 `OperationsRunner`。

## 主要存储产物

| 产物 | 位置与说明 |
| --- | --- |
| RCA 交换包 | RCA `outputs/signals/signal-run-*/signal-bundle.json`；CSV 控制台任务的输出保存在 EasyRAG `outputs/console/jobs/job-*/signals/` 下 |
| 原始文件映射 | 同次信号运行的 `source-registry.private.json`，保存 SHA256 与原始本地路径的关联；不是检索正文 |
| 导入事件及语料 | `outputs/signal_imports/import-*/incident.json`、`signal-bundle.json`、`corpus/manifest.jsonl` |
| manifest | 每行一个完整 `KnowledgeDocument`，包含稳定 ID、类型、标题、正文、来源、`raw_ref` 和元数据；不只是原始文件路径列表 |
| 向量缓存 | CLI 使用 `outputs/embedding_cache.sqlite`；控制台使用 `outputs/console/embedding-*.sqlite`；向量持久缓存于 SQLite，当前主链路使用内存精确余弦检索 |
| 检索记录 | `outputs/operations/run-*.json`，包含查询上下文、配置、指纹、路线贡献、最终证据与诊断 |
| 生成记录 | `outputs/work_orders/gen-*/` 下的 `work-order.json`、`work-order.md`、`evidence-pack.json`、`audit.jsonl`；成功云生成另有 `prompt.json` |
| 复核记录 | `outputs/console/reviews/gen-*/rev-*/review.json` 与 `review.md` |
| 服务、任务与日志 | `outputs/console/service-settings.json`、`jobs/job-*/job.json`、`logs.json`；Windows 凭据由运行账户的 DPAPI 加密 |

## 实现边界

- RCA 与 EasyRAG 通过 JSON 契约交换，不需要相互导入项目内部模块。当前无标签信号主链路不依赖旧版 `RCAResult → IncidentDocument v1.0` 适配路线。
- 没有完整事件或观测窗口尚未结束时不进入 RAG。准入过滤发生在向量编码与各路 Top-K 之前。
- 图中指标摘要经过 signal-bundle 校验后才进入知识文档；不会绕过事件、时间与原始数据摘要的校验。
- 当前演示使用 RE1 时序数据，手册及历史案例仍含教学样例。手册文件夹批量转换已具备代码入口，但图不表示已完成外部 AIOps 全库接入。
- Dense / Hybrid 检索、重排和云生成可单独配置；离线流程仍可运行。当前主链路不是 Qdrant 索引。
- 引用检查验证来源、引文及范围，并不等于自动证明诊断结论。草稿中的根因仍待验证，处置建议未执行。
- 已复核草稿不会自动成为已确认根因或历史案例。原始运行日志、拓扑、图像检索及自动案例回流尚未接入。

## 再生成

在具有 `fontTools` 和 Windows 系统字体的 Python 环境执行 `build_workflow_figure.py`，然后使用 Node 执行 `render_workflow_figure.cjs`。两个脚本只写入本目录，不改动业务数据或应用代码。
