# 第 10 阶段第一步：单命令五段流水线与 RCA 环境检查

日期：2026-10-03。前置：[第 9 阶段总验收](09-stage-review.md)与[第 10 阶段设计](10-tracerag-design.md)。本步把既有文件契约、检索和工单生成整理成独立 CLI，不改动检测算法，也不重新声明已有 TT 测试集为未见数据。

## 1. 本步交付

`src/run_pipeline.py` 可以从显式 CSV/JSON 开始，在独立 Python 中调用 RCA，或复用已保存的信号包。完整事件经过契约校验后，生成本次事件的知识快照、EvidencePack、待审核工单以及独立审计回执：

```text
显式时序文件 → RCA CLI：检测 / 有界窗口 / RCA / 摘要
                                           ↓
已有 signal-bundle.json ────────────→ 严格契约校验
                                           ↓
       本次事件 + 适用长期知识 + 本包指标摘要
                                           ↓
                 RAG 证据包 → 工单草稿 → 独立产物审计
```

默认离线，采用已有 BM25 检索和 extractive 工单基线。不会修改 `knowledge/active.json`，不会执行运维动作，也不会创建正式历史案例。RCA 与 EasyRAG/TraceRAG 沿用独立项目和独立解释器，交界仍然是 JSON，不合并两套 `src`。

## 2. 复现命令

在项目根目录运行，路径含空格时使用 PowerShell 引号：

```powershell
$pythonTaskPath = 'D:\miniconda\envs\fastapi_env\python.exe'

# 仅检查 RCA 项目和所选算法所需依赖；不分析数据、不调用云模型。
& $pythonTaskPath src/run_pipeline.py --check `
  --rca-root D:\Project\RCA --rca-python $pythonTaskPath

# 从真实时序文件开始；每次会创建新的 pipeline-UUID 目录。
& $pythonTaskPath src/run_pipeline.py `
  --telemetry D:\Project\RCA\data\RE1\RE1-OB\checkoutservice_delay\1\data.csv `
  --rca-root D:\Project\RCA --rca-python $pythonTaskPath `
  --query '当前异常如何验证和处置'

# 复用某次运行中的信号包，不重新运行 RCA。
# 替换为上条回执的实际 pipeline_id。
& $pythonTaskPath src/run_pipeline.py `
  --bundle outputs/pipelines/pipeline-实际ID/signal-bundle.json `
  --query '当前异常如何验证和处置'
```

`--telemetry` 与 `--bundle` 互斥。`--signal-profile` 接收 RCA YAML/JSON 覆盖配置，仅用于时序分析或 `--check`；`--preprocessing` 可选 `strict`、`causal_ffill5_zero_v1`。`--rca-python` 默认当前解释器，`--rca-root` 默认项目同级的 RCA；默认发现不等于完成安装。

检索和生成使用 `--config`、`--retrieval-profile`、`--generation-profile`；默认配置为 `src/configs/easyrag.operations.windows.yaml`。`--cloud` 只表示显式允许调用，仍需对应模型配置和凭据；没有该选项时拒绝启用云检索或云工单，避免从服务器 YAML 继承付费开关。`--timeout-seconds` 默认 600 秒。

`--base-manifest` 默认 `examples/operations_knowledge/manifest.jsonl`。该默认手册是教学基线，不是正式领域效果评估。默认教学案例被排除；使用正式长期知识时，可显式传入固定版本的 manifest，流水线仍按当前 system 隔离，并排除清单中所有旧事件观测。当前事件摘要仅来自本次信号包。

## 3. 状态、目录及保护边界

每次独立创建 `outputs/pipelines/pipeline-UUID/`。`--output-dir` 可修改这个父目录；旧记录不会被覆盖。CLI stdout 仅输出一份 JSON，避免旧框架的调试输出破坏脚本读取。

| 状态 | 含义 | CLI 退出码 |
|---|---|---|
| `complete` | 完整事件已完成检索、生成与独立审计；工单本身可能是草稿、生成关闭或证据不足 | 0 |
| `no_event` | 检测器未提供可分析事件，停止进入 RAG | 0 |
| `awaiting` | 观测窗口尚未完成，或参考窗口/采样点不足，停止进入 RAG；详细原因见 `signal.analysis_status` | 0 |
| `failed` | 输入、环境、契约、检索、生成或审计失败，保存可操作错误与失败阶段 | 1 |
| 自检 `ready` / `not_ready` | 所选 RCA 模块与依赖真实导入成功 / 失败 | 0 / 1 |

CLI 参数错误退出码为 2。`complete` 表示工程步骤完成，不等于检测正确或根因确认。

主要产物：

- `pipeline.json`：阶段时间与状态、指纹、相对产物路径、证据数和独立审计结果。
- `source-paths.private.json`：原输入、配置、解释器和命令参数的绝对路径，仅供本机追溯。
- `telemetry.private.csv` 或 `.json`：本次时序输入副本，防止读取时原文件变化；原上传文件名不参与检测。
- `signals/signal-run-*/`：外部 RCA 本次输出及其私有来源注册表。
- `signal-bundle.json`、`incident.json`、`corpus/manifest.jsonl`：固定信号、当前事件及本次检索语料。
- `runtime-config.private.json`：运行所需配置；旧框架 `llm_keys` 等无关凭据字段不被复制。
- `retrieval/run-*.json`：检索路线、筛选诊断与最终证据包。
- `work_orders/gen-*/`：结构化工单、Markdown、证据包及生成审计日志。
- `verification.json`：通过已有 `verify_work_order.audit()` 重新核对身份、引用、指纹和 Markdown。

未完成事件只保存已经到达的阶段产物。运行报告不回显 CSV、子进程异常原文或本地来源路径；子进程 stdout/stderr 仅记录长度及最多前 4096 字节的哈希。路径含空格通过参数数组传递，不使用 shell 拼接。

## 4. 验证证据

先编写回归测试，首轮 8 个测试因缺少流水线实现出现 9 处预期断言失败，再实现生产代码。之后新增 CLI 纯 JSON 回执回归，先观察旧框架 print 导致的两个失败，再修复输出隔离。

本步 9 个定向测试全部通过，覆盖真实离线信号包到工单及独立审计、跨系统/其他事件与教学案例隔离、无事件与不完整窗口不检索、重复运行不覆盖、缺失 RCA、子进程非零退出/超时、含空格路径与冻结 profile 参数，以及异常输出不回显。测试中的外部子进程边界使用真实 Python 脚本夹具；夹具不作为算法效果证据。

2026-10-03 全量后端实际运行：**116 个测试，29.290 秒，OK**，包含同一阶段并行新增的控制台和凭据检查。运行命令：

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:PYTHONIOENCODING = 'utf-8'
& 'D:\miniconda\envs\fastapi_env\python.exe' -m unittest discover -s tests -v
```

本机真实 `--check` 已返回 `ready`，BARO 可导入；版本为 NumPy 2.5.1、Pandas 3.0.3、Pydantic 1.10.22、scikit-learn 1.9.0、PyYAML 6.0.1。这只是当前环境的可运行证据，不是新机器的完整依赖锁。测试保留已有 Jieba `pkg_resources` 弃用警告，未擅自升级共享环境。

真实 CSV 五段验收及新仓库交付记录由本阶段总验收汇总。检测器改进、真实独立人工案例、语义标注与有代表性的多源效果验证仍需后续工作；本次没有生成虚假确认数据。

2026-10-04 独立复核发现输出目录创建错误原先会泄露 traceback。先增加失败回归，再将创建目录纳入有界异常处理；返回脱敏的 `failed` JSON，并保持已有文件不变。流水线定向测试现为 10 项，完整后端现为 117 项。CLI 显式使用 UTF-8 stdout，在 Windows 默认 CP936、未设置 `PYTHONIOENCODING` 时仍可解析 JSON。最终结果见 [总验收](10-stage-review.md)。

## 5. 改动索引

| 文件 | 用途 |
|---|---|
| `src/run_pipeline.py` | CLI 参数、离线默认、JSON 回执和退出码 |
| `src/easyrag/orchestration/pipeline.py` | RCA 文件接口、依赖自检、阶段记录、事件隔离与本次目录编排 |
| `src/easyrag/orchestration/__init__.py` | 独立编排包入口 |
| `tests/test_pipeline.py` | 真实产物与子进程边界回归 |
