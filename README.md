# RAGuard

面向工具型 Agent 的安全恢复研究系统。Python 负责状态管理与恢复判断，Go 负责可注入故障的本地服务。仅用于研究沙箱，不连接真实支付或业务系统。

## Windows 快速启动

直接启动需要 **Python 3.11+**；随包已提供 Windows x64 Go 沙箱程序，启动、实验和日常检查均不强制安装 Go。若要自行重新编译沙箱、运行 Go 源码单元测试和 `go vet`，需另行安装 **Go 1.21+**。安装 Python 时请启用 PATH，并重新打开终端。项目核心模块无需额外安装第三方 Python 包。

1. **解压全部文件**，不要在压缩包预览窗口里双击。
2. 双击根目录的 **`启动.bat`**，自动运行经过哈希校验的内置沙箱、启动网关并执行订单演示。
3. 启动后，窗口保持打开；按 `Ctrl+C` 关闭服务。出现错误时窗口不会自动关闭。
4. 双击 **`检查.bat`** 执行 Python、编码及内置沙箱验证；双击 **`实验.bat`** 运行真实故障试验。未安装 Go 时，Go 源码测试和静态检查会被明确标为未执行。

如未能启动，请将 **`logs/运行日志.txt`** 发给开发者。已经明确的问题与修复处理都记录在 **`docs/问题记录.md`**，其他文件不再重复记录排错历史。

> 端口：Go 沙箱 `127.0.0.1:8181`；Python 网关 `127.0.0.1:8088`。端口被占用时程序会报错退出，不接管其他服务。

## Linux / macOS

```bash
sh scripts/start.sh          # 启动演示
sh scripts/check.sh          # 检查代码
sh scripts/run_smoke.sh      # 故障冒烟实验
```

跨平台无交互启动检查（完成演示后自动关闭进程）：

```bash
python scripts/launcher.py start --self-test
```

## 核心目录

| 路径 | 内容 |
|---|---|
| `raguard/` | 业务身份校验、持久账本、执行与恢复策略、HTTP 网关 |
| `mock/` | Go 工具沙箱、真实连接断开与副作用真值 |
| `bench/` | B0–B4 对照、故障矩阵、跨步骤 Saga、统计与评估 |
| `tests/` | 安全属性、并发、端到端与启动故障回归 |
| `scripts/` | 启动器、自检与编码检查 |
| `results/` | 研究用探索结果（不等于论文正式数据） |
| `logs/` | 本机运行日志（生成文件不纳入源码交付） |
| `docs/问题记录.md` | 唯一持续更新的问题、修复与验证记录 |

设计边界参见 `docs/DESIGN.md`，实验方法参见 `docs/EXPERIMENTS.md`，API 请求见 `docs/USAGE.md`，安全约束参见 `docs/SECURITY.md`。

## 开源许可

源代码按 [Apache License 2.0](LICENSE) 许可，版权说明见 [NOTICE](NOTICE)。
公开的是研究沙箱代码，不是经过生产安全认证的第三方 API 代理。
随仓库提供的 Windows 沙箱程序由 `mock/` 的 Go 代码构建，使用前仍需检查 SHA-256。
发布前执行 `python scripts/check_public_release.py` 和 `python scripts/verify_manifest.py`。

## GitHub 源码与安全

仓库不提交 `data/`、`logs/` 或本机生成的实验结果。`bin/raguard-mock.exe` 是本地 Windows x64 演示依赖，必须与同目录哈希文件一起保留。当前随仓库的 `results/reference_*` 是**探索性参考数据**，不是 Windows 用户本次生成的实验结果。

## 科研状态

本地模拟实验已经具备可运行的故障对照，但还缺真实多 Agent 跨域验证与正式统计实验。已有探索中，RAGuard **没有优于强幂等基线 B3**；不可将现有数据声称为论文已验证的创新优势。

源文件注释以简体中文为主，UTF-8 无 BOM。单个工具的测试通过不代表真实第三方 API 可以保证 exactly-once。
