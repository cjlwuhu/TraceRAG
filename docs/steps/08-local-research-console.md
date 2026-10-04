# 第 8 阶段：本地科研控制台

> 2026-09-21 更新：[简约界面、服务设置与运行日志](08b-minimal-console-settings.md)已实现。下文保留原始验收记录；其中“网页不能输入密钥”“必须通过 --cloud 开启”等旧限制，已由补充步骤的本机加密设置替代。

实施与验证：2026-09-18。网页操作闭环已经实现，但**整个多模态项目尚未完成**。原始日志、拓扑、图像的统一证据契约和正式科研评测属于第 9 阶段。

## 1. 本阶段的结果

- 选择已有事件、导入信号包或上传时序 CSV。
- 在网页启停检测、RCA、指标摘要、各检索来源、路径、融合、重排和生成。
- 查看真实证据、路由分数和运行配置；没有用模拟统计冒充实验结果。
- 逐条修改工单陈述、评价引用支撑程度，另存人工修订版本。
- 导出 JSON / Markdown；对照 2–4 份历史工单的配置与结果。
- 后台队列执行长任务，刷新页面不重启任务；服务中断不自动重试付费调用。

不会执行运维处置、对外发送工单，也不会将当前事件自动写成已确认历史案例。

## 2. 为什么仍保留两个仓库

```text
浏览器 Vue
  └─ EasyRAG FastAPI（127.0.0.1:8765）
       ├─ CSV → RCA 子进程 → signal-bundle.json → 事件语料
       ├─ OperationsRunner → EvidencePack
       ├─ WorkOrderGenerator → 原始草稿
       └─ 人工复核 → 独立修订记录
```

CSV 调用 `D:\Project\RCA\scripts\run_signal_workflow.py`，默认使用启动服务的同一个 Python，即 `fastapi_env`。EasyRAG 不导入 RCA 的内部 `src`，避免同名包和重型算法耦合。共享环境与模块解耦并不矛盾。

新入口也不导入旧 `api.py` 的全局模型初始化，因此不会为打开网页而初始化原版 7B Embedding 或读取旧私有配置。旧 Streamlit WebUI 保持不变。

## 3. 启动方法

本机已经构建。以后重新安装或改动前端时：

```powershell
cd D:\Project\EasyRAG\web
npm.cmd ci
npm.cmd run build

cd D:\Project\EasyRAG
D:\miniconda\envs\fastapi_env\python.exe src\operations_console.py
```

打开 <http://127.0.0.1:8765>。本次使用 Node 24.16.0、Vue 3.5.43、Vite 8.3.0；完整依赖版本由 `web/package-lock.json` 固定。

默认离线。需要云 Embedding、重排或 Qwen 生成时，先停止已有服务，再运行：

```powershell
$env:EASYRAG_DASHSCOPE_KEY_FILE = 'D:\Project\qwen_api.txt'
D:\miniconda\envs\fastapi_env\python.exe src\operations_console.py --cloud
```

网页不能输入密钥、云端地址、代理或本地文件路径。页面加载不调用模型；显式运行云配置可能计费。VPN 7897 仍由服务端 `cloud_services.proxy` 管理，本阶段未改系统代理。

服务固定绑定回环地址，没有多用户登录，不要直接暴露公网。`--rca-root`、`--rca-python` 是服务端参数，可指定 RCA 目录/环境，不能由 HTTP 请求覆盖。

## 4. 第一次使用：边操作边理解

1. “诊断工作台”选择带 `rcaeval_baro_re1_window` 的已有事件。
2. 保持完整离线基线，问题填“当前异常如何验证和处置”，运行检索与工单。
3. 检查三类来源：手册、教学模拟案例、真实指标摘要。展开查看原文和路由贡献，不把分数当成因果置信度。
4. 点击“审核工单”，逐条对照证据。点击引文可定位原文。
5. 修改陈述后，该条支持标注自动恢复为“不确定”。填自己的复核人信息和意见，保存新版本。
6. 用“检索与生成不使用 RCA 候选”预设重跑，在“实验记录”比较两份输出。

第 6 步只消融**下游是否使用候选信息**，没有关闭先前的 RCA 计算。端到端 no-RCA 实验需要重新上传同一 CSV、关闭 RCA，再运行 RAG。两种实验不能混称。

CSV 必须包含名为 `time` 的整秒 Unix 时间戳列及数值指标。原算法检查顺序、重复值、非有限数等。CSV 文件上限 6 MB，JSON 请求上限 8 MB。默认前后各 5 分钟窗口；关闭检测必须填写人工时间，来源为 `operator`，不读取 benchmark 注入标签。无报警或窗口不足时保留独立结果，不伪造事件进入 RAG。

## 5. 开关映射

| 网页选项 | 后端配置 / 实际作用 |
|---|---|
| 检测 / 人工时间 | RCA `detection.enabled` / `trigger.timestamp_unix`，互斥 |
| RCA / BARO / ε-Diagnosis | RCA `rca.enabled` / `rca.methods`，决定算法是否运行 |
| 指标摘要 | RCA `summaries.enabled`，决定是否产生指标证据 |
| 手册 / 案例 / 指标 | `sources.doc/case/metric`，控制检索来源 |
| RCA 扩展查询 | `query.use_rca`，控制候选信息是否进入查询 |
| 路径检索 | `retrieval.path_enabled` |
| RRF 融合 | `fusion.enabled`，关闭时使用已有 round-robin 基线 |
| Dense / Hybrid | `retrieval.mode` 与 `embedding.enabled` 必须一致 |
| 云重排 | `reranker.enabled`，需要服务端云授权 |
| 生成草稿 | `generation.enabled`，关闭仍保留证据包 |
| 生成提供 RCA | `include_rca_candidates`，不改变之前的检索 |

“完整配置 JSON”包含全部检索和生成参数，应用时先经过后端严格校验。网页实验要求两个 `save_intermediates=true`，确保留痕。RCA 界面提供常用开关与 MAD 阈值；窗口、预热、预处理等完整高级参数仍通过原 RCA 配置/CLI 管理，尚未全部做成表单。

## 6. 代码改动与阅读顺序

| 文件 | 职责 |
|---|---|
| `src/operations_console.py` | 独立服务入口，默认离线、固定本机 |
| `src/easyrag/console/server.py` | 请求模型、后台队列、CSV 子进程桥、运行/复核/导出接口 |
| `src/easyrag/console/store.py` | ID 映射、独立导入目录、JSON 保存与 Windows 读写锁 |
| `web/src/App.vue` | 三个工作区、配置表单、轮询、证据定位和人工修订 |
| `web/src/style.css` | 工作台样式、390px 手机适配 |
| `web/src/main.js`、`web/index.html` | Vue 挂载入口 |
| `web/package*.json`、`web/vite.config.js` | 固定依赖、构建 |
| `tests/test_console.py` | 8 项接口/流程测试 |
| `tests/serve_console_fixture.py`、`web/tests/console.spec.js` | 隔离目录的真实浏览器联调 |
| `web/playwright.config.js` | Windows Edge 测试配置 |
| `.gitignore`、`README.md`、`docs/ROADMAP.md` | 忽略产物、补充入口和阶段记录 |

先读 `App.vue: submitRun()` → `server.py: /api/runs` → `pipeline()`。网页传事件 ID 和配置；后端查事件与语料，复用 `OperationsRunner` 与 `WorkOrderGenerator`。未来案例过滤、引用校验等约束没有搬到浏览器。

前端按 `frontend-design` 技能采用深色导航、纸色工作区和并排复核布局；未使用外部字体/CDN。未改 RCA 算法、旧 WebUI 或私有模型配置；未安装新的 Python 包或更换共享环境中的 NumPy/PyTorch。

## 7. 数据如何存储

```text
outputs/
  signal_imports/import-UUID/
    incident.json
    corpus/manifest.jsonl
    signal-bundle.json             # 网页新导入时保留
  console/jobs/job-UUID/
    job.json                      # 请求快照、状态、完成/失败结果
    telemetry.private.csv         # 仅 CSV 任务；不是 HTTP 下载对象
    signal-profile.json
    signals/signal-run-UUID/...
  work_orders/gen-UUID/
    work-order.json / work-order.md
    evidence-pack.json / audit.jsonl
  console/reviews/gen-UUID/rev-UUID/
    review.json / review.md
```

原稿记录“模型当时说了什么”；修订记录“某位本地用户如何修改和评价”。`review.json` 保存源记录哈希、父版本哈希、修订内容哈希及逐条支持标注。保存前再次检查原稿、提示、证据包与 Markdown 一致性。旧结构草稿仅供查看/导出，不可按新契约复核。

`reviewed_draft` 不是根因确认。复核人是本地自报身份，不是登录认证。始终保留 `root_cause_status=unconfirmed`、`actions_executed=false`、`case_promoted=false`。

当前编辑器允许修改陈述正文、支持程度和整体意见；保留原引用以及验证/风险/回退字段。删除条目、更换引文、正式确诊签署、案例回流、Word/PDF 导出尚未实现。

## 8. 验证结果与复现

2026-09-18 实测：EasyRAG **84 项**通过（原 76 + 新 8），RCA **33 项**通过，Edge / Playwright **4 项**通过，前端生产构建成功。当次 npm audit 为 0 已知漏洞，但不等同安全认证。

浏览器测试覆盖真实离线生成、审核/下载、下游 no-RCA 预设、关闭生成、真实 CSV + 关闭 RCA、手机布局、旧结构归档和工程复查提示。所有测试修订写到临时目录，`BROWSER_TEST_NOT_HUMAN` 不属于科研人工标注。

测试修复了一个 Windows 竞态：轮询读取状态与后台替换文件冲突。现在 JSON 读写共享锁；操作系统文件锁阻止同一目录的多个服务同时运行。重启只标记未完成任务为 `interrupted`，不自动重试云请求。

真实目录另做一次联调：

- 任务 `job-2dffe8a5574446d7bfcf2086e6da1ae5`。
- 离线工单 `gen-0b50fdfbfd6e42ec848e907d17810ec1`。
- 证据包 `pack-e479e3f361e28091`，与此前同输入 BM25 基线一致。
- 6 条证据、8 条带引用陈述、8 个引用；独立审计通过来源/引用/哈希检查，**不是语义或根因正确性验证**。

截图：`outputs/console-qa/desktop-workspace.png`、`mobile-workspace.png`、`review.png`，来自隔离测试。

```powershell
cd D:\Project\EasyRAG
$env:PYTHONPATH = 'D:\Project\EasyRAG\src'
D:\miniconda\envs\fastapi_env\python.exe -m unittest discover -s tests -v
D:\miniconda\envs\fastapi_env\python.exe src\verify_work_order.py --run-dir outputs\work_orders\gen-0b50fdfbfd6e42ec848e907d17810ec1

cd D:\Project\EasyRAG\web
npm.cmd run build
npm.cmd test
```

浏览器测试依赖本机 Edge、指定共享 Python、前几阶段真实工单/事件产物及 RCA 的 RE1 CSV，不是空白 clone 无数据测试。测试服务使用 8766 及临时目录，真实服务使用 8765。

## 9. 不能据此宣称什么

- 证据数量、耗时、逐字引用匹配率不等于根因准确率、语义支持率或处置有效率。
- 已用事件的检测时间早于 benchmark 注入时刻，不能称作提前发现目标故障；还需要独立正常数据和更多事件评测。
- 教学模拟案例不是真实历史故障；不同输入/缓存/模型配置的比较也不是单变量消融。
- 本阶段未再次调用付费云生成，只将第 7 阶段已有云草稿及支撑不足记录接到网页。

下一步优先补日志与拓扑的可追溯契约，再接图像/OCR及独立研究数据分割、标注和统一消融；界面完成不替代这些工作。

## 10. 高质量论文与工程来源

本阶段是系统集成，没有提出新算法。沿用两篇直接相关的正式论文，不为网页功能硬凑引用：

1. Lewis et al. **Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks**, NeurIPS 2020。[原文](https://proceedings.neurips.cc/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html)。用于解释检索/生成分工；[CCF 对应会议页](https://www.ccf.org.cn/Academic_Evaluation/AI/zgjsjxhtjgjxshy/al/2017-04-25/592028.shtml) 列为 A 类。
2. Gao et al. **Enabling Large Language Models to Generate Text with Citations**, EMNLP 2023 主会，DOI `10.18653/v1/2023.emnlp-main.398`。[ACL 原文](https://aclanthology.org/2023.emnlp-main.398/)。ALCE 区分答案正确性与引用质量，支持本项目将逐字引文匹配和语义支撑分开。本项目尚未复现完整 ALCE 自动指标。[CCF 对应会议页](https://www.ccf.org.cn/c/2017-04-25/592066.shtml) 列为 B 类。会议目录等级不是具体论文质量评分。

工程实现参照 [Vue 官方文档](https://vuejs.org/guide/quick-start.html)、[Vite 指南](https://vite.dev/guide/)、[FastAPI 静态文件文档](https://fastapi.tiangolo.com/tutorial/static-files/)，不是科研论文。访问日期：2026-09-18。

论文 BibTeX 复用 `docs/references/stage07.bib` 的 `lewis2020rag`、`gao2023alce`，避免重复；工程条目见 `docs/references/stage08.bib`。
