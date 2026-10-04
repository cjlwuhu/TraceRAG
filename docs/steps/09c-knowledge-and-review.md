# 第 9 阶段第三步：全局知识版本、案例准入与人工标注

实施日期：2026-09-26 至 2026-09-27。前置为[第 9b 步](09b-preprocessing-and-baseline.md)。本步完成工程机制及真实手册登记；**尚无用户提供的真实人工核验和处置记录，正式历史案例数量为 0，语义质量指标保持未知**。

## 1. manifest 不是“之前所有查询加当前答案”

`manifest.jsonl` 是统一知识记录清单，每行一个 KnowledgeDocument，正文和来源也保存在该记录中。它不自动积累每次模型回答。RCA 候选位于事件的 `incident.json`，时序摘要进入该事件语料；生成工单是另外保存的草稿。

本轮之前，网页导入主要合并固定教学 manifest 和当前事件摘要；9a 新下载的 AIOps 语料虽然建好了，也没有自动成为网页当前知识库。用户指出的“总库从哪里来”确实是一个缺口。本轮补齐了稳定入口与版本发布。

```text
固定来源文件 → outputs/knowledge_builds/.../corpus/manifest.jsonl
              ↓ 显式登记（runbook + 真实已核验 case）
knowledge/versions/kb-<内容摘要>/manifest.jsonl
              ↑ knowledge/active.json 指向当前版本
              ↓ 按事件 system / 无事件的文档范围筛选
当前事件 metric + 已登记 log/trace/topology/image
              ↓ 查询提交时固定版本与内容
outputs/query_corpora/snapshot-<内容摘要>/manifest.jsonl
              ↓ 检索前再次执行事件、时间、案例确认门禁
EvidencePack → 工单草稿 → 人工复核
              ↓ 只有真实根因、实际处置和结果经人工确认后
新 HistoricalCase → 新知识版本（旧版本和旧查询保留）
```

| 位置 | 职责 | 是否查询的全局入口 |
|---|---|---|
| `outputs/knowledge_builds/` | 各数据源的标准化构建产物与清点报告 | 否，需要显式登记 |
| `knowledge/active.json` | 当前长期知识版本指针 | 是，网页每次“当前知识库”查询读取它 |
| `knowledge/versions/kb-.../manifest.jsonl` | 不可覆盖的长期语料版本，只含手册与已确认案例 | 指针所指的权威语料 |
| `outputs/signal_imports/import-.../corpus/manifest.jsonl` | 导入该事件时的快照 | 否；网页可显式选择它以重放旧实验 |
| `outputs/query_corpora/snapshot-.../` | 本次固定知识版本与事件观测的合成快照 | 本次查询实际使用的语料 |
| `outputs/work_orders/gen-.../` | 模型/摘录工单、来源证据包 | 不自动回写知识库 |

因此，用户举出的 `outputs/signal_imports/import-01af.../corpus/manifest.jsonl` 是某个事件的快照，不是会自动更新的总库。不是把每次结果继续追加到同一个巨大 manifest；知识发布和查询快照是不同生命周期。

## 2. 本轮登记的真实长期知识

新增 `KnowledgeRegistry` 与 `src/register_knowledge.py`，发布时拒绝将事件观测当长期知识；校验重复 ID 冲突、类型和文件哈希，原子更新 active 指针。默认导入 CLI `src/import_signal_bundle.py` 也改为读取当前注册版本；显式 `--base-manifest` 仍可用于历史复现。

实际当前版本：`kb-305444bbb768a43d7d16bf7a6ea7df0e`。

实际权威文件：`D:\Project\EasyRAG\knowledge\versions\kb-305444bbb768a43d7d16bf7a6ea7df0e\manifest.jsonl`。

manifest SHA-256：`67b5761fb7cc06a589ac089b8f39fe9254643f3fabcb7735fbe36d2b535a82a2`。

| 来源 | 登记记录 | 适用范围 |
|---|---:|---|
| 9a 原版 AIOps 2024 运维文本 | 23,339 | `zte-aiops2024` |
| Kubernetes 官方中文排查手册 3 篇，分别声明适用于三个微服务系统 | 9 | `online-boutique` / `sock-shop` / `train-ticket` |
| 正式人工案例 | 0 | 等待实际审核与处置依据 |
| 合计 | 23,348 条 runbook | 9 条是 3 篇内容的三个系统范围副本，不是 9 篇独立文章 |

微服务手册固定 Kubernetes website 提交 `fcf148c070b140cbd13ec65d5809f0686e680a9c`，包括 `debug-running-pod`、`debug-service`、`determine-reason-pod-failure`，来源、原始 SHA-256 与构建报告保留于 `data/external/kubernetes/` 和 `outputs/knowledge_builds/microservices/`。参考 [Kubernetes 官方 Pod 排障说明](https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/)。这些是通用 Kubernetes 排查参考，使用具体命令前仍需检查实际部署与版本；不能视作每个故障已有确定修复方案。

电信手册与微服务事件由 system 门禁隔离。无事件问答选择 `zte-aiops2024` 才查电信库。JSONL 是权威知识文件，BM25 索引和可选向量缓存是派生物；`outputs/knowledge_builds` 不是数据库服务，`knowledge` 也不是新建的 SQL 数据库。

## 3. 人工案例正式准入

新增网页“知识与案例”以及 `POST /api/knowledge/cases`：

- 必填真实审核人、症状、已确认根因、已执行处置、实际结果、核验依据及明确确认勾选。
- 使用本次实际审核时间，服务器拒绝与当前时间相差超过 10 分钟的提交，不能把今天的核验倒填为 2023 年。
- 审核提交保存在 `knowledge/case_reviews/review-.../submission.json`；案例 ID 冲突被拒绝，修订使用新 ID，发布新的知识版本。
- 新版本保留此前长期知识，案例正文包含处置结果和依据；旧版本、旧查询与原工单不覆盖。
- 当前事件排除自身案例；检索时排除事件发生之后才审核的案例。
- 这是本地单用户自报身份机制，没有外部身份认证；服务器能够校验字段和记录时间，不能代替独立人工核实事实。

生成工单的“已复核草稿”与“正式已确认案例”仍是两种状态。RCA 首位、故障注入标签和引用完整性均不能替代真实处置记录。当前没有把测试夹具、助手生成的内容或 benchmark 标签包装成人工案例。

## 4. 检索相关性和陈述支持度

工单证据区域新增独立相关性表：0 不相关、1 有关联、2 直接有助于验证或处置，未评项留空。`POST /api/orders/{id}/annotations` 检查证据 ID 属于该工单，并绑定证据包 SHA-256、审核人及时间，保存到 `outputs/annotations/`。

既有工单复核表记录每条陈述 `supported/unsupported/uncertain`，与相关性分开。新增 `src/evaluate_human_annotations.py` 只汇总实际提交的标注：同一运行、同一审核人取最新版本；相关性只对完整评完的检索池计算宏平均，报告标注覆盖率。检索池内 precision 不是全库 recall 或全局排名质量。

实际汇总 `outputs/stage09-human-evaluation.json`：标注包 0、完整标注包 0、陈述标注 0，相关性/支持率均为 `null`。界面自动化的假审核记录只存在临时测试目录，没有进入本机正式库。

真实多源样例的待标注材料：`outputs/research_reports/ablation-ef70c9b9d8424239926e4874c4d331c1/annotation.pending.json`。其中审核人、相关性、支持度故意未填；这是待审材料，不是已完成标签。9a 的 `historical-case.pending.json` 同样保持未确认。

## 5. 操作与复现

```powershell
cd D:\Project\EasyRAG
$pythonTaskPath = 'D:\miniconda\envs\fastapi_env\python.exe'
& $pythonTaskPath src/register_knowledge.py `
  --manifest outputs/knowledge_builds/aiops2024/corpus-dc6b2573461040ed91dbf0bb0277dee2/corpus/manifest.jsonl `
  --manifest outputs/knowledge_builds/microservices/fcf148c070b140cbd13ec65d5809f0686e680a9c/corpus/manifest.jsonl `
  --reason '发布电信及微服务范围的真实手册'
& $pythonTaskPath src/operations_console.py --rca-root D:\Project\RCA
# 独立导出人工评测结果；新输出文件名避免覆盖已有记录。
& $pythonTaskPath src/evaluate_human_annotations.py --output outputs/human-evaluation-next.json
```

登记命令发布所列 manifest 的并集，**不是隐式追加所有历史产物**。未来手动登记新手册时，应显式包含需要保留的当前版本 manifest；网页人工案例准入会自动把当前版本纳入新版本。不要用上面的初始发布命令覆盖未来新增案例的 active 指针。

网页默认选择“当前知识库 + 本事件观测”；需要重放导入时语料时选择“导入时快照”。后台提交时就固定 snapshot，后续知识发布不会改变已排队任务的语料。底层 `run_operations.py` 仍接受明确 `--corpus`，用于独立复现，不会自动推测该查哪个系统。

## 6. 验收与真实依赖

新增测试覆盖版本更新与旧快照隔离、文件篡改、系统边界、观测指纹与截止时间、案例的自证/时间门禁、无事件查询、标注证据 ID；全量 EasyRAG 98 项通过。浏览器 7 项通过，覆盖真实 CSV → RCA 桥、文档问答、版本展示、相关性界面和移动布局。

**工程验收完成；真实人工数据验收尚未完成。** 还需要实际审核者提供处置记录、完成独立相关性及陈述标注，再用本步工具评分。不能为了把阶段状态改成“全部完成”而生成虚假人工确认。多源接入及独立测试材料见[第 9d 步](09d-multisource-and-test.md)。
