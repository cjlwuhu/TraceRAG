# 第 10 阶段检测参数与可复现浏览器验证

日期：2026-10-03。这一步提供实验参数入口与工程验证，不声明异常检测准确率提升。

## 检测参数

`SignalOptions` 与网页 CSV 导入区新增 `warmup_points`、`consecutive_points`、`minimum_metrics`、`max_gap_seconds`。默认值分别为 300、3、1、5，保持原 RCA 基线。参数随任务保存，并直接传递给外部 RCA 的 profile；严格整数、范围校验在入队前执行。

先新增回归测试并观察有效参数因未知字段而失败，再实现字段和 profile 透传。两项定向检查通过；运行日志为 `outputs/stage10-detection-red.log` 和 `outputs/stage10-detection-green.log`。

## 干净源码的浏览器夹具

旧浏览器测试依赖 `outputs` 中一个历史事件和三份工单，还硬编码本机 Python 与真实 CSV 路径。现在启动测试时从跟踪的教学资料生成临时事件和工单，所有合成候选、警告和复核都有自动化夹具标识。测试不修改真实知识入口、人工标签或工单。

`TRACERAG_TEST_PYTHON` 选择 TraceRAG Python，`TRACERAG_TEST_RCA_ROOT` / `TRACERAG_TEST_RCA_PYTHON` 选择外部 RCA 及其独立 Python，`PLAYWRIGHT_CHANNEL` 可选择已安装的 Edge；默认使用 Playwright Chromium。真实 CSV 测试需显式设置 `TRACERAG_TEST_CSV` 和可用 RCA，未提供时清楚跳过，其余流程无需 RCA。

夹具生成新增失败测试后实现，定向 unittest 通过。浏览器名称/参数检查覆盖 TraceRAG 标题和四个默认值。完整测试数量与源码快照验证结果在阶段总验收中记录。

## 核心注释

在 signal-bundle 转换、检索前过滤与名次融合、最终证据包校验、工单引用核验、事件发布顺序及 CLI 编排处补充中文注释。解释字段边界和设计原因，避免逐行翻译代码。
