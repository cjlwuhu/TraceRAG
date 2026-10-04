# 第 9 阶段第二步：可审计预处理与批量 RCA 基线

实施日期：2026-09-26，记录整理于 2026-09-27。前置为[第 9a 步](09a-data-readiness.md)。本步完成 375 个 RE1 样本的预处理与两种触发方式的独立评估；结果不等于 RAG 回答准确率。

## 1. 输入协议及本轮纠正

继续使用 9a 冻结的系统划分：OB 开发集、SS 验证集、TT 测试集，各 125 样本。样本 ID、原始文件、评分标签不改写。

本轮检查发现：SS/TT 的 `data.csv` 使用大量原始指标名，发布者同时提供了采用简化指标名的 `simple_data.csv`；OB 没有这一文件。直接把 SS/TT 原始列名与 `service_cpu/mem/latency/diskio` 标签比较，会产生无效指标评分。因此在打开测试结果之前，固定为 **有官方 simple_data.csv 时用该文件，否则用 data.csv**，每个实际输入的路径、SHA-256 和选择方式都写入 v2 冻结协议。

早期 v1 开发/验证结果保留供审计，但不作为最终对比结果。v2 完整重跑开发、验证后才运行测试；未按测试结果重新调参。该跨系统划分检验系统迁移，不是同系统随机划分。

权威协议：`D:\Project\RCA\outputs\research_benchmark\stage09-frozen-v2.json`。

SHA-256：`959f95203078bc027cef8f234ecf3b126f33fe9bd68b9ad4f1a4cb77ffb91ee8`。

## 2. 预处理规则

新增 RCA `src/telemetry_preprocessing.py`，策略名 `causal_ffill5_zero_v1`：

1. 拒绝非数值指标、标签列、重复列、剩余时间戳重复/逆序等不满足契约的输入。
2. 删除 NaN、Inf、负数或非整秒时间行，保存原始零基行号；不排序、不重建时间。
3. 仅根据前 300 个有效时间行决定指标可用性。预热期完全未观测的列整列排除，不能用后续出现的值决定是否保留。
4. 指标 Inf 视为缺失，仅前向填充最多 5 行；时间间隔超过 5 秒时重置前向填充。
5. 剩余缺失显式填零，逐指标记录数量。**缺失不等于真实零值，该回退可能制造变化**，这是待优化的基线，不是通用最优清洗方法。
6. 原始数据保持不变；每样本保存清洗报告、输入及处理后指纹。页面默认仍是严格校验，使用此策略需要在 CSV 导入区显式选择。

测试覆盖未来数据变动不改变已完成预热之后的历史前缀、跨时间缺口、非法时间、无预热观测列和标签拒绝。

## 3. 固定模型与评分口径

- 检测器：既有 MAD 基线，预热 300 点、阈值 6、连续 3 点；算法为 BARO，保留前 20 个指标候选。
- `detector`：模型先在不接触注入标签的情况下寻找第一个告警，再执行 RCA。评分端随后读取标签。
- `annotation`：使用公开基准注入时间作为触发，明确标记 `benchmark_annotation`；用于隔离 RCA 排名能力，不称作异常检测成功。
- 证据窗口、清洗规则、配置、输入、实现及 vendor 文件哈希在测试前冻结；批量运行拒绝哈希变化。
- 服务 Hit@K 在去重后的服务排名上计算；指标 Hit@K 在指标排名上计算。`latency-90` 按既有约定映射到 `latency`。MRR 基于保存的 Top-20 指标候选产生的排名。
- 所有 125 个样本保留在服务评分分母，错误和窗口不完整不能悄悄删掉。
- 端到端服务 Hit@1 要求：首个告警落在注入后 0–120 秒、分析完整、服务首位正确。窗口包含告警后观测，需要相应采集等待。
- 每系统 25 个 LOSS 样本缺少直接匹配的 loss 指标，因此全体指标准确率为 `null`；另外报告 100/125 可观测样本上的条件指标值，不把缺少目标当普通预测错误。

## 4. 最终结果

| 集合 | 触发方式 | 服务 Hit@1 | Hit@3 | Hit@5 | MRR@20 | 端到端服务 Hit@1 | 注入前首告警 |
|---|---|---:|---:|---:|---:|---:|---:|
| OB 开发，125 | detector | 39.2% | 60.0% | 76.8% | 0.549 | 0.8% | 119 |
| OB 开发，125 | annotation | 72.8% | 92.0% | 98.4% | 0.836 | 72.8% | 不适用 |
| SS 验证，125 | detector | 85.6% | 97.6% | 100.0% | 0.918 | 0.8% | 124 |
| SS 验证，125 | annotation | 81.6% | 97.6% | 97.6% | 0.889 | 81.6% | 不适用 |
| TT 测试，125 | detector | 60.0% | 82.4% | 91.2% | 0.726 | 0.0% | 125 |
| TT 测试，125 | annotation | 59.2% | 87.2% | 92.8% | 0.732 | 59.2% | 不适用 |

SS annotation 有 2 个样本观测窗口不足，仍计入 125 分母。其他五组均产生 125 个完整结果。

在每系统 100 个目标指标可观测的样本上，annotation 指标 Hit@1 为 OB 65%、SS 50%、TT 38%；detector 为 28%、50%、42%。这些条件值不能写成全部 125 样本的指标准确率。

**当前主要失败点是首告警时刻。** 首告警后的服务排名命中可能与另一段提前异常有关，不能代替对目标故障的及时识别。上述“注入前告警”来自含故障文件，不能直接当作独立纯正常数据上的误报率。

## 5. 产物与复现

以下相对于 `D:\Project\RCA`：

| 对象 | 路径 |
|---|---|
| v2 开发结果 | `outputs/research_benchmark/development-504f3caef7f44c84b5362cc2bcce3dd4/` |
| v2 验证结果 | `outputs/research_benchmark/validation-2c7e518778c94cecbe78fad255e70dab/` |
| v2 测试结果 | `outputs/research_benchmark/test-346750da6bba4c2ab1d5741be0dab618/` |
| 表格、JSON、PNG/SVG 图与组级 bootstrap 区间 | `outputs/research_summary/summary-c96bac06b4444771ad26c56b98b410e1/` |

每次结果目录包含冻结协议副本、逐样本预处理报告、detector/annotation 信号包及 `scores.private.jsonl`、`summary.json`。标签文件和含标签的原路径只用于私有评测侧。

```powershell
cd D:\Project\RCA
$pythonTaskPath = 'D:\miniconda\envs\fastapi_env\python.exe'
# 重放已有冻结版本；不要覆盖原协议。新增协议应使用新的文件名。
& $pythonTaskPath scripts/run_research_benchmark.py `
  --preparation outputs/dataset_preparation/prep-54072ff7386c41178393cf102f1bf184 `
  --frozen-protocol outputs/research_benchmark/stage09-frozen-v2.json --split test
```

汇总脚本为 `scripts/summarize_research_benchmark.py`，对各系统 25 个“服务/故障类型”组做 5,000 次 bootstrap，随机种子 20260926。该区间描述样本内组间变化，不能证明对所有系统的泛化能力。

## 6. 验收及下一步

RCA 全量 **41 项 unittest 通过**，日志 `outputs/stage09-rca-tests.log`；包括新增清洗和评分口径检查。

本步已经完成基线测量，结果暴露检测器不足。下一阶段应在开发数据和新增正常时段中改进检测及缺失处理；TT 已经被本轮评估使用，不能在观察本轮结果后反复调参又称其为全新独立测试集。RAG 语义评估见[第 9c 步](09c-knowledge-and-review.md)。
