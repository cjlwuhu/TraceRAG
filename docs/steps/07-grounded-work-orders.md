# 第 7 阶段：证据约束的结构化工单草稿

日期：2026-09-17。前置为[第 6 阶段](06-cloud-hybrid-retrieval.md)的 EvidencePack。本步补上生成、导出与运行记录；尚未实现原始系统日志检索、人工确认回流或网页控制台，不能把本步等同于最终多模态系统完成。

## 1. 工单到底是什么

RCA 给出候选；检索器找证据；生成器把证据整理为人可以复核的诊断草稿。工单不是根因标签，也不是已执行的操作记录。

输出 `WorkOrderRecord v1` 包含：

- 故障摘要 `summary`；
- 待验证根因候选 `root_cause_candidates`，每项附验证方向；
- 影响范围 `impact`：当前三域证据尚不能证明业务范围，必须为 `null`，导出时显示“证据不足”；
- 验证步骤 `verification_steps`；
- 处置建议 `proposed_actions`：可为空；非空时必须含手册引用、待验证前置条件、风险、回退与审批要求；
- 缺失信息、模型声明的限制、系统强制附加的限制；
- 原证据包、生成配置、代码/输入/提示词指纹、模型标识与可用的 token 用量。

状态由服务器固定为 `pending_human_review`、`unconfirmed`、`actions_executed=false`。模型不能设置人工确认状态。没有操作执行工具，没有向 HistoricalCase 知识库写回的路径。

## 2. 为什么不能只要求模型“带引用”

本步分别校验三层内容：

1. **格式**：必须是符合严格 Schema 的单个 JSON 对象；拒绝未知字段、截断输出、重复 JSON 键、NaN 和不完整处置。
2. **引用完整性**：关键陈述必须引用最终证据包内的 ID，并附能在对应正文找到的连续原文。不允许引用只召回但未进入最终包的条目。
3. **工程边界**：缺少范围证据时不能填写影响范围；仅凭历史案例/指标不能提出处置；空证据不生成结论；不得把输出自动当成已确认根因或已执行结果。

这些检查仍**不是语义蕴含或事实正确性判定**。一段真实引文也可能被错误地解释。真实试运行就出现过：引用匹配，但模型由延迟推断结账失败、订单转化率受损，并照搬教学案例提出扩容。原输出保留，附工程复核记录，标为待修订，不伪称人工已确认。

据此修正了设计：只允许当前有证据的字段；Schema 中 `impact` 明确为 null，而不是一边在提示词中禁止、一边又在 Schema 中允许正文。以后接入真实日志/拓扑与可核验影响范围契约，再扩展此字段，不能用提示词凭空补足证据。

另外，检测时刻、观测窗口起点和某指标变化起点不是同一个概念。提示词明确禁止从窗口均值推断精确变化时刻。自然语言中的细微过度推断仍需要人工审核和后续标注评测。

## 3. 两种模式与配置开关

配置入口：`src/configs/easyrag.operations.windows.yaml` 的 `work_order`。它与 `operations` 检索配置分离，避免复用同一证据包时悄悄重做检索。

| 参数/预设 | 作用 |
|---|---|
| `enabled=false` / `generation/disabled.yaml` | 不构建生成提示词、不调用生成模型、不产出工单正文；可保存跳过记录 |
| `mode=extractive` | 默认离线基线：整理原始证据、列出可关联的 RCA 候选和验证方向；不假装是 LLM 推理 |
| `mode=cloud` / `generation/cloud.yaml` | 阿里云 JSON 模式生成；还需要服务器云能力启用或 CLI `--cloud` |
| `include_rca_candidates=false` | 不把显式 RCA 候选送入生成 Prompt；离线模式也不列 RCA 候选 |
| `max_candidates` / `max_steps` | 候选、验证步骤和处置建议的上限，不保证模型必须填满 |
| `max_input_chars` | 完整提示词字符预算，超限报错，不静默裁掉证据 |
| `max_output_tokens` / `temperature` / `model` | 云生成参数，保存到记录中；默认 qwen-plus、4096、0 |
| `repair_attempts` | 默认 0；`cloud.yaml` 显式设为 1，允许一次有记录的格式/引用修复，不重试网络错误 |
| `save_intermediates=false` | 不保存生成文件；API 仍返回结构化结果 |

`src/configs/generation/` 还提供 `extractive_no_rca.yaml` 与 `no_rca_in_prompt.yaml`（后者使用云模式）。

重要的消融区分：关闭**生成 Prompt**中的 RCA 不会撤销它此前对检索排序的影响。若要端到端“不用 RCA”，还要使用检索的 `query.use_rca=false`；在该检索设置下生成器不会重新导入原 Incident 的 RCA 字段。生成器只接收最终包和已筛选的 query_context，不接收原始 CSV、benchmark 标签、预拼接 retrieval_text 或丢弃的证据。

引用校验、时间门禁和禁止自动确认属于完整性约束，不提供可关闭的“消融开关”。

## 4. 三种“日志/工单”不要混淆

| 对象 | 当前位置/用途 | 是否已实现 |
|---|---|---|
| 原始业务/设备日志 | 服务产生的 ERROR、timeout、trace 等；未来 LogRetriever 的输入 | 尚未接入新链路 |
| 本系统运行审计 | 每次生成的 `audit.jsonl`，记录请求、成功/跳过/失败；失败只记录错误类型 | 本步实现 |
| 生成工单 | `work-order.json` 与 `work-order.md`，面向人工审核 | 本步实现 |

默认保存目录是 `outputs/work_orders/gen-<UUID>/`。每次运行新建目录，不覆盖已有实验：

```text
gen-<UUID>/
  audit.jsonl         # 本系统运行记录，不是原始业务日志
  evidence-pack.json # 输入的完整检索记录副本，用于审计，不全部送模型
  prompt.json        # 仅云模式的成功输出保存实际消息；不含密钥
  work-order.json    # 结构化草稿/跳过结果
  work-order.md      # 面向人阅读的导出
```

失败目录只保留审计，不保存厂商错误正文。若启用一次修复且最终成功，`prompt.json` 会保留修复对话中的上一版草稿；`provenance.attempts` 记录每次输入/响应指纹、校验结果和用量，总用量包括被拒绝的尝试。第二次仍不合格则报错，不无限生成。开发中被工程复核判为待修订的早期样本另有 `engineering-review.json`；这是助手的工程检查，不冒充人工运维确认。

这些文件包含事件和证据内容，虽然没有 API 密钥，也应作为项目数据保护；`outputs/` 默认被 Git 忽略。工单 ID 是内容指纹，不是数字签名，不提供对恶意改写者的身份认证。

## 5. 修改了哪些代码

所有下列文件均在 `D:\Project\EasyRAG`；本阶段没有修改 RCA 算法或安装新依赖。

| 文件 | 改动 |
|---|---|
| `src/easyrag/domain/work_order.py` | 生成配置、工单 Schema、引用/状态/数量限制 |
| `src/easyrag/generation/work_order.py` | 构建有边界的 Prompt、离线基线、调用编排、引用/工程规则校验、独立审计与 Markdown 转义导出 |
| `src/easyrag/generation/cloud_chat.py` | 复用服务器侧阿里云传输；JSON 模式、非思考、停止原因检查、严格 JSON 解析、用量白名单 |
| `src/run_work_order.py` | 已有证据包生成或先检索再生成；CLI 云能力显式开关；脱敏错误 |
| `src/verify_work_order.py` | 只读核对来源、内容指纹、最终证据、Prompt、引用和 Markdown |
| `src/api.py` | 新增 POST `/v1/work-orders`；GET 配置补充生成 Schema 与能力 |
| `src/configs/easyrag.operations.windows.yaml`、`src/configs/generation/` | 生成设置与五种预设 |
| `src/export_operations_schemas.py`、`schemas/` | 生成配置、草稿与完整记录三个新 Schema |
| `tests/test_work_order.py`、`tests/test_operations_api.py` | 生成和真实 HTTP 路径的测试 |

原 `/v1/rag` 文本/图片问答链没有被替换。新工作流沿用 `/v1/evidence/pack` 的证据约束。

## 6. 如何复现

### 用已有证据包生成离线基线

```powershell
conda activate fastapi_env
cd D:\Project\EasyRAG
$env:PYTHONIOENCODING = 'utf-8'
python src/run_work_order.py `
  --pack outputs/ablations/batch-b2a38edff7b74b0aa95c8bbf67ec82d1/runs/run-c57bde17cc5640a7b7cf557699a681e9.json
```

### 用同一证据包做云生成（可能计费）

```powershell
$env:EASYRAG_DASHSCOPE_KEY_FILE = 'D:/Project/qwen_api.txt'
python src/run_work_order.py --cloud `
  --pack outputs/ablations/batch-b2a38edff7b74b0aa95c8bbf67ec82d1/runs/run-c57bde17cc5640a7b7cf557699a681e9.json `
  --generation-profile src/configs/generation/cloud.yaml
```

需要从事件重新检索时，不传 `--pack`，改传 `--incident`、`--corpus`、`--query`，可另加 `--retrieval-profile`。例如：

```powershell
python src/run_work_order.py `
  --incident outputs/signal_imports/import-e94a1dec22f242448e29e4841f1d99bf/incident.json `
  --corpus outputs/signal_imports/import-e94a1dec22f242448e29e4841f1d99bf/corpus `
  --query '当前异常如何验证和处置'
```

### HTTP 请求

新接口 `POST /v1/work-orders` 接受 `query`、`incident`、检索 `overrides` 和 `generation_overrides`。服务器先完成检索，再生成；不接受浏览器提交任意本机路径、模型服务器地址、密钥或伪造证据包。

```json
{
  "query": "当前异常如何验证和处置",
  "incident": null,
  "overrides": {"query": {"use_rca": false}},
  "generation_overrides": {"mode": "extractive", "save_intermediates": false}
}
```

上例仅展示接口形状，不包含事件；要诊断已导入事件，应将 `incident` 替换为对应 `incident.json` 的完整对象。服务端 `data_path` 还须选定相应语料；CLI 的 `--corpus` 不会修改正在运行的服务配置。

配置错误为 422、未启用云后端为 503、云端/生成校验失败为 502。没有静默退回离线模式，也没有自动反复付费重试。

## 7. 验证与局限

当前离线回归：EasyRAG 76 项通过，RCA 33 项通过。本步新增 19 项测试，覆盖开关、空证据、无效引用、未打包引用、提示词边界、风险/前置条件、范围约束、有界修复、HTTP、保存隔离和记录完整性。真实生成的验收记录见下方补充。

现阶段只验证了软件链路与少量真实 API 调用，没有独立相关性/事实支持标注。不能把“引用检查通过”统计为论文中的证据支持率，也不能报告根因正确率提升。`temperature=0` 不保证云生成逐字相同；模型别名、时间、完整输入和输出必须留档。

后续人工标注至少应分开记录：引用完整性、引文是否真正支持陈述、根因候选正确性、验证/处置的适用性、未知信息是否正确保留。业务影响需要实际日志、拓扑或业务证据，不能以服务名联想代替。

### 本次真实验收记录

所有路径相对 `D:\Project\EasyRAG\outputs\work_orders`：

| 模式 | 生成目录 | 检查结果 |
|---|---|---|
| 真实 Incident → 重新 BM25 检索 → 离线工单 | `gen-a1928301846d4c5284cb1b85dd93d05c` | 已运行；保留 6 条最终证据 |
| 关闭生成 | `gen-8f5eed893026478d819ff21ce0246440` | generation_disabled；没有工单正文或 ID |
| 离线生成、不展示 RCA 候选 | `gen-23c83d453ec341d38b590b1518c1b740` | 已运行；候选列表为空，检索输入保持不变 |
| 云生成，允许一次修复 | `gen-87c6bcfc46b44a338b2ba0f62a94aeed` | 7 条带引用陈述、9 处引用，均可定位；待人工语义修订 |

云端记录使用 qwen-plus，首次返回因处置只引用案例而被拒绝，一次显式修复后通过结构/引用规则。两次调用合计返回用量 14855 tokens（11193 输入、3662 输出），约 166 秒；这是单次现场记录，不是性能/费用基准，也不包含此前独立调试调用的用量。

该云草稿仍不是经确认的诊断：助手工程复核发现摘要自行添加秒单位、部分概括缺少对应引用、个别候选机制没有证据支持。问题保存在同目录 `engineering-review.json`，不能将本样本计作“事实支持率 100%”。它说明了后续逐条审核/标注界面的必要性，而不是证明模型没有幻觉。

本次开发还保留了超时与约束拒绝的审计目录，以及早期需修订草稿 `gen-29028755684a403ab86b9cb37958c07e`。该早期样本不符合修订后的 Schema，不应作为当前版本的通过样本。

核验最新云草稿：

```powershell
python src/verify_work_order.py `
  --run-dir outputs/work_orders/gen-87c6bcfc46b44a338b2ba0f62a94aeed
```

验证器的 passed 仅表示所列结构/来源/字面引用检查通过；请同时阅读工程复核记录。

## 8. 高质量参考文献

BibTeX：[`../references/stage07.bib`](../references/stage07.bib)。

1. **Lewis et al. Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. NeurIPS 2020, volume 33.** RAG 的代表性主会论文，为检索证据参与生成提供方法背景；本项目没有复现其联合训练，也不继承其论文性能结论。[官方论文页](https://proceedings.nips.cc/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html)。
2. **Gao, Yen, Yu, Chen. Enabling Large Language Models to Generate Text with Citations. EMNLP 2023 主会，6465–6488。** ALCE 将正确性与引用质量分开评测，直接支持本项目区分“引用能定位”与“引用支持结论”。我们目前实现的字面校验不是完整 ALCE 指标。[ACL 官方论文页](https://aclanthology.org/2023.emnlp-main.398/)，DOI: 10.18653/v1/2023.emnlp-main.398。
3. **阿里云百炼：结构化输出、Chat API 官方文档。** 只作为工程接口依据，不是顶会论文。JSON Mode 不能替代应用侧 Schema/证据校验。[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[Chat 接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)，访问日期 2026-09-17。

下一阶段将接入网页操作：选择事件、模块开关、证据与工单同屏核对、编辑/导出和实验对比。人工确认回流、原始日志/拓扑/图片契约以及正式科研数据评测仍需继续实现。
