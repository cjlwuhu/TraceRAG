# 步骤 1：RCA 输出到 IncidentDocument v1

## 本步目标

在做向量化和检索之前，先建立 RCA 与 RAG 之间稳定、可验证的数据契约：

```text
多个 RCAResult JSON
        ↓ 合并、截断 Top-K、标签隔离
IncidentDocument v1
        ↓（下一步）分块、Embedding、索引
RAG 检索语料
```

这样可以避免 RAG 直接依赖某一种 RCA 算法的私有 JSON 格式，也可以防止评测真值进入知识库。

## 标签隔离规则

以下 RCA 评测字段禁止进入 IncidentDocument：

- `ground_truth`
- `fault_type`（当前数据中的值来自故障注入目录，不是线上观测结果）
- `root_cause_service`、`root_cause_metric`
- `label`、`labels`

原始 `incident_id` 和文件路径也可能包含 `delay`、`loss` 等标签。因此，本适配器生成中性且稳定的事件 ID：

```text
inc-<时间戳>-<12 位 SHA-256 摘要>
```

来源追踪只保存输入内容的 SHA-256，不保存带标签的路径。`inject_time` 暂时保留用于对齐 RCA 窗口，但明确标记为 `benchmark_annotation`；上线时必须换成异常检测器产生的时间。

## 文档主要字段

| 字段 | 含义 | 是否进入检索文本 |
|---|---|---|
| `incident_id` | 中性事件主键 | 否 |
| `detection` | 时间、来源及窗口长度 | 时间进入 |
| `observations` | 从算法候选推导出的服务和指标 | 是 |
| `rca_runs` | 每种算法的 Top-K 候选、得分和耗时 | 候选及得分进入 |
| `retrieval_text` | 下一步可直接切分/向量化的规范文本 | 是 |
| `source_refs` | 输入文件内容指纹 | 否 |
| `limitations` | 当前证据边界 | 否 |

`service` 和 `signal` 只是按指标名最后一个下划线拆出的提示，原始 `metric` 始终保留为权威值。后续接入正式指标字典时，应由字典替代这一启发式解析。

## 如何复现

在 `D:\Project\EasyRAG` 执行：

```powershell
$env:PYTHONPATH = "src"
python src\build_incident.py `
  <同一事件的第一个 RCA JSON> `
  <同一事件的第二个 RCA JSON> `
  --top-k 5 `
  --output examples\incidents\example.v1.json
```

运行本步测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p "test_incident_adapter.py" -v
```

## 为什么先做契约再做 Embedding

RAG 的生成结果依赖检索到的外部证据；如果索引阶段已经混入评测真值，后续准确率会被虚高且无法说明系统具有真实诊断能力。RAG 与 DPR 的经典工作分别奠定了“检索证据增强生成”和“稠密向量检索”的基础。本步先保证输入证据的边界，下一步再比较 BM25 与 Embedding 检索。

本步没有读取 `D:\Project\qwen\_api.txt`，也没有调用 Embedding API。密钥文件不应复制进仓库；后续只通过环境变量读取。

## 参考文献与资料等级

1. Lewis, P. et al. *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*. NeurIPS 2020（机器学习顶级会议，RAG 原始论文）. <https://proceedings.neurips.cc/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html>
2. Karpukhin, V. et al. *Dense Passage Retrieval for Open-Domain Question Answering*. EMNLP 2020（自然语言处理顶级会议，DPR 原始论文）. DOI: 10.18653/v1/2020.emnlp-main.550. <https://aclanthology.org/2020.emnlp-main.550/>
3. Pham, L. et al. *RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data*. WWW Companion 2025（与本项目数据和 RCA 评测高度相关；属于 WWW Companion，并非主会长文）. DOI: 10.1145/3701716.3715290. <https://openreview.net/pdf/f0ad03673af1f847033420d0bceb6a2df29872ef.pdf>
4. Notaro, P., Cardoso, J., Gerndt, M. *A Survey of AIOps Methods for Failure Management*. ACM Transactions on Intelligent Systems and Technology, 2021（ACM TIST 同行评审综述）. DOI: 10.1145/3483424. <https://doi.org/10.1145/3483424>
5. JSON Schema. *Draft 2020-12 Specification*（本项目机器可读契约采用的官方规范，不属于论文）. <https://json-schema.org/draft/2020-12>
