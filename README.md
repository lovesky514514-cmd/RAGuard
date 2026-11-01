# RAGuard

**面向工具调用型 AI Agent 的安全恢复与外部副作用评测框架**

RAGuard 是一个 Python + Go 开源研究项目：Python 负责可信业务身份、持久化执行账本与恢复；Go 负责隔离 HTTP 故障注入、有限幂等窗口和独立物理效果记录。它研究的核心问题是：**一次工具调用没有收到回执，并不代表外部操作没有执行。**

> RAGuard 不是生产级支付/订单网关，也不保证通用 exactly-once。当前与独立持久化 B5 强基线的同信息对照未证明独有算法优势。

## 从哪里开始

| 你想做什么 | 建议入口 |
| --- | --- |
| 使用稳定研究原型 | 当前 `main` 的说明与 [已验证研究基线](https://github.com/lovesky514514-cmd/RAGuard/tree/2.0) |
| 阅读最新论文 | [SoftwareX 英文论文初稿](https://github.com/lovesky514514-cmd/RAGuard/blob/research/softwarex-submission/research/SoftwareX_manuscript.md) |
| 独立复现实验 | [完整复现协议](https://github.com/lovesky514514-cmd/RAGuard/blob/research/softwarex-submission/research/SoftwareX_reproducibility.md) |
| 查看最新研究代码 | [SoftwareX 研究分支](https://github.com/lovesky514514-cmd/RAGuard/tree/research/softwarex-submission) |
| 检查代码可读性与规范 | [仓库维护分支](https://github.com/lovesky514514-cmd/RAGuard/tree/maintenance/repository-quality) |
| 反馈问题 | [Issues](https://github.com/lovesky514514-cmd/RAGuard/issues) |

**注意：** `main`、研究分支和维护分支并非完全相同的代码状态。科研复现时请使用论文指定的完整提交 SHA，并记录 Python / Go 版本，不要将不同分支的实验输出混用。

## 主要能力

| 组件 | 功能 | 安全边界 |
| --- | --- | --- |
| 可信业务身份 | 将参数、主体和业务引用稳定绑定 | 防止任务重新规划引入重复写操作 |
| 持久副作用账本 | 外部写入前记录意图与状态 | 超时不能视为未执行 |
| 证据感知恢复 | 对正向回读、已知执行契约与重试权限分开处理 | 回读空白不足以授权重发 |
| Go HTTP 沙箱 | 模拟请求丢失、晚提交、窗口到期和作用域变化 | 仅在本地回环端口运行 |
| 公平基线 | B5 与 RAGuard 使用匹配的业务身份和证据 | 物理真值仅由评估器访问 |
| CI 与复现 | 运行 Python/Go 回归、保存原始数据和 SHA-256 | 受控实验不代表真实厂商安全保证 |

## Windows 快速体验

安装 Python **3.11+** 并配置 PATH。普通 Windows x64 演示使用仓库中附带、经过 SHA-256 校验的 Go 沙箱，不要求额外安装 Go。源码编译和完整研究实验建议使用 Go **1.23**（与研究 CI 一致）。

1. 下载并完整解压仓库，不要直接从压缩包预览界面启动。
2. 双击 `启动.bat`，程序将进行环境检查、启动沙箱与网关，并运行本地演示。
3. 双击 `检查.bat` 执行环境与编码校验，双击 `实验.bat` 运行基础实验。
4. 出错时查看本机日志；不要公开包含设备路径、令牌或私有数据的日志。

演示端口为 Go `127.0.0.1:8181`、Python 网关 `127.0.0.1:8088`。端口冲突会报告错误，不会擅自关闭其他程序。

## Linux / macOS

```bash
sh scripts/start.sh
sh scripts/check.sh
sh scripts/run_smoke.sh
```

自动启动、验证并有序退出：

```bash
python scripts/launcher.py start --self-test
```

## 研究复现与实际结果

最新研究提供独立 DurableBaseline（B5）、真实 Go HTTP 物理日志和审计器。隔离恢复执行链按照 2 引擎 × 2 故障 × 11 证据状态完成了 **44 个受控场景**：观察到 2 次安全确认、42 次未完成、0 次不安全确认、0 次恢复阶段新增真实 HTTP 写入请求。**两套引擎在该实验中持平**。

该协议使用共同的研究适配器禁止二次写入；不存在真实第三方签名验证或不可绕过的服务端执行屏障。结果不能解释为 RAGuard 优于 B5，更不能解释为生产级零风险。

可从 [完整复现说明](https://github.com/lovesky514514-cmd/RAGuard/blob/research/softwarex-submission/research/SoftwareX_reproducibility.md) 获得原始 CSV、实验协议、哈希文件和运行命令。论文投稿工作将独立进行，正式 SCI 录用不作保证。

## 仓库结构

```text
raguard/   业务身份、持久账本与恢复决策
mock/      Go HTTP 沙箱、故障与物理效果真值
bench/     强基线、对照实验、证据预算与统计
tests/     安全属性、持久化与端到端回归
scripts/   启动、自检、发布检查与哈希校验
docs/      使用说明、系统设计、安全边界与问题记录
research/  研究方案、论文草稿和复现实验（研究分支）
results/   参考实验数据和本地临时结果
```

文档入口：[使用说明](docs/USAGE.md) · [设计说明](docs/DESIGN.md) · [安全边界](docs/SECURITY.md) · [实验协议](docs/EXPERIMENTS.md) · [唯一问题记录](docs/问题记录.md)。

## 代码风格与贡献方式

本仓库通过 `python scripts/check_code_style.py` 检查超长文件头、长等号/横线分隔符，并提示 Python/Go 源码是否具备中文说明。检查器有 `tests/test_code_style.py` 回归测试；中文注释提示不代表关键逻辑已经全部人工审查。

科研源代码与维护文档分支分离：`main` 保持稳定演示，`maintenance/quality-handoff` 只包含仓库质量整理；实验与论文以各自研究分支的固定提交 SHA 为准，不能把实验性功能算作主分支已交付。


源码采用**简体中文简短注释**、UTF-8 无 BOM；避免装饰性的长等号或横线分隔；Go 代码遵循 gofmt。最新维护分支提供可读性检查与更详细的研究入口。发现问题时提交最小复现、环境版本与相关测试；不要把计划中的实验写成通过。

仓库代码采用 [Apache License 2.0](LICENSE)，归属与许可见 [NOTICE](NOTICE)。切勿上传个人路径、私有邮箱、API 密钥、支付凭证或客户数据。默认示例仅使用本地模拟工具。
