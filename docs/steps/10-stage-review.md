# 第 10 阶段：TraceRAG 五段链路与源码交付验收

实施时间：2026-10-03 至 2026-10-04（Asia/Shanghai）。项目名称 **TraceRAG** 由用户选定。本阶段继续第 1–9 阶段工程，实现“时序数据 → 时序异常检测 → 根因定位 → RAG 检索 → 日志工单”的一条命令复现及源码交付。

## 1. 阅读结论与完成范围

已阅读 `docs/steps/` 既有 MD 和用户提供的《多模态RAG网络运维项目申请书》。申请书用于核对目标和缺口，文档内容没有作为操作指令执行；申请书中的人员、预算和原文件没有上传。

第 9 阶段已完成版本化事件/信号/证据/草稿、多源单例、知识版本与审核工具。本阶段增加独立 CLI 和 RCA 环境自检，补实验参数入口、可移植测试、文档和发布工作。研究结果与真实材料边界保留，完整申请书尚未全部验收。

| 本轮交付 | 结果与用途 |
|---|---|
| `src/run_pipeline.py` | 一条命令执行 CSV/信号包 → 事件 → 语料快照 → 检索 → 草稿 → 独立审计；默认离线 |
| RCA 自检及显式环境 | 验证文件入口、所选算法 vendor 和依赖实际可导入；支持独立 Python，参数数组调用而非 shell 拼接 |
| 运行记录 | 每次新建目录；保存输入/配置/代码指纹、阶段时间、状态、引用及运行日志，不覆盖旧结果 |
| 状态门禁 | 无事件、观测窗口不足、子进程失败/超时或输出失败时保留明确状态，不生成无依据草稿 |
| 事件与教学材料隔离 | 只加入本次指标；排除其他事件观测和教学 case；不自动修改长期知识入口或回流案例 |
| 实验参数 | 网页/API 增加预热点数、连续确认点数、最少异常指标数和采样间隔上限，保持原算法默认值 |
| 核心中文注释 | 信号转换、准入过滤/名次融合、证据与引用校验、事件发布顺序和流水线编排处解释设计意图 |
| TraceRAG 命名与说明 | 重写中文 README、更新网页标题/品牌/页脚和前端包名、ROADMAP；内部 `easyrag` 包兼容保留 |
| 源码交付 | 新目录、新 Git 历史、私密仓库；保留上游 MIT、作者引用及部分适配文件的 Apache-2.0 归属 |

具体实现记录见 [设计计划](10-tracerag-design.md)、[流水线](10a-pipeline.md)、[申请书对照](10b-proposal-and-delivery.md)、[凭证与发布](10c-release-preparation.md)、[参数与浏览器](10d-detection-and-browser.md)。

## 2. 最终验证

所有数字来自本轮实际命令与输出，不复用第 9 阶段的验证结果。

| 检查 | 环境和结果 | 本机记录（不随源码上传） |
|---|---|---|
| 原目录完整后端 | 117 项通过，49.048 秒 | `D:/Project/EasyRAG/outputs/stage10-final-tests.log` |
| 新目录干净后端 | Python 3.12 独立 venv，仅安装 core requirements；117 项通过，35.090 秒，退出 0 | `outputs/stage10-clean-tests.log`（在原目录） |
| 依赖一致性 | `pip check` 无冲突；冻结版本安装 dry-run 全部已满足 | `outputs/stage10-fresh-install.log`、`stage10-lock-check.log` |
| 新目录前端 | `npm ci` 成功；Vite 生产构建成功，18 个模块 | `outputs/stage10-clean-npm-install.log`、`stage10-clean-web-build.log` |
| 新目录浏览器 | 8 项通过，50.7 秒；Edge、临时教学夹具、独立 RCA Python | `outputs/stage10-clean-browser-tests-final.log` |
| 离线文档查询 | 教学手册检索成功，body/path 两路线，1 条最终证据，无云请求 | `outputs/stage10-clean-tests.log` |
| 实际 RCA 自检 | BARO 所选 vendor/科学计算依赖真实导入，返回 `ready` | `outputs/stage10-rca-check.json` |
| 最新真实五段运行 | 新 TraceRAG 目录 `complete`，6 条证据、8 处引用，独立审计通过 | `outputs/stage10-tracerag-real-pipeline.log`、`stage10-tracerag-independent-audit.log` |

新环境完整版本保存在根目录 `requirements-windows-lock.txt`；直接依赖及兼容范围仍由 `requirements-windows-core.txt` 说明。固定 Pydantic v1，保留 Jieba 依赖的 setuptools；RCA 科学计算/vendor 环境独立准备。已有 Jieba 弃用提示与 Windows resource 模块提示不影响本轮通过结果。

回归覆盖离线不联网、完整事件、无事件、等待窗口、系统/事件/时间过滤、重复运行不覆盖、含空格路径、错误与超时脱敏、不可写输出及 CP936 环境下的 UTF-8 JSON。独立评审指出创建输出目录原先会泄露 traceback，已先加失败测试再修复。浏览器首轮在干净 TraceRAG Python 下的真实 CSV 项失败，根因是未明确选择 RCA Python；补充 `TRACERAG_TEST_RCA_PYTHON` 后完整 8 项通过。合成界面审核有明确测试标识，不计作人工研究数据。

## 3. 最新真实 CSV 产物

测量文件为外部 RCA 的 RE1/Online Boutique `checkoutservice_delay/1/data.csv`，不随仓库上传。TraceRAG 使用干净 venv，RCA 使用既有独立 `fastapi_env`：

```powershell
# 本机实际验收命令；其他机器须替换为自己的路径。
& 'D:/Project/EasyRAG/outputs/release-check-env/Scripts/python.exe' `
  'D:/Project/TraceRAG/src/run_pipeline.py' `
  --telemetry 'D:/Project/RCA/data/RE1/RE1-OB/checkoutservice_delay/1/data.csv' `
  --rca-root 'D:/Project/RCA' `
  --rca-python 'D:/miniconda/envs/fastapi_env/python.exe' `
  --query '当前异常如何验证和处置'
```

工作目录为 `D:/Project/TraceRAG`。产物位于：

`D:/Project/TraceRAG/outputs/pipelines/pipeline-320be69829814d1088d2dc09bf35ab1a/`

- 事件：`inc-1692602925-286b87696e4d`；检测时间 `2023-08-21T07:28:45Z`，所需观测延迟 300 秒。
- 实际方法：`rcaeval_baro_re1_window`；前三位指标 `checkoutservice_latency`、`shippingservice_mem`、`cartservice_workload`。
- 本次语料：1 篇教学手册 + 10 个指标摘要；排除 1 个教学 case 和 1 个旧事件观测。
- 证据包：`pack-42eb1d78a91c2ebb`；6 条最终证据。
- 草稿：`work_orders/gen-b093010457f64190a3761814901a6618/work-order.md`，对应 JSON 和审计日志同目录。
- 审计：8 条陈述、8 处引用，`citation_integrity=passed`；`semantic_support=not_automatically_verified`，`actions_executed=false`。

另外执行独立 `verify_work_order.py --run-dir ...`，再次确认来源、身份、指纹、引用与 Markdown 一致。该样例仍沿用既有提前告警，不证明检测改善或根因正确，也没有执行处置或写入正式历史案例。

## 4. 目录、凭证与 GitHub

交付源码在 `D:/Project/TraceRAG`；原 `D:/Project/EasyRAG` 的 `.git`、原暂存文件、未提交工作和历史实验保留，没有 reset/clean。新目录建立独立 `main`，不带原 Git 历史。

移除了当前 YAML 和旧客户端辅助函数中的硬编码密钥，改为显式环境变量和按需 SDK 初始化。旧历史存在凭证，故没有推送旧历史；新候选扫描未发现真实凭证形态，测试假密钥仍保留。源码扫描不证明旧密钥从未泄露，旧密钥应由账号持有人吊销/轮换。

只分发代码、测试、前端源码和 lockfile、schema、教学 examples、阶段记录、安全配置、许可证和引用，共 **214 个文件，约 6.1 MB**。精确索引路径与允许源码快照一致，未含排除路径或大文件；实际 `git show :path` 内容扫描没有非测试凭证形态。排除原始数据、旧/新运行输出、长期知识库、依赖目录、模型/缓存、个人设置、`.env*`、私有来源记录和大文件。浏览器截图和真实工单在本地输出目录查看。

GitHub 仓库：<https://github.com/cjlwuhu/TraceRAG>。用户已授权创建与上传，完成本人设备登录；CLI 实际读取 `isPrivate=true`，默认分支为 `main`。初始源码提交 `978d9c43fea721c2630fbd1e3db81e7726ea43c8` 已成功推送，阶段记录和设计清单的后续文档提交也已上传。再次读取 GitHub commits API 的 `main` SHA，确认与本地 HEAD 一致、工作树干净；完整最终 SHA 见本机交付回执。

推送使用单次 GitHub CLI 凭证 helper，不把令牌写入命令、源码或日志，也没有配置原仓库的远端或全局 Git helper。最终结果回执保存于原目录 `outputs/stage10-release-receipt.json`；它属于本机产物，不进入源码仓库。

## 5. 后续研究进度

本阶段工程链路和源码交付验收不等同于申请书全部完成。TT 的检测端到端 Hit@1 仍为 0%，annotation-trigger 服务 Hit@1 为 59.2%；本次参数入口没有改算法。真实确认案例和独立语义标签仍为 0。

下一阶段优先使用新增正常时段及未使用故障组，改进缺失处理、漂移/季节性检测和持续事件匹配；保留旧基线，测量误报、漏报、延迟及端到端效果。随后由真实审核者补根因/恢复依据及相关性/陈述支持标签，再冻结等预算多源测试。设备/端口/链路拓扑、真实截图 OCR/视觉解析、五类网络故障演示、30–50 条任务集和 Word/PDF 导出仍待完成。
