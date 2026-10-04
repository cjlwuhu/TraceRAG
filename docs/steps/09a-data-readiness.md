# 第 9 阶段第一步：真实语料、数据划分与待确认案例

实施日期：2026-09-25。前置为[第 8 阶段补充](08b-minimal-console-settings.md)。本步完成数据准备与可复现验收，**没有完成正式准确率评测，也没有新增人工已确认案例**。RCA 与 EasyRAG 继续独立，通过文件契约连接。

后续更新（2026-09-27）：9b–9d 已实施，当前知识入口、冻结基线与多源联调见[阶段总验收](09-stage-review.md)。下文保留 9a 当时状态，其中“网页未接入真实库”和“尚未测试”已由后续步骤更新；真实人工案例仍未提供。

## 1. 对下一步顺序的调整

用户提出“确认历史案例 → 下载原版 AIOps 文档 → 找时序故障数据验证”的方向基本合理，但应先明确三类数据的用途与隔离规则：

| 对象 | 本次处理 | 不能替代什么 |
|---|---|---|
| 原版 EasyRAG 使用的 AIOps 2024 四套电信手册 | 下载固定版本，校验后建立独立 runbook 语料库 | 不能证明 Online Boutique/Sock Shop/Train Ticket 的处置适用性 |
| 真实人工确认案例 | 先整理可审查的待确认材料，确认信息留空 | 算法候选、生成工单、注入标签、助手工程检查均不是人工处置确认 |
| RCAEval RE1 时序及故障标注 | 复用本机已有完整 375 样本，先清点质量和冻结划分 | 不提供问答标准答案、人工处置记录、日志或调用链 |

推荐顺序改为：**确定数据边界与划分 → 引入真实手册、准备人工材料 → 明确预处理和评分协议 → 开发/验证 → 冻结配置后测试 → 扩展日志/调用链与跨模态评测**。

不必先找到“同时包含时序、自然语言问题、正确回答、处置结果”的单一数据库。时序根因评测可使用根因标注；检索相关性与工单陈述支持度需要另外标注，三者应分别报告。

## 2. 原版 AIOps 资料如何入库

来源由本地 `scripts/process.sh` 与上游仓库交叉确认：

- [EasyRAG 上游数据处理入口](https://github.com/BUAADreamer/EasyRAG/blob/main/scripts/process.sh)。
- [ModelScope 数据集](https://www.modelscope.cn/datasets/issaccv/aiops2024-challenge-dataset)。
- 固定数据仓库提交：`854d1d35eb1f3ae5471a2b57f0261e1da8e8f3f4`。
- 下载清单、Git LFS 对象 SHA-256 和大小固定在 `src/configs/data_sources/aiops2024.json`。

下载四个原始 `director/emsplus/rcp/umac.zedx`，同时使用同版本的上游解析文本 `data.zip` 建立**文本基线**。原始 ZEDX 保留以供后续核查图表与重做解析。本次没有运行未知的阅读器程序、没有下载模型，也没有调用 OCR、Embedding 或生成 API。

原始文件保存在：`data/external/aiops2024-challenge-dataset/`。Git LFS 最初部分文件停滞，随后对剩余文件使用同源固定提交的 `resolve/<revision>/<filename>` 地址下载；只有大小与 SHA-256 均匹配才替换 LFS 指针。

```text
原始 ZEDX + 上游 data.zip + 固定来源指纹
  → ZIP 内逐条读取 UTF-8 TXT，不解压到任意路径
  → 过滤空文档、按产品去除完全重复正文
  → KnowledgeDocument[type=runbook, system=zte-aiops2024]
  → 独立 corpus/manifest.jsonl + 原成员映射 + report.json
```

去重只规范 UTF-8 BOM、CRLF 和首尾空白，不合并不同产品，也不声称完成近重复去重。重复成员仍在 `source-members.jsonl` 指向保留文档，可以从 `raw_ref` 的压缩包哈希和成员路径回看原文。ZIP CRC、来源 SHA-256、Schema 均经过检查。

`question.jsonl` 不进入知识库；目录页等非空文本仍保留，尚未做人工相关性筛选。现有 Qdrant 与 SQLite 向量缓存无需迁移，权威语料仍按第 2/6 阶段约定保存为 JSONL，本次没有生成向量。

本轮实际产物：`outputs/knowledge_builds/aiops2024/corpus-dc6b2573461040ed91dbf0bb0277dee2/`。原包有 42,139 个 TXT 成员，过滤 16,403 个空文件、2,397 个产品内重复文件，形成 **23,339 条 runbook**；另外 7 个非 TXT 文件没有摄取。按产品统计为 director 2,167、emsplus 906、rcp 4,866、umac 15,400。实际文本字节和有效文档数以本轮清点为准，不能把上游 README 的概数当成已入库数量。

系统标记 `zte-aiops2024` 是这批电信手册的语料范围标记，不是微服务系统别名。现有 `eligible()` 会将它排除在 `online-boutique` 等事件之外。默认网页教学库和既有事件语料没有切换；这批数据用于独立电信文档检索，不作为微服务处置证据。

## 3. RE1 清点与冻结划分

本机 `D:\Project\RCA\data\RE1` 已有三套数据，各 125 个样本，无须再次下载。按每个 CSV 计算 SHA-256，扫描全部数值和时间列，并读取故障注入标注用于**独立评分侧**的注册表。

| 数据 | 用途 | 样本 | 有缺失/非有限值的样本 | 时间列同时有问题的样本 |
|---|---|---:|---:|---:|
| RE1-OB / Online Boutique | development | 125 | 11 | 3 |
| RE1-SS / Sock Shop | validation | 125 | 70 | 2 |
| RE1-TT / Train Ticket | test | 125 | 125 | 1 |
| 合计 | | 375 | 206 | 6 |

时间列问题与非有限值重叠，不应相加成 212 个问题样本。全部 375 个样本保留，原始文件没有清洗、插值、重排或删除；有限时间戳范围仅供清点，不能理解成已修复时间列。

先前本机预测和信号注册表发现使用过 OB；保守地将整个 OB 系统留在开发侧。75 个“系统＋服务＋故障类型”组均不跨划分，同组五次重复不拆散；跨集合相同字节的时序重复为 0。

这是**跨系统划分**，不是 IID 随机划分、按时间划分，也不适合直接证明同系统历史案例检索收益。现有记录无法证明其他人从未查看过验证/测试数据。后续若发现额外暴露，必须修订协议并保留旧版本，不能继续声称未见测试集。

冻结产物位于：

`D:\Project\RCA\outputs\dataset_preparation\prep-54072ff7386c41178393cf102f1bf184\`

| 文件 | 内容 / 使用方 |
|---|---|
| `questions.jsonl` | 中性样本 ID、划分、系统、固定无标签问题、时序哈希引用；未来流水线入口 |
| `labels.private.jsonl` | 根因服务、故障类型、注入时间、规范指标与标注指纹；仅评分器 |
| `telemetry-registry.private.jsonl` | 原 CSV 路径、哈希、形状和逐例质量问题；仅本地数据加载/审计 |
| `protocol.json` | 固定分组规则、局限、各文件指纹与准备代码指纹 |

`.private` 是文件用途约定，不是加密或访问控制。这些目录不能直接作为 RAG 语料目录。RE1 的公开“具体问题”是可评分的故障根因标注，不是可直接作为工单答案的文字说明。

所有输入使用同一个问题：“当前异常的候选根因是什么？请结合可用证据说明验证步骤；证据不足时明确说明。”不把真实故障服务写进问题，也不把 `inject_time` 交给检测器。

**当前数据质量尚不满足既有严格时序入口。** 下一步先定义可复现的缺失值处理、异常时间行处理、不可用列报告和在线时间边界。不能直接对 206 个样本报根因错误，也不能悄悄跳过它们；正式报告应同时给出覆盖率、失败类别和保留全体分母的端到端结果。

## 4. 待确认材料不等于已确认历史案例

本次从步骤 5 已有真实回放 bundle 整理一份材料，避免再造虚构故障。目录：

`outputs/case_review_queue/review-ffe47c16ada64bf8b9d403ba96de0a7b/`

包含 `review.md`、`review.json`、原事件、10 条观测摘要及 `historical-case.pending.json`。保留算法候选与原始数据引用；根因、实际处置、审核人、审核时间留空，`human_verified=false`，`case_promoted=false`。它不能通过现有 HistoricalCase 准入检查。

人工需要补充的是：引文是否支持陈述、根因是否有独立证据、处置是否真实发生、结果是否有效。RE1 注入标签可以用于判定基准目标，但不证明发生过扩容/重启或故障已经修复。没有这些记录时维持“基准标注/待确认”，不能为了满足 Schema 编造 actions。

还存在重要时间边界：今天（2026 年）审核的案例不能倒填成 2023 年已经确认、供 2023 年事件检索的历史。当前 `verified_at <= detection time` 与当前事件自排除规则保持有效。若要研究固定历史知识库的离线实验，需要另定义知识可用时间与历史/查询事件分割协议，不能直接关闭门禁。

本步没有实现自动签署、正式案例回流或网页确认按钮；助手没有代替用户完成人工确认。

## 5. 复现命令

以下命令不改变默认服务配置，不调用付费模型；各准备命令生成新目录，不覆盖已有实验。

```powershell
cd D:\Project\EasyRAG
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'D:\Project\EasyRAG\src'

# 五个固定版本的数据压缩包；已有正确文件会校验并跳过
D:\miniconda\envs\fastapi_env\python.exe src\download_aiops_data.py
D:\miniconda\envs\fastapi_env\python.exe src\prepare_aiops_corpus.py

# 用本轮全量手册库复现纯文档 BM25 检索；新建库后替换 --corpus 的目录
D:\miniconda\envs\fastapi_env\python.exe src\run_operations.py `
  --corpus outputs\knowledge_builds\aiops2024\corpus-dc6b2573461040ed91dbf0bb0277dee2\corpus `
  --profile src\configs\experiments\doc_only.yaml --query 'CGW 查询链路状态'

# 待确认材料：仍不会进入 case 语料
D:\miniconda\envs\fastapi_env\python.exe src\prepare_case_review.py `
  --bundle D:\Project\RCA\outputs\signals\signal-run-f67c44cf480f47969772a530e8e9520e\signal-bundle.json

cd D:\Project\RCA
# 清点全部样本、校验组隔离并输出新版本协议；不执行测试集模型评分
D:\miniconda\envs\fastapi_env\python.exe scripts\prepare_research_dataset.py
D:\miniconda\envs\fastapi_env\python.exe -m unittest discover -s tests -v

cd D:\Project\EasyRAG
D:\miniconda\envs\fastapi_env\python.exe -m unittest discover -s tests -v
```

下载脚本从固定来源清单即可取回压缩包，不依赖先安装 ModelScope SDK 或完整拉取阅读器；首次源仓库的 README、问题文件及版本元数据保留在本机数据目录。压缩包共约 698 MB，Git LFS 缓存可能额外占用空间。

## 6. 代码改动索引

| 项目 / 文件 | 本步职责 |
|---|---|
| EasyRAG `src/configs/data_sources/aiops2024.json` | 固定上游提交、文件大小、SHA-256、系统范围 |
| EasyRAG `src/download_aiops_data.py` | 固定来源下载、完整性检查、拒绝覆盖未知文件 |
| EasyRAG `src/prepare_aiops_corpus.py` | 逐成员转换、去重、来源映射与独立 JSONL 库 |
| EasyRAG `src/prepare_case_review.py` | 从已校验 bundle 生成待人工材料，不确认、不回流 |
| EasyRAG `tests/test_data_preparation.py` | 去重/范围、问题排除、ZIP 路径、LFS/下载校验、未确认案例门禁 |
| RCA `scripts/prepare_research_dataset.py` | 全量清点、质量报告、按系统划分、标签与公开输入隔离 |
| RCA `tests/test_research_dataset.py` | 相同时序和同故障组不能跨集合，已用系统留在开发侧 |

### 本轮实际验收

- EasyRAG 全量 **94 项 unittest 通过**；RCA 全量 **36 项通过**。日志分别为两项目 `outputs/stage09a-easyrag-tests.log`、`outputs/stage09a-rca-tests.log`。
- 5 个压缩包合计 **697,940,990 字节**，全部与固定 Git LFS SHA-256 和大小匹配。
- 独立重载并验证 23,339 条 KnowledgeDocument；逐条确认它们均被 Online Boutique 的系统门禁排除。
- 对 375 个公开问题检查字段白名单，标签字段为 0；问题、标签、私有时序注册表的中性 ID 一一对应，全部冻结文件及 RCA 准备脚本指纹匹配。
- 实际待确认模板被 HistoricalCase 校验拒绝；没有写入确认案例，人工案例新增数为 **0**。
- 独立结果记录：`outputs/stage09a-verification.json`，这些检查是完整性验收，不是语义正确率。
- 用完整新库执行真实离线 `OperationsRunner`：23,339 文档切成 **58,229 节点**，`doc.body` 与 `doc.path` 各召回 5 条，最终包 6 条。命中 `CGW管理`、`查询链路状态(SHOW CGW LINK)` 等原文，也出现其他产品告警文档；没有相关性标注，不能将此次召回写成准确率或 Recall。
- 真实检索记录：`outputs/operations/run-9ac25f48f3564f33984bbd80e3e7f1f5.json`，`pack_id=pack-5451f42ad141e9bf`。终端摘要见 `outputs/stage09a-aiops-retrieval.log`。未调用付费模型、未生成新工单、未对冻结测试集运行模型。

没有修改网页，因此本步没有重复执行浏览器测试；已有 8b 的浏览器验收只代表当时版本。当前完整库索引在进程内建立，尚未把这一规模的建库耗时与内存作为正式性能基准。

## 7. 后续步骤与验收标准

1. **第 9b 步：预处理与开发集批量基线。** 在 OB 开发集上固定清洗规则，记录每个样本删行/列/填充值，保证检测器不使用未来信息；先验收覆盖率和失败记录，再分别运行 annotation-trigger RCA 与真实 detector-trigger 流水线。
2. **第 9c 步：领域一致的知识与人工标注。** 补微服务适用手册或经过核验的真实工单，建立独立的相关性和陈述支持标注；正式案例入库必须有真实审核及处置依据。电信手册单独做运维文档问答实验。
3. **第 9d 步：验证与冻结后的测试。** 开发/验证完成后封存配置、代码、语料、模型/缓存标识，才运行 TT 测试。检测、RCA、检索、工单正确性分别评分；不能用“引用可定位”充当准确率。
4. **后续多模态。** 同系统 RE2/RE3 提供日志/调用链，适合接后续证据契约；RE1 当前指标基线稳定后再扩展，避免一次引入过多变量。

本轮原始语料与 RE1 的角色不同，尚不构成完整端到端诊断准确性验证。教学案例仍只用于原演示链路，不计入真实案例数量。

## 8. 官方来源与资料用途

1. [EasyRAG 上游仓库](https://github.com/BUAADreamer/EasyRAG)：核对原版文档问答流程及数据入口。
2. [AIOps 2024 数据仓库](https://www.modelscope.cn/datasets/issaccv/aiops2024-challenge-dataset)：四套 ZEDX 与解析文本的直接来源；本机保留固定提交 README，其声明的许可为 Apache License 2.0。代码仓库 MIT 不应被误写为这批数据的许可。
3. [RCAEval Zenodo 固定记录 14590730](https://zenodo.org/records/14590730)：本机 RE1 原始版本的来源；说明 RE1 仅含指标，RE2/RE3 提供其他遥测与根因标注。
4. [RCAEval 作者仓库](https://github.com/phamquiluan/RCAEval)：核对基准系统、重复样本与多源数据范围。上游持续演进，本次没有升级项目已固定的 RCAEval 算法源码。

访问/核验日期：2026-09-25。本步是数据工程与研究协议准备，没有新增算法性能结论，不为流程改动虚构准确率或论文贡献。
