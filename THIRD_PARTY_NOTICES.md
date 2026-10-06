# 第三方代码归属

TraceRAG 基于 [BUAADreamer/EasyRAG](https://github.com/BUAADreamer/EasyRAG) 改造，保留根目录 `LICENSE` 的 MIT 许可及原版权声明。EasyRAG 论文引用见 README 和 CITATION.cff。

此前源码版本的 `src/easyrag/utils/` 包含以下 Apache License 2.0 模型适配文件。本次清理已移除这些未被当前控制台使用的模块，Git 历史仍保留原版权头；许可副本继续保留为 [Apache-2.0.txt](third_party/Apache-2.0.txt)：

- `gemma_config.py`、`gemma_model.py`：Google 与 Hugging Face 模型实现。
- `configuration_minicpm_reranker.py`、`modeling_minicpm_reranker.py`、`efficient_modeling_minicpm_reranker.py`：保留文件中的 EleutherAI、Hugging Face 版权及 MiniCPM 适配说明。
- `modeling_qwen.py`、`tokenization_qwen.py`：保留 Qwen、Alibaba、Hugging Face 版权和上游来源说明。

当前源码不再分发上述模型适配模块。仍在使用的 BM25 与文档切分代码保留 EasyRAG 的 MIT 归属。外部模型、RCAEval 算法与数据各有独立许可，不随本次源码快照分发。

本仓库的下载来源锁和参考文献记录外部资料来源；代码许可不能替代资料本身的使用条款。
