# 第 8 阶段补充：简约界面、服务设置与运行日志

实施日期：2026-09-21。基于第 1–8 阶段的事件、检索、工单与复核链路重构控制台。本补充不占用“第 9 阶段：多模态与科研验收”的编号。

## 1. 界面

- 白色内容区、浅灰侧栏、黑灰按钮；错误时才使用低饱和红色。
- 移除宣传标语、英文装饰标题、阶段编号、流程图与重复教程。
- 工作台保留事件、问题和结果。实验参数默认折叠，来源、RCA、路径、融合、重排、生成与高级 JSON 仍可配置。
- 圆角问题输入框、发送按钮、Ctrl / Command + Enter；可收起侧栏，增加最近工单入口。
- 结果统计压缩为一行；原文、路由贡献、查询配置和证据边界按需展开。
- 保留引用核验、未确认候选、未执行处置与人工修订；适配 390px 手机宽度。

## 2. 设置与密钥

左下角“设置”或右上角模式按钮打开设置弹窗：

| 页面 | 内容 |
|---|---|
| 通用 | Cloud 总开关、默认生成服务 Qwen / GLM |
| 模型服务 | GLM / Qwen 各自的 API Key、清除密钥、GLM 模型名、Qwen 地域与生成模型、连接测试 |
| 网络 | 本地 HTTP(S) 代理；留空直连；10–120 秒超时 |

Qwen 沿用 `text-embedding-v4`、`gte-rerank-v2` 和 `qwen-plus/qwen-flash`；GLM 默认 `glm-4.7`，可填写账号支持的 `glm-*` 文本模型。GLM 生成与 Qwen 检索独立使用各自密钥，允许同一实验组合使用。

开启 Cloud 只开放云能力，还需在实验配置中选择 Dense / Hybrid、重排或 Cloud 生成。保存设置和打开网页不调用模型；“测试连接”会显式发送少量请求，可能计费。Qwen 测试验证 Embedding，GLM 测试验证 JSON 生成，不代表全部模型权限或完整诊断质量均通过。

设置立即影响新任务，无须重启。已提交任务持有当时的连接配置快照；关闭 Cloud 阻止新云任务，**不取消已提交任务**。完整实验配置中的生成模型是本次任务实际使用的模型。

### 存储规则

- `outputs/console/service-settings.json` 原子保存普通偏好与 Windows DPAPI 密文；`outputs/` 已被 Git 忽略。密文绑定当前 Windows 用户。
- 读取设置只返回 `saved/environment/missing/disabled` 状态，不返回密钥或后缀。保存成功清空密码输入框；不写入浏览器存储、任务、工单、导出或日志。
- 留空保留现有密钥；勾选清除会禁用该服务的环境变量回退，可重新输入新密钥。
- 尚未在网页配置对应密钥时，兼容 `DASHSCOPE_API_KEY` / `EASYRAG_DASHSCOPE_KEY_FILE` / 原服务端 `key_file`，以及 `GLM_API_KEY` / `ZHIPU_API_KEY`。不自动搜索或迁移其他目录中的私有密钥。
- Windows 之外不提供明文存储回退，可继续使用环境变量；本次验收平台为 Windows。
- Qwen 选择标准官方地域入口，GLM 固定智谱官方域名。代理只接受本地回环 HTTP(S) 地址，不允许浏览器指定任意模型主机或本机路径。

设置只作用于新 `operations_console.py`，未迁移旧 Streamlit、`api.py` 或 CLI 配置。CLI 如果使用 GLM 模型但没有对应后端，会明确拒绝，不会误发给 Qwen。启动参数 `--cloud` 仅在没有网页保存设置时提供初始值；已有网页配置优先。

## 3. 错误与日志

`outputs/console/logs.json` 保留最近 500 条服务事件，刷新和重启后仍可查看。完整任务记录继续单独保存在 `jobs/job-*/job.json`。

页面顶部显示原因与处理建议。运行日志可筛选级别，展开查看错误码、服务、HTTP 状态、任务 ID、失败阶段和校验字段。不会回显服务商响应正文、请求头、API Key 或未经处理的 Python 异常文本。

| 情况 | 页面信息 |
|---|---|
| Cloud 关闭 / 密钥缺失 | 明确指出设置入口及缺少哪个服务的 API Key |
| HTTP 401 / 403 | 鉴权失败，检查密钥、地域与模型权限 |
| HTTP 429 | 限流或额度不足 |
| 网络失败 / 超时 | 检查网络、代理或超时时间 |
| 配置错误 | 对应字段、类型或范围；常见模块冲突给出具体要求 |
| CSV / RCA 失败 | 时间列、数值、窗口、缺失依赖等常见校验类别 |
| 草稿或引用不合格 | 输出校验失败，保留生成审计 |
| 服务中断 | 保留中断任务，不自动重试云调用 |

浏览器连接异常单独在当前页面保留最多 50 条，重复轮询不刷屏；恢复后自动同步状态与操作令牌，并清除断线提示。刷新会清空客户端日志，服务端日志保留。未识别的内部异常使用脱敏通用信息和失败阶段定位。

这里展示的是 **EasyRAG 自身运行日志**，不是第 9 阶段尚未实现的原始业务日志 / LogRetriever。

## 4. 主要代码

| 文件 | 职责 |
|---|---|
| `web/src/App.vue`、`style.css` | 简约布局、折叠参数、结果与日志、连接恢复 |
| `web/src/ServiceSettings.vue`、`Icon.vue` | 设置弹窗、密钥表单、统一图标 |
| `src/easyrag/console/settings.py` | 设置校验、DPAPI、Qwen / GLM 运行时快照 |
| `src/easyrag/console/diagnostics.py` | 脱敏错误分类、CSV 诊断、有界持久日志 |
| `src/easyrag/console/server.py` | 设置、连接测试、日志接口与任务阶段记录 |
| `src/easyrag/retrieval/cloud_models.py` | 注入任务凭据，安全 HTTP 状态与网络分类 |
| `src/easyrag/generation/cloud_chat.py`、`work_order.py` | GLM 后端检查，代码指纹纳入适配实现 |
| `src/easyrag/domain/work_order.py`、`schemas/` | GLM 模型名校验与同步 Schema |
| `src/run_operations.py` | 注入控制台专属云运行时，CLI 默认行为保留 |
| `tests/test_console.py`、`web/tests/console.spec.js` | 凭据、路由、日志脱敏、浏览器工作流 |

新增接口：`GET/POST /api/settings`、`POST /api/settings/test`、`GET /api/logs`。写操作沿用同源检查与操作令牌；服务继续只监听回环地址。没有新增 Python/npm 依赖。

## 5. 启动与验证

```powershell
cd D:\Project\EasyRAG\web
npm.cmd run build
cd D:\Project\EasyRAG
D:\miniconda\envs\fastapi_env\python.exe src\operations_console.py
```

打开 <http://127.0.0.1:8765>。如旧服务仍运行，先停止旧服务再启动。首次默认离线，之后读取保存的设置。

```powershell
cd D:\Project\EasyRAG
$env:PYTHONPATH = 'D:\Project\EasyRAG\src'
D:\miniconda\envs\fastapi_env\python.exe -m unittest discover -s tests -v
cd web
npm.cmd test
```

最终验收：生产构建成功，EasyRAG 全量 **90 项**后端测试通过，Edge / Playwright **6 项**浏览器测试通过。测试写入临时目录；云响应和网页密钥使用测试值，不修改真实凭据，没有实际调用付费模型。真实离线检索、CSV → RCA、复核下载、消融、关闭生成及旧工单兼容均有浏览器覆盖；额外验证 DPAPI 持久化、清除与环境回退、GLM 生成路由和凭据不进入产物。

截图：`outputs/console-qa/desktop-workspace.png`、`desktop-results.png`、`mobile-workspace.png`、`settings-models.png`、`settings-mobile.png`、`logs.png`、`review.png`。已查看桌面、结果、手机、设置和日志截图进行布局检查。

真实密钥有效性、模型权限与网络连通性仍需保存用户自己的 API Key 后，显式点击连接测试验证。模拟云响应通过只说明软件链路正确，不证明模型诊断效果。

## 6. 工程参考

GLM 接口地址、Bearer 认证、JSON 输出和 thinking 参数依据[智谱对话补全官方文档](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)（2026-09-21 查阅）。Qwen 复用第 6–7 阶段已实现的官方接口适配。本次为交互和工程能力改进，没有新增科研准确率结论。
