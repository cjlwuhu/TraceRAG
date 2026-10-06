# TraceRAG

TraceRAG 是面向运维故障研究的本地原型，连接 **时序数据 → 异常检测 → 根因候选 → RAG 证据检索 → 待审核工单草稿**。默认使用 CPU、BM25 和离线摘录生成，不需要 GPU、模型下载或 API Key。

本项目在 [BUAADreamer/EasyRAG](https://github.com/BUAADreamer/EasyRAG) 基础上扩展事件契约、RCA 联动、证据审计和科研控制台。展示名称使用 TraceRAG，内部 Python 包继续叫 `easyrag`，兼容既有事件、配置和数据格式。当前源码专注运维控制台与研究链路，旧 GPU/比赛、Streamlit 和 OCR 分支已移除；上游复现请使用原仓库。

网站逐项操作、上传文件格式、保存目录及人工修改方法见 [UI 使用教程](assets/UI使用教程.md)。

阶段总结、申请书缺口与后续 Codex 任务见 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)，服务器维护见 [DEPLOYMENT.md](DEPLOYMENT.md)。`docs/` 研究过程记录保留在本地，GitHub 当前版本和服务器发布包均排除该目录；下文提到的阶段记录需在本地查阅。

## 当前能做什么

| 环节 | 已实现行为 | 使用边界 |
|---|---|---|
| 时序输入 | CSV 校验、显式缺失处理、信号包导入 | CSV 需要整秒 Unix `time` 列和数值指标；原始文件不改写 |
| 异常检测 | 按时间回放的中位数/MAD 检测，连续点确认 | 固定前段基线，只取首告警；不是常驻监控服务 |
| 根因定位 | 调用独立 RCA 工程的 BARO / ε-Diagnosis，保存候选与参数 | 候选排名不是已确认根因；默认还需收集报警后的观测窗口 |
| 检索 | 手册、案例、指标的 BM25；可选云 Dense / Hybrid / 重排 | 检索前校验系统、事件、时间窗口、证据截止时间及案例确认状态 |
| 工单草稿 | 离线摘录或可选云生成；JSON / Markdown 导出与字面引用校验 | 保持未确认、未执行状态；引用可定位不代表陈述正确 |
| 本地控制台 | Vue + FastAPI，CSV 导入、参数、证据查看、人工复核与实验记录 | 绑定回环地址的单用户研究原型 |
| 多源证据 | 一个真实 RE2 样例接入日志、调用链、拓扑摘要与派生指标图 | 结构化证据接入已实现；不是任意日志解析器或独立视觉诊断 |

第 9 阶段已完成 375 个 RE1 样本的冻结基线。TT 的 125 个样本中，使用注入时间触发的 RCA 服务 Hit@1 为 **59.2%**；真实检测器的首告警全部早于目标注入，要求及时报警且首位根因正确的端到端 Hit@1 为 **0%**。这说明需要改进检测和预处理，不能把链路跑通解释为可用诊断精度。评分口径见 9b 基线记录。

第 9 阶段验收时真实人工确认案例为 **0**，独立人工相关性和陈述支持标注也为 **0**，相关质量指标保持未知。本机随后保存的占位测试记录不能计作真实核验材料。`examples/` 中的教学案例仅用于软件演示，与真实长期知识库的统计分开。告警截图 OCR/视觉解析、设备拓扑图检索与影响范围契约、代表性的多源效果评测仍需补齐。参见 第 9 阶段总验收 与 申请书对照和本阶段交付。

## Windows 安装

先克隆本仓库，再进入仓库根目录。以下 PowerShell 命令均从**根目录**执行。使用 Python 3.12 和 Node.js 24；Node 版本须满足锁定的前端依赖要求。新仓库目录可以叫 `TraceRAG`，不要求放在固定磁盘。

```powershell
py -3.12 -m venv .venv
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\requirements-windows-lock.txt
npm.cmd --prefix .\web ci
npm.cmd --prefix .\web run build
```

`requirements-windows-lock.txt` 冻结已验证的 Windows / Python 3.12 依赖；`requirements-windows-core.txt` 保留当前直接依赖与兼容范围。可选 Dense 使用 SQLite 向量缓存和精确余弦检索。当前入口不依赖 Qdrant、GPU、本地模型、Paddle OCR 或 Streamlit。

### RCA 是独立工程

TraceRAG 不携带 RCA 源码、vendor、原始时序或 RCA 专用算法依赖。完整 CSV 链路需要另行准备兼容工程，其中至少包括 `scripts/run_signal_workflow.py`、`src/signal_workflow.py`、配置和该工程所需的 vendor / Python 包。它与 TraceRAG 通过版本化 `signal-bundle.json` 交换数据，不合并两套 `src`，也不互相导入内部模块。

可把两个工程放在同一父目录，或显式指定 RCA 位置和 Python。RCA 可以使用自己的虚拟环境；仅安装 TraceRAG 的 core requirements 不能保证所有 RCA 算法依赖齐全。数据来源和算法范围见 [RCAEval 上游](https://github.com/phamquiluan/RCAEval)；本项目调用的是具有上述信号入口的配套 RCA 工程，不能把任意上游 checkout 当成已安装好的配套工程。

```powershell
# 路径按你的实际 RCA 工程和环境修改。
$traceRcaRoot = (Resolve-Path ..\RCA).Path
$traceRcaPython = (Resolve-Path ..\RCA\.venv\Scripts\python.exe).Path
.\.venv\Scripts\python.exe .\src\operations_console.py `
  --rca-root $traceRcaRoot --rca-python $traceRcaPython
```

打开 <http://127.0.0.1:8765>。没有 RCA 时仍可进行文档检索和导入已有信号包；执行 CSV 检测/RCA 任务前要先配好外部工程。

## 干净克隆后的第一条查询

源码包含教学语料，不包含本机的 `data/`、`outputs/`、`knowledge/`、API Key 或个人设置。首次打开控制台时没有过去的事件和工单，也没有第 9 阶段登记的完整长期知识版本。没有 `knowledge/active.json` 时保留教学库兼容入口，界面会说明尚未登记长期知识。

安装完成后先跑一条无需 RCA 或云模型的文档检索：

```powershell
.\.venv\Scripts\python.exe .\src\run_operations.py `
  --corpus .\examples\operations_knowledge `
  --profile .\src\configs\experiments\doc_only.yaml `
  --query 'checkoutservice 延迟如何验证和处置'
```

这验证的是教学文档的离线检索入口。真实事件需上传自己的 CSV，或导入由配套 RCA 工程产生的信号包。无报警会保留 `no_event`；报警后的窗口未收集完整会保留 `awaiting_observation_window`，不会伪造事件或补一份诊断草稿。

## 一条命令运行完整链路

先检查外部 RCA 文件入口与所选算法依赖。`--check` 不读取测量数据、不调用云模型，也不执行实际分析；`ready` 表示依赖可导入，不代表诊断效果合格：

```powershell
.\.venv\Scripts\python.exe .\src\run_pipeline.py --check `
  --rca-root $traceRcaRoot --rca-python $traceRcaPython
```

将自己的测量数据放在示例路径，或替换 `--telemetry` 的参数，然后运行五段链路：

```powershell
.\.venv\Scripts\python.exe .\src\run_pipeline.py `
  --telemetry .\inputs\metrics.csv `
  --rca-root $traceRcaRoot --rca-python $traceRcaPython `
  --query '当前异常的候选根因是什么，请结合证据说明验证步骤'
```

已有信号包可以跳过 RCA，独立运行后续环节：

```powershell
.\.venv\Scripts\python.exe .\src\run_pipeline.py `
  --bundle .\inputs\signal-bundle.json `
  --query '当前异常如何验证和处置'
```

流水线默认使用教学手册作为基础，排除教学 case 和其他事件观测。它不会自动读取或修改 `knowledge/active.json`；使用已发布的真实库时通过 `--base-manifest` 显式指定该版本 manifest。当前事件指标只来自本次信号包。教学手册仍需核实领域适用性。

每次新建 `outputs/pipelines/pipeline-<UUID>/`，以 `pipeline.json` 保存状态、阶段、指纹和相对产物路径。成功时保留事件、语料快照、证据包、`work_orders/` 中的草稿和 `verification.json`。原始输入与 RCA 路径单独保存在 `source-paths.private.json`；`.private` 是用途标记，不是加密。

| 状态 | 含义 | 退出码 |
|---|---|---:|
| `complete` | 本次检索、生成与独立结构/引用审计完成；关闭生成则保存跳过记录 | 0 |
| `no_event` | 未检测到事件，未生成草稿 | 0 |
| `awaiting` | 参考/观测窗口或采样点不足，未生成草稿 | 0 |
| `failed` / 自检 `not_ready` | 输入、RCA、配置或下游失败 | 1 |
| CLI 参数错误 | 缺输入或参数互斥等用法错误 | 2 |

`--signal-profile` 可选 RCA YAML/JSON 覆盖，仅用于测量输入或自检；`--preprocessing` 支持 `strict`（默认）和 `causal_ffill5_zero_v1`。后者的补零可能制造变化，研究中须记录实际策略。`--retrieval-profile` / `--generation-profile` 分别覆盖检索与生成；`--output-dir` 选择输出父目录，`--timeout-seconds` 控制 RCA 子进程超时。云调用必须显式传 `--cloud` 并准备有效服务配置与凭据。完整参数可运行 `--help` 查看。

## 长期知识与原始数据

长期入口为 `knowledge/active.json`，指向不可覆盖的 `knowledge/versions/kb-.../manifest.jsonl`。每次查询固定“适用的已发布手册/案例 + 当前事件观测”快照。RCA 候选属于事件对象；回答和草稿另存，未经真实人工核验不能进入历史案例库。

第 9 阶段原机器登记了 23,339 条 AIOps 文本及 9 条按系统声明范围的 Kubernetes 手册记录。9 条来自 3 篇手册的三个系统范围副本。它们属于本地构建产物，干净克隆不会自动拥有这些数据。电信手册范围 `zte-aiops2024` 与微服务事件隔离。

需要原版 AIOps 电信手册时，按固定来源下载并构建文本库；下载约 698 MB，不下载模型。下面自动取本次构建返回的目录，避免依赖旧机器的 UUID 路径：

```powershell
.\.venv\Scripts\python.exe .\src\download_aiops_data.py
$traceBuild = (& .\.venv\Scripts\python.exe .\src\prepare_aiops_corpus.py | ConvertFrom-Json)
$traceManifest = Join-Path $traceBuild.directory 'corpus\manifest.jsonl'
.\.venv\Scripts\python.exe .\src\register_knowledge.py `
  --manifest $traceManifest --reason '登记固定版本 AIOps 运维文本'
```

登记会以明确列出的 manifest 并集发布新版本；以后扩充时须同时列出要保留的当前版本，避免遗漏既有资料。Kubernetes 手册、多源样例、RE1 研究数据与人工标注的准备方法分别见 9a、9c、9d。历史记录中的本机绝对路径仅是当时的产物位置，需要换成本次实际输出。

| 目录 | 内容 |
|---|---|
| `examples/` | 可随源码分发的教学输入和契约示例 |
| `data/external/` | 原始外部下载，按来源锁和哈希核对 |
| `knowledge/` | 本地长期知识版本、指针及正式案例审核记录 |
| `outputs/` | 事件/查询快照、证据包、草稿、复核、运行日志与向量缓存 |

原始业务日志、TraceRAG 运行日志和生成草稿是三种对象。目前真实业务日志只在已准备的多源样例中接入；`audit.jsonl` 或控制台日志不属于 LogRetriever 的业务证据。

## 可选云能力

默认离线。在控制台“设置”中可以配置 Qwen / GLM 凭据与本地代理；密钥使用当前 Windows 用户绑定的 DPAPI 密文存储。Cloud 总开关开放服务能力，具体任务仍需选择 Dense / Hybrid、云重排或云生成。保存设置、打开网页不会调用模型；连接测试和云任务可能计费。

Qwen 支持 `text-embedding-v4`、`gte-rerank-v2` 与生成模型，GLM 用于生成。密钥不写入 YAML、Git、工单或浏览器存储。命令行沿用服务端配置及环境变量；新流水线的离线默认行为不依赖控制台保存设置。已有云接口可用性记录不代表独立诊断效果验证。详见 第 6 阶段 与 8b 设置说明。

## 验证命令

以下命令也从仓库根目录执行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
npm.cmd --prefix .\web run build
```

浏览器集成验证使用隔离的教学契约 fixture，不需要旧事件或云调用。先安装 Playwright 对应的浏览器，再运行：

```powershell
npm.cmd --prefix .\web exec -- playwright install chromium
$env:TRACERAG_TEST_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm.cmd --prefix .\web test
```

真实 RCA CSV 属于另行选入的集成验证，需设置以下参数后运行 `npm.cmd --prefix .\web test`：

```powershell
$env:TRACERAG_TEST_CSV = (Resolve-Path .\inputs\metrics.csv).Path
$env:TRACERAG_TEST_RCA_ROOT = $traceRcaRoot
$env:TRACERAG_TEST_RCA_PYTHON = $traceRcaPython
```

`TRACERAG_TEST_PYTHON` 运行 TraceRAG 服务，`TRACERAG_TEST_RCA_PYTHON` 运行独立 RCA；后者未设置时沿用服务 Python，该环境必须具备 RCA 依赖。未配置测量文件时该项跳过。可设置 `PLAYWRIGHT_CHANNEL=msedge` 使用已安装的 Edge。本轮 9 项浏览器流程通过，包含真实 CSV 桥和 TraceRAG 检测参数检查；合成审核不计作研究标注。

本轮真实 CSV 经 BARO、检索和离线工单生成，取得 6 条证据、8 处引用，独立来源审计通过。详细后端、干净环境和 GitHub 交付结果见 第 10 阶段总验收。工程验收不代表根因准确率提升。

## 代码入口

```text
src/run_pipeline.py              本地五段流水线 CLI
src/operations_console.py        新控制台服务入口
src/api.py                       可选的轻量 Operations HTTP 服务
src/run_operations.py            独立检索实验
src/run_work_order.py            已有证据包生成草稿
src/verify_work_order.py          结构、来源、引用与导出一致性审计
src/easyrag/adapters/             事件、知识和信号包转换
src/easyrag/retrieval/            准入过滤、多路召回、融合和缓存
src/easyrag/generation/           草稿生成和约束校验
src/easyrag/console/              任务队列、知识版本和人工复核
schemas/                         版本化数据契约
web/                             Vue 前端
docs/steps/                      分阶段实施记录和限制
```

`src/api.py` 只保留现行证据包、工单和配置接口，默认绑定 `127.0.0.1:8000`；网页仍由 `src/operations_console.py` 提供。旧 Streamlit、比赛入口、模型适配及竞赛预处理脚本已清理。完整改造进度见 ROADMAP，本次范围与核验见 Cloud 审计和清理记录。

## 上游归属与 Citation

TraceRAG 的上游为 [EasyRAG](https://github.com/BUAADreamer/EasyRAG)，原论文为 [EasyRAG: Efficient Retrieval-Augmented Generation Framework for Automated Network Operations](https://arxiv.org/abs/2410.10315)。仓库保留上游 [MIT LICENSE](LICENSE) 与 `Copyright (c) 2024 BUAADreamer`。部分模型适配代码的 Apache-2.0 归属见 [第三方声明](THIRD_PARTY_NOTICES.md)。外部数据和算法的来源、许可及引用需分别保留。

```bibtex
@article{feng2024easyrag,
  title={EasyRAG: Efficient Retrieval-Augmented Generation Framework for Automated Network Operations},
  author={Feng, Zhangchi and Kuang, Dongdong and Wang, Zhongyuan and Nie, Zhijie and Zheng, Yaowei and Zhang, Richong},
  journal={arXiv preprint arXiv:2410.10315},
  year={2024}
}
```

感谢 [CCF AIOps 2024 Challenge](https://competition.aiops-challenge.com/home/competition/1780211530478944282) 与 RCAEval 提供研究数据和基准。TraceRAG 尚未报告独立的多模态准确率提升。
