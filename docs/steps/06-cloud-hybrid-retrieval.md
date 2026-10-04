# 第 6 阶段：云端 Embedding、Hybrid 检索与可选重排

日期：2026-09-15。前置成果见[第 5 阶段](05-telemetry-detection-bridge.md)。本阶段仍是**证据检索**，尚未生成工单、执行处置或完成多模态科研验收。

## 1. 这一步解决什么

BM25 擅长匹配指标名、服务名和关键词；Dense 把文本转换为向量，用相似度寻找语义相关内容；Hybrid 同时运行两者，按排名融合。Reranker 对已召回的候选重新打分，它不能补回未进入候选集的文档，也不负责确认因果。

本机 RTX 3070 Ti Laptop 有 8GB 显存，原配置的 7B Embedding 模型仅半精度权重便超过其容量。因此使用用户授权的阿里云 `text-embedding-v4`（1024 维）和可选 `gte-rerank-v2`，没有安装新的 GPU/OCR 依赖，没有修改 RCA 算法，也没有降级共享 `fastapi_env`。

实际凭据文件为 `D:\Project\qwen_api.txt`。密钥仅在请求时读取，不进入 YAML、实验输出或向量库。直连已通过实际调用；`http://127.0.0.1:7897` 的首次探测失败，故当前配置使用直连。没有修改系统代理、VPN 或证书校验。

```text
问题 + Incident（不含评测标签）
  → 查询构建 / 时间、事件、确认状态过滤
  → Doc / Case / Metric 的正文、知识路径
  → BM25 和/或 Dense（按路 Top-K）
  → RRF 融合，或 round-robin 对照
  → 可选 Reranker（最多 candidate_top_k 条）
  → 去重的 EvidencePack（条数 + 字符预算 + 引用来源）
```

过滤发生在建 BM25 索引、送云端编码和 Top-K 之前。当前事件自己的案例、未来确认案例、其他系统/事件的指标不能因更高相似度而绕过门禁。

## 2. 两层开关及消融设计

服务器/CLI 配置位于 `src/configs/easyrag.operations.windows.yaml` 的 `cloud_services`，默认 `enabled: false`。命令行 `--cloud` 只允许本次进程调用云服务，不会改写配置。HTTP 请求不能设置密钥、文件路径、代理或模型服务器地址。

实验配置位于同一文件的 `operations`：

| 模块 | 参数 | 实际效果 |
|---|---|---|
| 检索方法 | `retrieval.mode` | `bm25` / `dense` / `hybrid` |
| 向量模块 | `embedding.enabled` | 必须恰好在 dense/hybrid 时开启；矛盾配置报错 |
| 向量规格 | `embedding.model/dimension` | 本步支持 text-embedding-v4 的明确维度集合；默认 1024 |
| 知识路径 | `retrieval.path_enabled` | 对稀疏和向量路都生效；不是原始本机磁盘路径 |
| 重排 | `reranker.enabled` | 独立于 Embedding，可测试 BM25 + 重排 |
| 候选预算 | `reranker.candidate_top_k` | 融合后、最终打包前截取候选；不得小于 pack.top_k |
| 原有开关 | sources / query / fusion / pack | 继续控制三域、RCA 扩展、融合和证据预算 |

新增预设：`dense`、`hybrid`、`hybrid_no_path`、`bm25_rerank`、`hybrid_rerank`，位于 `src/configs/experiments/`。原 `full` 仍表示第 4/5 阶段的 BM25 全路基线，不表示自动打开云模型。批量脚本默认仍只执行六个离线预设，不自动产生 API 调用费用。

这些预设是配置对照，不足以单独形成论文效果实验。例如 Hybrid 路数与候选并集可能增加，需要在正式实验中另做统一候选/成本预算的公平比较。

## 3. 现在向量和故障信息具体存在哪里

权威语料仍是 `manifest.jsonl`：每行一个带类型、事件、时间、来源信息的 KnowledgeDocument。原始时序 CSV 留在 RCA，通过 `raw_ref` 关联，不能用向量替代原始证据。

原版 EasyRAG 的 Dense 路径使用 Qdrant；本步新增 OperationsRunner 的小规模科研基线使用**SQLite 向量缓存 + 内存精确余弦检索**。这是一个明确的实现调整：目前样本很小，精确检索便于核对排名且不需启动额外服务；尚未实现新 OperationsRunner 的 Qdrant 后端，不宣称它能支持大规模生产检索。将来可以在契约不变的情况下增加 Qdrant/ANN 对照，并验证其过滤与召回行为。

默认缓存 `outputs/embedding_cache.sqlite`，表结构：

```sql
vectors(
  cache_key TEXT PRIMARY KEY,
  dimension INTEGER NOT NULL,
  vector BLOB NOT NULL,
  sha256 TEXT NOT NULL
)
```

- `cache_key`：缓存格式版本、API 主机、模型、维度、query/document 角色和文本的内容哈希。
- `vector`：小端 float32 数组，经过 L2 归一化；1024 维向量本体占 4096 字节，不含 SQLite 开销。
- `sha256`：核对缓存字节完整性；读取还检查维度、有限数值和向量范数。不是防篡改签名。
- 不存原始正文和密钥，但向量仍应当作敏感衍生数据保护。缓存和实验结果位于 Git 忽略的 `outputs/`。

对单位向量用点积计算余弦相似度，复杂度约为 O(Nd)。负分/零分仍可能是候选，不套用 BM25 的正分匹配门槛；Top-K 只是排序，不是“证据充分”的保证。候选始终带上原文与出处，供下一阶段审查。

模型别名可能由厂商更新。缓存可固定已获得的向量，实验保存实际向量字节哈希与重排分数；这不等于固定了厂商模型权重。正式论文应封存语料、代码、缓存及模型调用时间；要重编码时显式改用新缓存文件，保留旧实验，不能混淆版本。

## 4. 代码改动与阅读顺序

以下均位于 `D:\Project\EasyRAG`：

| 文件 | 作用 |
|---|---|
| `src/easyrag/domain/experiment.py` | 严格配置与模块一致性校验；每次请求独立覆盖 |
| `src/easyrag/retrieval/cloud_models.py` | 阿里云原生 HTTP 接口、10 条 embedding 分批、索引重排、维度/数值检查、脱敏错误 |
| `src/easyrag/retrieval/cloud_runtime.py` | 服务器配置与实验配置分离，延迟读取凭据 |
| `src/easyrag/retrieval/vector_cache.py` | SQLite 内容寻址缓存、完整性校验、并发写入后读取实际持久化值 |
| `src/easyrag/retrieval/operations.py` | 过滤后按域/字段运行 BM25、Dense 或二者；保存向量来源指纹 |
| `src/easyrag/retrieval/evidence_pack.py` | 融合 → 候选重排 → 最终预算，保留原始路分数、RRF 贡献与重排分数 |
| `src/easyrag/domain/evidence_pack.py` | 校验证据与重排轨迹；不得篡改内容、顺序或分数 |
| `src/run_operations.py`、`src/run_ablation.py` | CLI 云服务开关、五个新实验预设 |
| `src/easyrag/pipeline/pipeline.py`、`src/api.py` | 新 OperationsRunner 后端接入，HTTP 能力声明与 422/503/502 分级报错 |
| `src/probe_cloud_models.py` | 显式运行的少量计费探测，不打印密钥或完整向量 |
| `src/verify_operations_artifacts.py` | 只读复核批次、证据门禁、内容指纹，并用缓存重新计算余弦分数 |
| `src/configs/easyrag.operations.windows.yaml`、`src/configs/experiments/` | 默认离线、按需启用云能力 |
| `schemas/experiment-config.v1.schema.json`、`schemas/evidence-pack.v1.schema.json` | 由同一 Pydantic 模型重新导出，供网页使用 |
| `tests/test_cloud_models.py`、`tests/test_dense_operations.py`、`tests/test_operations_api.py` | 模型适配、真实配置行为、缓存、HTTP 回归测试 |

错误不静默退回 BM25：配置矛盾为 422；服务器未启用云后端为 503；云端网络/响应错误为 502。公共错误不回显厂商响应正文、请求头或密钥。GET `/v1/operations/config` 区分“代码已实现”与“本服务器已启用”，已启用也不表示实时网络探测成功。

## 5. 复现命令

从 PowerShell 执行，环境仍是 `fastapi_env`。下面云端命令可能计费，输入是已获授权的本项目回放摘要和教学知识，不上传原始 CSV 或凭据文件内容。

```powershell
conda activate fastapi_env
cd D:\Project\EasyRAG
$env:PYTHONIOENCODING = 'utf-8'
$env:EASYRAG_DASHSCOPE_KEY_FILE = 'D:/Project/qwen_api.txt'

python src/run_operations.py --cloud `
  --incident outputs/signal_imports/import-e94a1dec22f242448e29e4841f1d99bf/incident.json `
  --corpus outputs/signal_imports/import-e94a1dec22f242448e29e4841f1d99bf/corpus `
  --profile src/configs/experiments/hybrid.yaml `
  --query '当前异常如何验证和处置'

python src/run_ablation.py --cloud `
  --incident outputs/signal_imports/import-e94a1dec22f242448e29e4841f1d99bf/incident.json `
  --corpus outputs/signal_imports/import-e94a1dec22f242448e29e4841f1d99bf/corpus `
  --query '当前异常如何验证和处置' `
  --profiles full dense hybrid hybrid_no_path bm25_rerank hybrid_rerank --repeats 2

$env:PYTHONPATH = 'D:/Project/EasyRAG/src'
python -m unittest discover -s tests -v
python src/export_operations_schemas.py
```

HTTP 服务若需启用云模型，要显式调整**服务器**的 `cloud_services.enabled` 并设置凭据环境变量，再启动原 API。不要把密钥放进浏览器请求。默认 API 使用教学 corpus；导入后的事件专属 corpus 仍须通过服务 `data_path` 显式选择，CLI 不会悄悄切换服务语料。网页尚待第 8 阶段实现。

## 6. 验证与科研边界

离线测试：EasyRAG 57 项通过（本步新增 21 项）；RCA 33 项通过。测试验证真正调用/关闭模块、过滤发生在编码前、负余弦分数、缓存冷暖一致、输入预算、响应乱序/异常、重排先于打包、请求隔离与 HTTP 行为。测试中使用的确定性假向量仅检查软件正确性，不能证明模型效果。

真实云端探测：两条文本返回 2×1024 有限单位向量；问题“服务请求超时”对“下游请求出现超时日志”/“用户修改了头像颜色”的重排分数分别约 0.32297/0.00622。这只说明接口可用且该小样本排序符合直觉，分数不是概率，更不是检索准确率。

真实回放语料的批量记录及检查结果见下方验收记录。一次样本的重复运行不是随机种子效果实验，缓存暖启动也不能作为端到端服务延迟基准。尚无独立检索相关性标注，不能报告 Recall、MRR、nDCG 改善，不能由召回顺序声称某服务已确认是根因。

### 本次真实批次验收记录

批次：`outputs/ablations/batch-b2a38edff7b74b0aa95c8bbf67ec82d1/manifest.json`。六种配置各两次，共 12 个独立 run_id；每次选出 1 条手册、1 条教学历史案例、4 条真实时序摘要。缓存共 25 个向量条目（1 个 query + 12 个正文 + 12 个知识路径），不是 25 条故障案例。

| 配置 | 两次 pack_id 是否相同 | 最终证据顺序是否相同 |
|---|---|---|
| full（BM25） | 是 | 是 |
| dense | 是 | 是 |
| hybrid | 是 | 是 |
| hybrid_no_path | 是 | 是 |
| bm25_rerank | 是 | 是 |
| hybrid_rerank | 否 | 是 |

Hybrid + 重排的 7 个候选分数发生微小变化，最大绝对差约 0.0002586，未改变最终顺序。由于指纹包含完整分数，两次 pack_id 分别为 `pack-4410b576038ab3e7`、`pack-455e5189525a6963`。保留真实差异，不通过分数舍入伪造确定性，也不把它解释成服务商已更换模型的证据。

只读复核工具通过 12 份 Schema/指纹/准入检查，并根据持久化向量重算 98 个召回余弦分数，误差均在 1e-6 容差内：

```powershell
python src/verify_operations_artifacts.py `
  --manifest outputs/ablations/batch-b2a38edff7b74b0aa95c8bbf67ec82d1/manifest.json `
  --vector-cache outputs/embedding_cache.sqlite
```

首次 Dense 运行约 221 秒，包含云端编码与网络耗时；缓存后的运行显著更快。两次重排请求耗时也存在波动。这一批次没有控制网络、缓存、并发和计费 token，不能当作模型速度或费用排名。

## 7. 高质量参考文献

BibTeX 位于 [`../references/stage06.bib`](../references/stage06.bib)。按论文实际发表类型列出，不把工程文档当顶会论文，也不为未核查的目录版本填写 CCF 字母等级。

1. Karpukhin et al. **Dense Passage Retrieval for Open-Domain Question Answering. EMNLP 2020 主会论文，6769–6781。** 双编码器稠密检索的重要工作，作为方法背景；本项目使用阿里云模型，不是复现其训练和权重。[ACL 官方论文页](https://aclanthology.org/2020.emnlp-main.550/)，DOI: 10.18653/v1/2020.emnlp-main.550。
2. Cormack, Clarke, Büttcher. **Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods. SIGIR 2009，758–759，短论文。** 支撑多路排名融合，不直接比较不同模型原始分数。[作者论文](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)，DOI: 10.1145/1571941.1572114。
3. Thakur et al. **BEIR. NeurIPS 2021 Datasets and Benchmarks Track。** 支撑跨任务比较与保留 BM25 强基线；不能从通用检索结果推断网络运维领域 Dense 必然更好。[官方论文页](https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/hash/65b9eea6e1cc6bb9f0cd2a47751a186f-Abstract-round2.html)。
4. 阿里云百炼[文本向量同步接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/)、[文本重排接口](https://help.aliyun.com/zh/model-studio/text-rerank-api)。工程实现依据，访问日期 2026-09-15。新文档包含 workspace 专属域名，本适配器也支持明确列出的地域；本机密钥已实际验证使用默认 `dashscope.aliyuncs.com`。旧 `gte-rerank` 与本步 `gte-rerank-v2` 不是同一模型，不能照抄旧模型名。

下一阶段：由受约束的证据包形成带引用、待验证项和风险说明的结构化工单草稿；原始运行日志与生成工单分开存储，生成结果不能自动变成“人工已确认案例”。
