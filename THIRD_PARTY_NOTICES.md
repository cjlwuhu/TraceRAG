# 第三方代码归属

TraceRAG 基于 [BUAADreamer/EasyRAG](https://github.com/BUAADreamer/EasyRAG) 改造，保留根目录 `LICENSE` 的 MIT 许可及原版权声明。EasyRAG 论文引用见 README 和 CITATION.cff。

`src/easyrag/utils/` 中以下模型适配文件沿用上游实现及各文件原有版权头，适用 Apache License 2.0；许可副本为 [Apache-2.0.txt](third_party/Apache-2.0.txt)：

- `gemma_config.py`、`gemma_model.py`：Google 与 Hugging Face 模型实现。
- `configuration_minicpm_reranker.py`、`modeling_minicpm_reranker.py`、`efficient_modeling_minicpm_reranker.py`：保留文件中的 EleutherAI、Hugging Face 版权及 MiniCPM 适配说明。
- `modeling_qwen.py`、`tokenization_qwen.py`：保留 Qwen、Alibaba、Hugging Face 版权和上游来源说明。

这些模块属于可选的原版本地模型路径；新的离线运维入口不要求加载模型权重。外部模型、RCAEval 算法与数据各有独立许可，不随本次源码快照分发。

本仓库的下载来源锁和参考文献记录外部资料来源；代码许可不能替代资料本身的使用条款。
