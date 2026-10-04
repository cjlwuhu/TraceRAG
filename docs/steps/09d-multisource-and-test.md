# 第 9 阶段第四步：冻结测试、多源证据与统一实验材料

实施日期：2026-09-26 至 2026-09-27。前置为[第 9c 步](09c-knowledge-and-review.md)。本步完成冻结后的 RE1 测试、真实 RE2 单事件多源集成、配置消融、离线工单与来源审计。没有将单事件联调写成多模态准确率评测，也没有替代人工语义标注。

## 1. 独立测试结论

使用 9b 的 v2 固定协议运行 TT 125 样本，没有根据测试结果改参数。annotation-trigger 服务 Hit@1 为 **59.2%**；detector-trigger 125 个首告警全部早于目标注入，要求及时告警和正确根因的端到端服务 Hit@1 为 **0%**。完整口径、覆盖率和结果表见[9b](09b-preprocessing-and-baseline.md)。不能把“流程能跑”或“RCA 排名偶然命中”写成检测诊断已达到可用精度。

## 2. 真实多源输入及保留边界

来源：[RCAEval 官方代码仓库](https://github.com/phamquiluan/RCAEval)、[作者发布的 Hugging Face 数据集](https://huggingface.co/datasets/phamquiluan/RCAEval)。RE1 只有指标；本轮从 RE2 取一个 Online Boutique 样本用于接入联调。

固定数据版本：`afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`。下载锁为 `src/configs/data_sources/rcaeval_re2.json`；原文件在 `data/external/rcaeval-re2/`。含故障名称的下载路径只留在获取锁和 `source.private.json`，不会进入事件查询或检索正文；没有下载注入时间或给模型提供标签。

| 原文件 | 大小（字节） | SHA-256 |
|---|---:|---|
| logs.parquet | 336,876 | `e1a25e50c8b0beae4b2886df54d86d2f28877d0b611fed98411e9c0b0c77dad3` |
| metrics.parquet | 157,212 | `f46b3d354b234e37c424f54a005329b61aac83d15dcdd46b5642e55f390ab25a` |
| traces.parquet | 10,146,857 | `56ca8f6dbeb76fab2d33faeca54bce8f40e9c5491ebd76404ebb2dfd83409367` |

PyArrow 19.0.1 以 `--no-deps --target outputs/optional_runtime` 安装，仅供该 Parquet 转换脚本使用；未修改共享环境中已有包版本。

`src/prepare_multisource_demo.py` 先从指标产生真实 detector-trigger 信号，再按该事件窗口摘要其他来源：日志按服务记录条数和前五项消息频次；调用链按服务统计完整 span 的耗时中位数、P95 和原始状态码频次；拓扑由同 trace 下父子 span 连接得到。Jaeger 的 `startTime/duration` 单位为微秒，依据 [Jaeger JSON 结构定义](https://pkg.go.dev/github.com/jaegertracing/jaeger/model/json)，仅纳入截止时间前已经完成的 span。状态码没有未经核对就解释成 HTTP 错误率。

图片是实际窗口内候选指标的 Matplotlib 图，图注来自相同绘图数据的统计量，明确记录派生方式。**没有调用 OCR、视觉模型或训练图像嵌入，不能宣称独立视觉诊断能力。** 原始图像可在网页证据中预览，SHA-256 对应原资产。

## 3. 统一契约与检索

新增 `log/trace/topology/image` 类型，贯通 KnowledgeDocument、EvidenceItem、EvidencePack、引用 ID、来源权重、检索开关和工单验证。新类型默认关闭，避免悄悄改变旧实验；网页可选择“多源证据”预设或逐来源开关。

新观测要求事件 ID、系统、明确窗口、源资产 SHA-256、派生方式与最多 8,000 字符的内容。上传时及检索前执行跨系统、跨事件、窗口和截止时间检查；原资产保存在 `outputs/evidence_assets/<sha>`，事件增量批次保存在 `outputs/event_evidence/<event-id>/batch-.../`。旧导入快照与旧查询不变，新观测在下一次“当前知识库”查询中纳入。

API：`POST /api/events/{id}/evidence`，请求为 `{"documents": [...], "assets": [{"sha256": "...", "base64": "..."}]}`。仅允许四种新观测，拒绝重复 ID 和错误指纹。大资产可由本地 `src/attach_event_evidence.py --event-id ... --manifest ... --asset ...` 导入；网页 JSON 上限 8 MB。当前是明确的结构化证据入口，不是任意日志文件自动解析器。

实际多源样例：

- 事件导入：`import-b1e002fb255f4f7c806d1d0190e3494b`，事件 `inc-1705354148-415975ebb25e`。
- 产物：`outputs/multisource/demo-2f2384023f4541689bc39aff32c4104a/`。
- 事件观测：10 个 metric、10 个 log、7 个 trace、1 个 topology、1 个 image。
- 有效窗口内原记录：71,432 行日志，163,511 条完成的 span。
- 查询语料：`outputs/query_corpora/snapshot-5dde5598987753ab80c6b5db756338e6/manifest.jsonl`，含该系统 3 篇手册和 29 个观测，共 32 条知识记录。

## 4. 配置消融与工单

新增 `multisource/without_log/without_trace/without_topology/without_image/metrics_only` 配置。连同 `full/no_rca/doc_only`，共 9 组，每组重复两次。全部运行在相同真实事件与固定语料上，未调用付费模型。

检索批次：`outputs/ablations/batch-4ccf0513dbf14d0186c952afa92f4b60/manifest.json`。

统一审计与待人工材料：`outputs/research_reports/ablation-ef70c9b9d8424239926e4874c4d331c1/`。

| 配置 | 第一次最终证据构成 |
|---|---|
| full | metric 5，doc 1 |
| no_rca | doc 5，metric 1 |
| doc_only | doc 6 |
| multisource | image 1，metric 5，topology 1，trace 4，log 4，doc 5 |
| without_log | image 1，metric 5，topology 1，trace 5，doc 8 |
| without_trace | image 1，metric 5，topology 1，log 4，doc 9 |
| without_topology | image 1，metric 5，trace 5，log 4，doc 5 |
| without_image | metric 5，topology 1，trace 5，log 4，doc 5 |
| metrics_only | metric 5 |

18 个 EvidencePack 全部通过类型、哈希、时间边界和重复一致性检查；每个配置两次 pack_id 相同。随后生成 18 份离线工单，全部通过来源、引用及 Markdown 一致性审计；`no_rca` 同时关闭生成侧候选输入。

预算须明确：`full/no_rca/doc_only` 沿用 6 条、12,000 字符；多源及逐模态删除配置、metrics_only 使用 20 条、40,000 字符。**只有 multisource 与各 without_* 比较隔离单一模态；不能把不同预算配置的差异全归因于模态。** 检索片段数也不是独立文档数，手册可能切出多个片段。

这些是配置生效与证据留痕实验，未标注相关性或根因改善效果。案例数为 0，不能测量历史案例带来的增益。多源 RCA 算法仍是基于指标的 BARO；日志等进入 RAG 证据融合，没有被偷偷描述为新的多源因果算法。

## 5. 复现命令

```powershell
cd D:\Project\EasyRAG
$pythonTaskPath = 'D:\miniconda\envs\fastapi_env\python.exe'
& $pythonTaskPath src/download_aiops_data.py --source-dir data/external/rcaeval-re2 `
  --source-lock src/configs/data_sources/rcaeval_re2.json
# 首次转换需要可选依赖；当前本机已经安装。
# & $pythonTaskPath -m pip install --no-deps --target outputs/optional_runtime pyarrow==19.0.1
& $pythonTaskPath src/prepare_multisource_demo.py --rca D:\Project\RCA
# 上条命令会生成新事件和路径；使用其 report.json 中实际值。
& $pythonTaskPath src/run_ablation.py `
  --incident outputs/signal_imports/import-b1e002fb255f4f7c806d1d0190e3494b/incident.json `
  --corpus outputs/query_corpora/snapshot-5dde5598987753ab80c6b5db756338e6 `
  --query '当前异常的日志、调用链耗时、服务调用拓扑和指标图如何相互验证；Pod 应如何排查' `
  --profiles full no_rca doc_only multisource without_log without_trace without_topology without_image metrics_only --repeats 2
& $pythonTaskPath src/complete_research_ablation.py `
  --manifest outputs/ablations/batch-4ccf0513dbf14d0186c952afa92f4b60/manifest.json
```

`complete_research_ablation.py` 新建报告目录和工单，不覆盖已有结果。CLI 不执行任何建议的运维动作。

## 6. 实际 UI/API 验收与研究材料

真实库在 `127.0.0.1:8765` 完成两次网页 API 查询：电信无事件问答取得 6 条证据，多源事件取得 20 条，均完成离线工单及引用审计；图片 API 返回内容与原资产 SHA-256 相同。验证回执为 `outputs/stage09-api-verification.json`，执行脚本 `src/verify_console_research.py`。不是只用教学测试库证明功能可用。

EasyRAG 全量 98 项、RCA 41 项、浏览器 7 项检查通过；浏览器自动化写入临时目录，避免污染正式人工标注。界面截图在 `outputs/console-qa/`；RCA 结果表、图和分组不确定性在 RCA 的 `outputs/research_summary/summary-c96bac06b4444771ad26c56b98b410e1/`。

论文方法/结果/局限提纲见 `docs/research/stage09-results.md`。本阶段仍缺：真实处置确认、独立人工相关性/支持度标签、有代表性的多源效果测试及检测器改进。验收状态必须保留这些限制，不能填造缺失准确率。
