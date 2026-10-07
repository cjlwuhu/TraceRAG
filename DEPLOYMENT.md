# TraceRAG 服务器维护

目标域名为 `https://tracerag.hnustuc.xyz`，服务器 `ubuntu@42.192.107.154`。本次 SSH 使用本机 `C:\Users\DELL\.ssh\tengxunyun_key.pem`；私钥不随项目分发。

## 结构

- `/opt/tracerag/current`：当前源码及已构建的 `web/dist`；发布版本保存在 `/opt/tracerag/releases/`。
- `/opt/tracerag/venv`：Python 3.12 / CPU 检索环境。
- `/opt/rca/current`、`/opt/rca/venv`：独立信号/RCA 工程与环境，保留固定 RCAEval vendor 及许可。
- `/var/lib/tracerag/{data,knowledge,outputs,rca-outputs}`：持久数据；源码目录内使用链接。`/var/lib/tracerag/tmp` 是服务用户独占的临时/分词缓存目录，部署时创建并赋予 tracerag 读写权限。不要在后续源码发布时覆盖或删除持久数据。
- `/etc/tracerag/htpasswd`：Nginx 登录凭据哈希；`/etc/tracerag/environment`：可选服务端云密钥，权限限制为 root，均不进 Git。
- `tracerag.service`：一个进程，监听 `127.0.0.1:8765`；明确允许 `tracerag.hnustuc.xyz`，仅信任回环代理头。
- 独立 Nginx vhost：HTTPS、Basic Auth、真实 Host/Origin 校验和 32MB 上传限制；现有站点使用各自配置。

登录账号和密码保存在操作者本机仓库外的 `D:\Project\TraceRAG-access.private.json`。不要把密码写入 README、handoff、命令日志或 Git。应用操作令牌用于防跨站写入，不替代 Nginx 登录。

## 打包与发布边界

从本地构建前端：`npm --prefix web ci && npm --prefix web run build`。使用 Python 3.12 运行：

```powershell
python deploy/package_release.py --output D:\Project\TraceRAG-release --rca-root D:\Project\RCA
```

输出必须位于两个项目目录之外。`source.tar.gz` 是源码/构建产物，`state.tar.gz` 是本轮完整副本的数据、知识及研究产物，`rca.tar.gz` 是独立 RCA。显式排除所有层级的 `docs`、`.git`、本机依赖环境、开发工具备份、密钥、Windows 的 `service-settings.json` 及临时锁。归档里的旧私有来源路径仅是历史审计记录，不保证在 Linux 可重放。

后续普通源码更新只上传并解压 source/RCA 到新版本目录，再更新 `current` 链接；新目录重新链接持久状态。`state.tar.gz` 仅用于首次迁移或显式授权的数据恢复，不能无条件覆盖已运行的状态。每次上传前检查 tar 清单，确认 `docs/` 和凭据没有进入。

安装依赖使用 `deploy/requirements-linux.txt` 与 `deploy/requirements-rca-linux.txt`；本次已验证环境另存 `deploy/requirements-linux-lock.txt`。不要把 Windows 精确锁直接当成已验证的 Linux 环境。

ε-Diagnosis 使用官方 `sfr-pyrca==1.0.1` 的相关 Python 模块。该版本的安装声明要求旧 sklearn 与 Java 依赖，与 Python 3.12 运行环境不一致；本次先显式安装 RCA 科学计算依赖，再以 `--no-deps` 安装它，并执行实际 BARO/ε 集成验证。RCA 环境的 `pip check` 实际报告 sklearn 版本要求以及未装 javabridge、matplotlib、networkx、pyparsing、schema、tqdm、wheel 共 8 项声明不满足；这些额外模块未被本次 BARO/ε 导入链使用，TraceRAG 主环境的 `pip check` 通过。不得将最小算法验收写成整个 PyRCA 依赖声明一致。后续应替换旧适配器或维护经过验证的依赖声明，不能仅隐藏检查结果。

## 运维与验证

```bash
sudo systemctl status tracerag --no-pager
sudo journalctl -u tracerag -n 80 --no-pager
sudo nginx -t
sudo systemctl restart tracerag
sudo systemctl reload nginx
/opt/tracerag/venv/bin/python /opt/tracerag/current/src/run_pipeline.py --check --rca-root /opt/rca/current --rca-python /opt/rca/venv/bin/python
/opt/tracerag/venv/bin/python /opt/tracerag/current/src/run_pipeline.py --check --rca-root /opt/rca/current --rca-python /opt/rca/venv/bin/python --signal-profile /opt/rca/current/configs/signal_experiments/epsilon_only.yaml
```

确认匿名 HTTPS 返回 401、认证后页面/bootstrap 返回 200、HTTP 跳转 HTTPS；验证真实 HTTPS 同源 POST、错误 Origin/Host 和缺操作令牌均符合预期。CSV 测试须运行到 RCA、检索、草稿和独立审计；readiness 只证明导入，不证明诊断效果。状态测试和占位案例不作为真实科研材料。

证书由 Certbot webroot 管理，挑战目录 `/var/www/tracerag-acme`；保留 `certbot.timer` 与续期后 Nginx reload hook。更新前备份当前链接和状态，回滚时恢复上一源码链接并重启单进程服务；不要用旧状态覆盖新用户数据。

Linux 控制台显示 `linux_aesgcm`（服务器加密存储），可直接在“设置 → 模型服务”保存 Qwen / GLM Key。主密钥 `/var/lib/tracerag/outputs/console/credential-store.key` 和加密设置 `service-settings.json` 属于服务用户，权限为 600；保存在持久目录，源码更新和服务重启不删除它们。备份已保存凭据时必须同时私下保全主密钥与加密设置；主密钥不能进 Git 或公开发布包，丢失后不能从密文恢复。Windows DPAPI 文件不可直接迁到 Linux。

也可通过 `/etc/tracerag/environment` 设置 `DASHSCOPE_API_KEY`、`GLM_API_KEY`/`ZHIPU_API_KEY`，重启服务后生效。UI 保存值优先；UI “清除”明确停用对应凭据并阻止环境变量自动恢复。启用 Cloud 和具体云模块后才调用模型；保存、读取、清除设置均不发起模型请求。默认离线，本次不迁移本机 DPAPI 密钥，也不自动调用计费模型。
