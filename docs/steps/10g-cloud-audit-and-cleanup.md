# 第 10g 步：Cloud 运行核验、UI 教程与旧代码清理

日期：2026-10-06（Asia/Shanghai）。用户授权核验下载工单的 Cloud 运行，在 `assets` 编写完整教程，清理旧 EasyRAG 残留并上传已有个人 GitHub 仓库。

## 范围与实施顺序

- [x] 根据工单、审计和任务记录核验实际生成服务；不把 Cloud 总开关当作调用证据，不额外调用付费接口。
- [x] 创建 `assets/UI使用教程.md`，逐项说明输入文件、配置、保存目录、人工修改与正式案例。
- [x] 保留现行 BM25、切分、证据/工单/控制台接口与研究工具；移除旧比赛、Streamlit、GPU/OCR 等独立分支及旧宣传资产。
- [x] 将独立 API 简化为现行 Operations 接口，保留对应 HTTP 回归；精简无用配置与依赖。
- [x] 验证完整后端、前端构建、浏览器和原 CSV 五段链路，并独立复核清理范围与发布内容。
- [ ] 向 `cjlwuhu/TraceRAG` 发布经过验证的源码与教程，核对远端提交。

本机时序原始数据、知识版本、工单/实验记录、凭据和依赖环境不删除、不进入源码发布。删除的旧源码与资产先以精确文件清单备份在本机 `outputs`；保留上游许可证与论文引用。EasyRAG 上游 Git 历史及 TraceRAG 现有未提交文件不被重置。

发布使用个人仓库的独立源码副本，以个人仓库当前 `main` 为基础，不推送原 EasyRAG 历史，不强制覆盖远端。

## Cloud 初步核验

下载的 `gen-6d672d8f530e47048bff29db4e92537f.md` 与本机工单 Markdown 字节一致。对应 JSON 配置为 `cloud/qwen-plus`，实际 API 主机为 DashScope，记录了输入 5031、输出 2398、合计 7429 token，生成响应通过结构校验且任务完成。

时间为北京时间 2026-10-06 00:50:38 至 00:52:14。该请求属于 Qwen/DashScope，不属于 GLM/智谱服务。运行成功不代表全部生成陈述都被证据支持；教程将说明如何核验并修订 Redis 原因假设和引用错配。

157 个检索向量都匹配在本次检索前已写入的缓存，强支持 Embedding 复用；没有本次精确命中计数。重排未启用。现有审计没有保留厂商 request/response ID，不能直接替用户核对计费记录。下载 MD 与落盘文件完全一致；详细脱敏依据留在本机 `outputs/cloud-run-audit-6d672.json`。

## 清理与约束修复

保留现行共用模块路径，避免改变代码指纹读取入口：BM25 仍保留 Okapi 0 和正 IDF 2 两种现用算法，文档切分、层级解析、pathmap 和证据元数据保留。退役 BM25S 模式明确报错，不悄悄切换算法。

移除旧 `main/webui/submit/get_ocr_data/preprocess_zedx` 入口、旧 Pipeline/QA/RAG、未用本地模型与 OCR 辅助代码、旧 YAML/下载处理脚本/Dockerfile、旧全量依赖、仅针对已移除 SDK helper 的测试，以及旧宣传图片/PDF。现行独立 API 通过应用生命周期建立 OperationsRunner，不再在 import 时下载 NLTK 或初始化旧模型；保留三个现行 HTTP 接口并绑定回环地址。

当前直接依赖的实际安装闭包为 59 个锁定包；Qdrant/gRPC/BM25S 专用依赖移除，LlamaIndex 自身仍需的 NLTK/OpenAI 等传递依赖保留。新干净环境安装锁文件并通过 `pip check`，未发现依赖冲突。前端间接依赖 `source-map-js` 从 1.2.1 更新到 1.2.2，修复 [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)；更新后 `npm ci` 的审计为 0 个漏洞。

删除前按精确路径复制、核对 SHA256，退役文件及缓存共 171 个、99,066,329 字节，备份位于本机 `outputs/cleanup-backup-20261006/`，清单为 `retired-files.json`。其中包括旧比赛数据/缓存，不包括 `data/external`、知识库和用户实验记录；旧 Git 历史没有改写。

附件暴露的另一个现有约束缺口是模型输出了禁止的具体 shell 指令。本轮增加叙述字段校验，保留合法证据引文及正常工具/手册描述。该校验同时用于未确认草稿的人工修订；教程说明仅标“不支持”无法绕过，具体命令要改为非执行性验证说明。此检查针对明确命令形式，不是完整脚本解析或自动语义支持评估。

## 最终结果

发布副本基于个人仓库 `main` 的 `af0b6d907f622f7660dbc503512bed1f130f2e65`；来源清单含 182 个源码/文档文件，移除 37 个原有已跟踪退役文件。未带入本机输出、数据、知识版本、备份、依赖环境或密钥。

最终验证：

- 干净 Python 3.12 环境完整后端 **124 tests 通过**；发布副本再次运行 **124 tests 通过，50.478 秒**，日志 `outputs/cleanup-publication-tests.log`。
- 前端生产构建通过；Edge 浏览器 **9 tests 通过，58.5 秒**，覆盖真实 CSV 的 RCA 关闭、重复时间列规范化、复核/下载、设置与知识页面，日志 `outputs/cleanup-publication-browser.log`。
- 发布副本使用原始 `adservice_cpu/1/data.csv` 与独立 RCA 环境，五段链路状态 **complete**，6 条最终证据、9 处引用通过独立结构/来源审计；日志 `outputs/cleanup-pipeline-check.log`。未调用云模型。
- 独立复核确认共用查询、HTTP 接口和命令约束回归通过。命令校验误拦/漏拦已修复；尚无未关闭问题。引用审计仍不证明生成陈述或 RCA 根因正确。

GitHub 发布结果在远端提交核验后记录。
