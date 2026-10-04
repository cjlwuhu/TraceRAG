# 步骤 5：真实时序 → 检测回放 → RCA → RAG 证据

日期：2026-09-10。环境仍为 `fastapi_env`，没有安装新依赖、读取 API 密钥或调用云模型。

## 1. 本步结果与边界

已经从本机 RE1 的真实测量数据计算出 10 条 MetricSummary，导入新的事件专属知识库，并通过第 4 步的检索/融合得到证据包。这里的“真实”指基准系统实际采集的指标，不代表真实生产事故；原手册和历史案例仍是教学语料。

本步实现的是**按时间顺序回放的简单检测基线**，不是常驻在线监控服务，也不是完整 BARO 论文复现。没有把数据集注入时间偷偷提供给检测器。

```text
RCA 项目：显式 CSV/JSON → 前段基线 → 逐点检测 → 等待观测窗口完成
                                                    ├─ 可选 BARO / EpsilonDiagnosis
                                                    └─ 独立选择的统计摘要
                         ↓ signal-bundle.json（不含目录标签）
EasyRAG 项目：严格校验 → IncidentDocument 1.1 + MetricSummary
                         ↓ 新目录 corpus/manifest.jsonl
                    body/path 检索 → EvidencePack → 后续工单/网页
```

两个项目没有互相导入 `src`；交界处仍然是版本化 JSON。原基准加载器及其标签评测用途保留，新入口不调用 `load_re1_case()`。

## 2. 检测器如何工作

对每个指标，使用文件开头 `warmup_points` 个采样点，计算中位数 m 和 MAD：

```text
MAD = median(abs(x - m))
scale = max(1.4826 × MAD, abs(m) × relative_scale_floor, absolute_scale_floor)
score(t) = abs(x(t) - m) / scale
```

后续逐点检查：同一个指标连续 K 个采样点超阈值才满足触发条件；满足条件的指标数至少为 `minimum_metrics` 才报警。采样间隔超过 `max_gap_seconds` 会重置连续计数。报警时刻是第 K 个确认点，不能倒填成第一个超阈值点。

中位数/MAD 的稳健性背景见 [Rousseeuw & Hubert 的综述](https://arxiv.org/abs/1707.09752)；修正 Z-score 的参考定义见 [NIST 手册](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm)。本实现的尺度下限、连续点规则和阈值 6 是明确记录的工程设定，不是上述资料证明的最佳参数，也不是从当前单个测试事件调出来的结果。

此基线的局限：首段数据只是假定可用的参考段，没有标签证明其正常；没有处理季节性、概念漂移、指标间依赖或多重检验；只返回首次报警。不读取未来点拟合基线，也不使用双向插值/反向填充。输入必须是有限数值和严格递增的整秒时间戳，异常输入明确报错。

## 3. 新增的配置开关

入口：`D:\Project\RCA\configs\signal_workflow.yaml`。它控制**时序侧实际计算**，不同于 EasyRAG 的查询扩展开关。

| 配置 | 默认 | 实际作用 |
|---|---|---|
| `detection.enabled` | true | 是否运行 MAD 检测回放 |
| `detection.warmup_points` | 300 | 仅用前 300 点拟合固定参考分布 |
| `detection.threshold` | 6 | 超阈值条件 |
| `detection.consecutive_points` | 3 | 同一指标连续确认点数 |
| `detection.minimum_metrics` | 1 | 同时满足持续条件的指标数 |
| `detection.max_gap_seconds` | 5 | 超过此间隔重置持续计数 |
| `trigger.source/timestamp_unix` | operator/null | 关闭检测器后，显式输入人工或标注时刻 |
| `window.reference_minutes` | 5 | 触发点之前的参考窗口 |
| `window.observation_minutes` | 5 | 触发点之后要收集的观测窗口 |
| `rca.enabled` | true | 实际执行 RCA 的总开关 |
| `rca.methods.baro/epsilon` | true/false | 分别运行两个既有算法适配器 |
| `rca.preprocessing` | rcaeval_re1 | RE1 预处理，或显式 raw 对照 |
| `rca.top_k` | 5 | RCA 返回候选数 |
| `rca.alpha/bootstrap_time/random_seed` | .05/50/42 | EpsilonDiagnosis 的显式实验参数 |
| `summaries.enabled/top_k` | true/10 | 独立生成/选择时序摘要 |

检测器开启时禁止同时提供 `trigger.timestamp_unix`。检测器关闭且没有显式触发时刻，则返回 `no_event`，不会自动读旁边的 `inject_time.txt`。

预设位于 `configs/signal_experiments/`：`no_rca`、`no_summaries`、`epsilon_only`、`raw_rca`、`annotation_checkout_demo`。最后一个含本样本的注入时刻，**只能作为 oracle timestamp 对照**，不能视为检测器性能。

`rca.enabled=false` 后不加载/运行算法，输出 `rca_runs=[]`；它与 EasyRAG 中 `query.use_rca=false`（已有算法结果，但不用于扩展查询）是两种不同的消融。摘要按窗口统计变化独立选择，不依赖 RCA 的候选，因此关闭 RCA 不会同时移除或改变时序摘要。

## 4. 预处理、时间可用性与版本调整

### RCA 与原始统计各用各的输入

默认 `rcaeval_re1` 沿用既有流程的列处理：排除 `_latency-50`，把 `_latency-90` 规范为 `_latency`；BARO 使用 RCAEval 的 RE1 预处理，EpsilonDiagnosis 还经过本项目的可用列检查。原始数据不被原地修改，摘要保留原始列名、原始单位与数值。这样不会为了做 RCA，把摘要里的内存单位和列名也悄悄改掉。

`raw` 对照保留原列，实验方法名和配置都会不同。联调最初直接送 raw 列给 BARO，得到 `redis_mem` 排首；发现其与既有 RE1 预处理不一致后，增加了显式模式并把默认值对齐。本次默认 Top-3 为 `checkoutservice_latency / shippingservice_mem / cartservice_workload`。这种差异说明预处理必须记录，不说明某种模式在完整数据集上更优。

本机沿用的 RCAEval 源码提交为 `bc49dbd85bd14032101fb9a69a5a37e9d6d55178`（已核对），没有升级 vendor。新结果来自检测到的窗口，不应与“已知注入时刻”的原基准结果视为完全相同协议。

### 不隐瞒收集窗口的等待时间

若报警发生在 T，默认参考窗口为 `[T−300, T)`，观测窗口为 `[T, T+300)`。只有输入已覆盖到 T+300，才运行 RCA 并输出摘要；否则返回 `awaiting_observation_window`，不把半个窗口的统计冒充完整结果。

本步引入 `IncidentDocument 1.1`：

- 允许 `rca_runs=[]`，支持真实关闭 RCA；1.0 的原有非空要求保留。
- 支持 `source_refs.source_type=telemetry_bundle`。
- 要求 `detection.evidence_cutoff_utc`，明确证据可用截止时间。
- RAG 除了事件/系统/窗口筛选，还排除结束时间晚于 cutoff 的时序摘要。

单次模型计算几十毫秒，不等于端到端诊断只需几十毫秒；本配置还需要 300 秒观测时间。

### 摘要、原数据和标签分离

每个摘要包含参考/观测均值、变化量、规范化变化分数、采样数、观测 min/max/p95，参考均值接近零时不生成无穷百分比。URI 形如：

```text
telemetry:sha256:<原文件哈希>#column=<原始列名>
```

真实本地路径保存在同一次运行的 `source-registry.private.json`，不进入交给 RAG 的文件。后续网页回看数据时要由后端解析该注册表，不能让浏览器任意读取本地路径。内容哈希是来源追踪和误改检测，不是数字签名或身份认证。

在 RAG 摄取端，最长 8000 字符的 MetricSummary 保持为完整节点，避免把窗口/观测说明和统计数字拆到两个块；超长摘要拒绝导入，手册和案例仍按原方案分块。

## 5. 本轮结果：如实保留提前报警

使用 `data/RE1/RE1-OB/checkoutservice_delay/1/data.csv`：721 行，57 个指标，1 秒采样，无缺失值。

| 时刻（UTC） | 事件 |
|---|---|
| 07:28:45 | 检测基线因 frontend_cpu 触发首个持续报警 |
| 07:29:32 | 数据集注入时刻，仅在检测完成后用于对照 |
| 07:33:45 | 检测模式下完整观测窗口结束，证据才可用 |

相对目标注入时刻，报警提前 47 秒。这是**目标故障前的报警**，不能宣传为提前发现目标故障，也不能仅靠这个样本估算误报率。需要在独立正常段、开发集/测试集上评估事件命中、漏报、误报和延迟；不能针对这个已看到的案例选择阈值再报测试成绩。

本轮核验：

- RCA 33 项测试通过；EasyRAG 36 项通过。
- 新测试覆盖：有效输入的检测前缀不变性、同指标连续确认、间隔重置、零 MAD、没有事件、不完整窗口、实际关闭模型、两个实际 RCA 适配器、1.1 接口、摘要完整性与 cutoff 门禁。
- 独立重读原 CSV，10 条摘要的参考/观测均值均与导出的值匹配，原文件 SHA-256 匹配。
- 无 RCA 输出的真实事件也能完成导入和 RAG 检索，没有假造一个 `method=disabled` 的结果。
- 新库共 13 个文档：原 3 条教学输入 + 10 条真实测量摘要。原来的不相关时序夹具仍被过滤。
- 默认配置的最终包：1 doc + 1 教学 case + 4 metric，共 3236 字符。最先出现的 metric 是 `shippingservice_mem`；检索排序不是根因置信度，也不是根因确认。
- 六组检索配置各重复两次，同组 `pack_id` 一致、`run_id` 不同。question_only 下本问题没有返回 metric，说明词面检索仍有局限；下一步需要与 Dense/Hybrid 对照。

这仍然只是单事件集成验收。没有标注相关性，所以不计算 Recall/nDCG；没有真实日志，不能声称生成了日志佐证或确认了根因。

## 6. 复现命令与产物

先激活 `fastapi_env`，从任意目录都可运行时序入口（路径示例为本机）：

```powershell
python D:\Project\RCA\scripts\run_signal_workflow.py --telemetry D:\Project\RCA\data\RE1\RE1-OB\checkoutservice_delay\1\data.csv
python D:\Project\RCA\scripts\run_signal_workflow.py --telemetry D:\Project\RCA\data\RE1\RE1-OB\checkoutservice_delay\1\data.csv --profile D:\Project\RCA\configs\signal_experiments\no_rca.yaml
```

每次打印新生成的 bundle 路径，不覆盖旧记录。下面直接复用本轮已验证的默认产物：

```powershell
Set-Location D:\Project\EasyRAG
python src\import_signal_bundle.py --bundle D:\Project\RCA\outputs\signals\signal-run-f67c44cf480f47969772a530e8e9520e\signal-bundle.json
python src\run_operations.py --incident outputs\signal_imports\import-e94a1dec22f242448e29e4841f1d99bf\incident.json --corpus outputs\signal_imports\import-e94a1dec22f242448e29e4841f1d99bf\corpus --query "当前异常如何验证和处置"
python src\run_ablation.py --incident outputs\signal_imports\import-e94a1dec22f242448e29e4841f1d99bf\incident.json --corpus outputs\signal_imports\import-e94a1dec22f242448e29e4841f1d99bf\corpus --query "当前异常如何验证和处置" --repeats 2
```

新导入会打印新的目录；如果要使用刚产生的版本，请替换后续命令中的 `--incident/--corpus` 路径。`--corpus` 只供本地 CLI 使用，HTTP 请求没有任意文件读取参数。

本轮主要产物：

- 时序输出：`D:\Project\RCA\outputs\signals\signal-run-f67c44cf480f47969772a530e8e9520e\signal-bundle.json`。
- 转换后事件：`outputs/signal_imports/import-e94a1dec22f242448e29e4841f1d99bf/incident.json`。
- 12 份检索实验：`outputs/ablations/batch-dd966c075c47413cb5359da5c884eaf3/manifest.json`。
- 其中默认证据包：同目录 `runs/run-90987dedd7094911a9d493a190952714.json`。

现有 HTTP `/v1/evidence/pack` 已兼容 1.1；默认服务 YAML 仍指向教学库，不会在 CLI 导入后悄悄切换整个服务。要用新库启动服务，需要显式配置 `data_path`。完整网页操作将在后续统一接入。

## 7. 代码改动索引

| 项目 / 文件 | 本步变更 |
|---|---|
| RCA `src/signal_config.py` | 严格配置模型与逐次覆盖 |
| RCA `src/signal_workflow.py` | 无标签加载、MAD 回放、窗口门禁、实际 RCA、摘要、指纹 |
| RCA `scripts/run_signal_workflow.py` | 输出公共 bundle 与私有路径注册表 |
| RCA `scripts/run_rcaeval.py` | vendor 路径改为相对脚本定位，算法原实现未改 |
| RCA `configs/signal_workflow.yaml`、`signal_experiments/` | 默认配置与 5 个对照预设 |
| RCA `scripts/export_signal_schema.py`、`schemas/signal-config.v1.schema.json` | 为后续网页导出配置 Schema |
| RCA `tests/test_signal_workflow.py` | 15 项新增测试 |
| EasyRAG `adapters/signal_bundle.py`、`import_signal_bundle.py` | 严格转换契约、新目录导入、保护基础库 |
| EasyRAG `domain/incident.py`、`retrieval/query_context.py` | 1.1 无 RCA 与证据截止时间 |
| EasyRAG `retrieval/operations.py` | 截止时间过滤和可用性说明 |
| EasyRAG `pipeline/ingestion.py` | 时序摘要保持完整，其他文档照常分块 |
| EasyRAG `run_operations.py`、`run_ablation.py` | 显式选择事件专属语料 |
| EasyRAG `export_operations_schemas.py`、新 1.1 Schema | 导出当前接口模型 |
| EasyRAG `tests/test_signal_bundle.py`、`test_operations_api.py` | 5 项桥接测试 + 1 项 1.1 HTTP 测试 |

## 8. 参考文献与用途

BibTeX：[`../references/stage05.bib`](../references/stage05.bib)。优先引用直接相关的正式论文，不把资料等级等同于本项目的实验质量。

1. **Pham, Ha, Zhang. BARO. FSE 2024 Research Papers / Proceedings of the ACM on Software Engineering, 1(FSE), 2024.** 软件工程顶级会议研究论文，支撑“检测误差会影响 RCA，需要分阶段评测”的设计。DOI: 10.1145/3660805。[官方会议页](https://2024.esec-fse.org/details/fse-2024-research-papers/81/BARO-Robust-Root-Cause-Analysis-for-Microservices-via-Multivariate-Bayesian-Online-C)。本步 MAD 检测器不是论文中的多变量贝叶斯在线变点检测器。
2. **Rousseeuw, Hubert. Anomaly Detection by Robust Statistics. WIREs Data Mining and Knowledge Discovery, 2018, e1236.** 同行评审稳健统计综述，为中位数/MAD 提供方法背景，不负责证明我们的参数最优。DOI: 10.1002/widm.1236。[作者版本](https://arxiv.org/abs/1707.09752)。
3. **Pham et al. RCAEval. Companion Proceedings of the ACM on Web Conference 2025, 777–780.** 数据与基准来源；应写明 Companion，不能当作 WWW 主会长文。DOI: 10.1145/3701716.3715290。[作者仓库及正式引文](https://github.com/phamquiluan/RCAEval)。
4. **NIST/SEMATECH e-Handbook, Detection of Outliers.** 官方工程统计参考，不是顶会论文；用于修正 Z-score 的定义与局限。[手册](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm)。

下一步是可选 Embedding 与 BM25/Dense/Hybrid 对照，再向带证据引用的日志工单和网页控制台推进。多模态、正式数据分割/评测和完整在线服务尚未完成。
