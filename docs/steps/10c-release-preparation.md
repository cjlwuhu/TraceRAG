# 第 10c 步：TraceRAG 发布准备与凭证清理

初次审计日期：2026-10-03；发布补充日期：2026-10-04。第 1–5 节保留审计时的状态，最终交付结果见第 6 节与阶段总验收。

## 1. 实际发现与处理

只读扫描覆盖当时 Git 跟踪文件、未忽略的待提交文件，以及全部分支可达的 356 个历史 blob。扫描只报告路径、行号与类别，没有输出密钥，也没有通过服务商验证密钥有效性。

发布前当前源码存在两类硬编码凭证：`src/configs/easyrag.yaml` 原第 33–34 行的 GLM 密钥列表，`src/easyrag/utils/llm_utils.py` 原第 5 行的 GLM 客户端凭证。现已将默认 YAML 的 `llm_keys` 清空；旧辅助函数仅在显式调用时读取 `GLM_API_KEY`，其次读取 `EASYRAG_LLM_API_KEY`，缺失则抛出明确的 `ValueError`。导入时不创建客户端，SDK 与可选 Torch 均按需导入，视觉辅助函数也不再打印完整服务商响应。原有文本、视觉和本地生成函数的参数及返回值保持兼容。

旧 Git 历史还包含以下凭证类别，清理工作树不会清理这些历史对象：

| 历史路径 | 原行号 | 类别 |
|---|---:|---|
| `src/configs/easyrag.yaml` | 24–25、29–30、32–34 | GLM 密钥 |
| `src/config.py`、`src/easyrag/config.py` | 4–8 | GLM 密钥 |
| `demo/.env` | 3 | GLM 密钥 |
| `src/easyrag/utils/llm_utils.py` | 5、26 | GLM / OpenAI 形态密钥 |
| `src/pipeline/llm_utils.py` | 26 | OpenAI 形态密钥 |

TraceRAG 应从清理后的源码新建 Git 仓库，并建立新的提交历史。不要复制原 `.git` 或推送原历史。原 EasyRAG 工作目录及历史保留在本机；此次没有改写历史、删除产物、提交或上传。源码扫描不能证明凭证从未泄露；旧密钥的吊销与轮换由对应账号持有人处理。

## 2. 上传清单与排除项

建议保留：清理后的 `src/` 源码与安全配置、`tests/`、`web/` 源码及 lockfile、`schemas/`、教学 `examples/`、阶段与研究 `docs/`、依赖说明、`.gitignore`、上游 `LICENSE` 和引用信息。生成的网页构建与测试产物可以重新生成。

排除 `.git/`、`.idea/`、`.env*`、`private-*`、`*.private.*`、下载的 `data/`、运行 `outputs/`、本机 `knowledge/`、模型与缓存、`node_modules/`、`web/dist/`、截图/测试报告、个人服务设置与任何密钥文件。第 9 阶段的已发布知识及运行结果通过来源登记和重建命令复现，不随源码快照上传。`src/data/` 是上游已跟踪的数据与 NLTK 下载资源；现有 ignore 不能移除已跟踪文件，新快照应显式筛除下载缓存及 `.DS_Store`，仅保留所选运行配置确实需要的安全小型资源。

当时工作目录的超 50 MiB 文件只有两份已忽略的 AIOps 下载：`data/external/aiops2024-challenge-dataset/rcp.zedx` 约 174.2 MiB，`umac.zedx` 约 405.4 MiB。旧 Git 历史中未发现超 50 MiB blob。忽略目录约为 data 675.9 MiB、outputs 202.5 MiB、knowledge 44.8 MiB，不应整体上传。

## 3. 上游归属与第三方材料

保留 `LICENSE` 中的 MIT 许可和 `Copyright (c) 2024 BUAADreamer`，以及 EasyRAG 论文引用；README 说明 TraceRAG 基于 EasyRAG 改造，不将上游论文、实验和作者归为本项目新增贡献。

`src/easyrag/utils/` 内的 MiniCPM、Gemma、Qwen 模型适配文件已有 Apache-2.0 版权头。若随快照保留这些文件，需要保留原版权头、附上 Apache-2.0 许可副本并记录来源。数据许可独立于代码许可：AIOps 下载来源记录声明 Apache-2.0，不应将其改称 MIT。此次不把下载数据、RCAEval vendor 副本、模型权重或真实人工审核记录打包。

## 4. 干净克隆的运行条件

离线 RAG 示例可使用 `examples/operations_knowledge/manifest.jsonl`，无需复制当前 `knowledge/active.json` 或全部产物。网页需要安装前端依赖并执行 `npm run build`，Python 依赖按核心运行说明准备。旧 `requirements.txt` 是原 Linux/GPU 复现配置，不能当作 Windows RCA 共享环境的安装方案。

CSV → 检测 → RCA → 信号包仍依赖外部 RCA 项目及独立 Python 环境，需要显式指定 `--rca-root` / `--rca-python`。必要入口是 RCA 的 `scripts/run_signal_workflow.py`，并依赖其 `src/signal_config.py`、`src/signal_workflow.py` 和配置文件；只放两个同级目录并不足以运行。

RCA 的 `pyproject.toml` 当前声明依赖为空，`requirements.txt` 只是环境策略说明，不能自动装齐算法依赖。时序入口直接需要 PyYAML、Pydantic、NumPy、Pandas。BARO 还使用 scikit-learn，并从 `vendor/RCAEval/` 加载固定 1.2.0 源码（文档记录提交 `bc49dbd85bd14032101fb9a69a5a37e9d6d55178`）。可选 ε-Diagnosis 使用 `sfr-pyrca` 及其科学计算依赖。必须按 RCA 项目说明准备 vendor 并单独验证算法导入；TraceRAG 没有合并 RCA 的 `src`。

审计时浏览器测试还使用本机 Python 路径，并复制被忽略的旧 signal_imports / work_orders 产物。因此完整浏览器测试不属于空白克隆即可执行的测试，需要可移植夹具或按文档准备这些输入。离线核心单元测试和显式示例验证应与这些本机集成测试分别说明。

## 5. 发布通道与验证

本机未在 PATH 或常用安装目录发现 `gh`。Git Credential Manager 2.7.3 可调用，但 `github list` 未返回登录账号；此次未读取或输出凭证，也未安装工具。

现有 GitHub connector 提供 create_file、create_blob、create_tree、create_commit、update_ref，可在已有且授权访问的仓库中上传经扫描的快照。它没有创建仓库的工具；create_commit 要求父提交，空仓库需要先用 Contents API 的 create_file 建立安全的首个文件，或采用常规 Git 初始提交。使用 blob/tree 通道时应从空 tree 构建，仅包含允许路径，逐个比对本地 blob 哈希，再创建提交并核对目标 ref；这些能力不能代替创建新私密仓库或可见性验证。此次只审计工具能力，没有执行任何远端写入。

凭证回归测试 `tests/test_credentials.py` 先在旧实现上出现 6 项预期失败，再在清理后全部通过，SDK/本地 GPU 依赖由测试替身隔离，不产生云端请求。测试覆盖导入无客户端构造、缺失凭证、GLM 环境优先级、EasyRAG 环境回退、文本/视觉请求兼容与默认配置无密钥。

2026-10-03 使用 `D:/miniconda/envs/fastapi_env/python.exe`，设置 `PYTHONPATH=src` 后执行 `python -m unittest discover -s tests -v`：114 项全部通过，18.209 秒，退出码 0；存在原有 Jieba / pkg_resources 弃用提示。清理后的 284 个当前发布候选文件重新扫描，未发现非占位符 GLM / OpenAI / GitHub 密钥形态；测试内的明确假密钥保留。该结果对应当次工作树，最终快照仍须重新扫描和验证。

## 6. 最终准备结果

后续已替换浏览器的历史产物依赖和硬编码解释器路径，改用跟踪的教学资料生成隔离夹具。真实 CSV 使用显式外部 RCA 路径及独立 Python；新目录 8 项浏览器流程通过。干净 Python 3.12 环境仅安装 core requirements，117 项后端测试通过且 `pip check` 无依赖冲突；其完整版本保存在 `requirements-windows-lock.txt`。固定 Pydantic v1 和保留 Jieba 所需 setuptools，避免空白环境安装到不兼容版本。

GitHub CLI 从官方发布渠道下载并核对校验和，用户通过设备登录授权自己的 `cjlwuhu` 账号。没有把登录令牌写入源码或输出。新 Git 仓库仅包含允许源码，不复制旧历史；最终仓库 URL、可见性和推送核验以 [总验收](10-stage-review.md) 为准。原 EasyRAG 目录的暂存区和历史保留。
