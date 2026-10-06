# 第 10e 步：adservice_cpu CSV 导入失败排查

日期：2026-10-06（Asia/Shanghai）。用户报告在原 EasyRAG 网页导入 `D:/Project/RCA/data/RE1/RE1-OB/adservice_cpu/1/data.csv` 后提示“CSV / RCA 处理失败”。本次核对日志、原失败任务、运行进程和数据，复现后修正诊断提示，并提供可重新导入的独立副本。

## 1. 现场与实际失败原因

截图对应 `job-1de80156d2834b88bb15d31016e64bdf`，时间 `2026-10-05T16:03:45Z`（北京时间 2026-10-06 00:03:45）。该任务使用 `strict`、MAD 默认参数、BARO 开启；现场服务解释器为 `D:/miniconda/envs/fastapi_env/python.exe`。

用任务保存的 `telemetry.private.csv`、`signal-profile.json` 及同一 RCA Python 复现，子进程退出 1：

```text
ValueError: duplicate CSV column names
```

失败发生在 RCA `load_telemetry()` 检查原始表头时，尚未开始异常检测、RCA 或 RAG。文件第 1、48 列同名 `time`，两列 4,201 行字符串及数值全部一致。共 51 列；数值非有限、空单元格、不合法时间、重复或倒序时间均为 0，采样间隔为 1 秒。

`causal_ffill5_zero_v1` 入口也会拒绝重复表头，实际复现错误为 `duplicate CSV columns are not allowed`，因此切换缺失值处理不会解决此问题。将第二列改名为 `time.1` 会使时间戳进入普通指标，不采用这种绕过方式。

请求中的 `csv_sha256` 是 `fingerprint(data.csv)` 对 JSON 字符串编码的指纹，不等于落盘文件字节的 SHA256；本次没有据此认定文件被改动。

## 2. 可重新导入的副本

位置：`D:/Project/EasyRAG/outputs/debug/adservice-cpu-import/telemetry.unique-columns.csv`。

先验证两份时间逐行完全一致，再使用 CSV reader/writer 只删除第 48 列。保留 4,201 行、50 列（time + 49 指标），所有保留单元格字符串不变；没有填补、排序或删除其他指标，原始文件未修改。CSV 序列化及列数量变化，所以文件字节哈希变化。

- 原始 SHA256：`42e972d3691a20a4db8b0864b50e88212dd24690252c6b29fdecb343e6e6d387`
- 副本 SHA256：`31976d6d92ec4bfdbac31059cc1c75667432e8eceacd34a25468627b22c07f74`
- 操作及逐单元格核对：同目录 `normalization-report.json`。

副本使用原失败任务 profile、原 RCA Python 和 `strict`，实际导出 `analysis=complete`、BARO 排名及 10 条指标摘要，输出位于同目录 `normalized-signals/signal-run-b1b32667921c4c9980a59a6d4c2e55ce/`。

## 3. 错误提示修正与验证

原 `rca_problem()` 没有识别重复表头，返回通用错误。新增回归测试，两个已知 RCA 消息均先因错误码仍是 `rca_input` 而失败，再增加固定、脱敏的类别映射：

```text
code: rca_duplicate_columns
message: CSV 存在重复列名
hint: CSV 列名必须唯一；重复的 time 列只有逐行完全一致时才可保留一列，请另存副本后重新导入。
```

修改同步到 EasyRAG 与 TraceRAG 的 `src/easyrag/console/diagnostics.py`、`tests/test_console.py`，两个目录对应文件哈希一致。未改变检测算法或自动放宽输入校验，异常原文和 CSV 内容仍不回显。

验证结果：

- 新回归及既有诊断测试 2 项通过。
- 原目录完整后端 118 项通过，48.622 秒，退出码 0；记录 `D:/Project/EasyRAG/outputs/debug-adservice-backend-tests.log`。
- 使用临时目录启动同一 FastAPI 应用，通过真实 `/api/telemetry` 路由验证：原文件返回 `rca_duplicate_columns`；副本完成导入，`rag_ready=true`、BARO 成功、10 条摘要。
- 路由回执：`D:/Project/EasyRAG/outputs/debug/adservice-cpu-import/console-import-verification.json`。
- `git diff --check` 通过。

隔离验证没有向正在运行的服务提交事件、修改长期知识或调用云模型。现有服务进程未重启，新的明确错误提示需重启服务后加载；去重副本可以直接重新上传。旧失败日志作为原运行记录保留。本次修复与记录保存在两个本地源码目录，未推送 GitHub。

## 4. 结果边界

导入成功只说明格式、检测/RCA 执行和信号转换通过。本例报警触发指标为 `recommendationservice_cpu`，BARO 首位候选为 `main_mem`，不能据此宣称正确定位到文件所属的注入故障。后续算法效果仍按既有独立评测协议验证。
