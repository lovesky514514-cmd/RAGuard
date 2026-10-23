<h1 align="center">RAGuard</h1>

<p align="center">Risk-Aware and Side-Effect-Safe Recovery for Tool-Using Agents</p>
<p align="center">面向工具调用型 Agent 的安全恢复研究系统</p>

RAGuard 是一个本地研究原型：Python 负责可信业务身份、持久化副作用账本与恢复策略，Go 负责可控故障注入和独立副作用真值。它区分“调用失败”和“业务未执行”，在证据不足时优先安全停止。

> **研究边界：** 不是生产安全网关，不保证通用 exactly-once；现有探索结果尚未证明优于具有稳定业务级幂等键的强基线 B3。

## Start here

- [使用说明](docs/USAGE.md)：接口、故障注入和启动步骤
- [设计说明](docs/DESIGN.md)：身份、账本、证据与恢复
- [安全边界](docs/SECURITY.md)：重试、回放与补偿限制
- [实验协议](docs/EXPERIMENTS.md)：基线、物理真值和统计
- [问题记录](docs/问题记录.md)：持续维护的唯一问题记录

## Windows quick start

Windows x64 需要 Python 3.11+。内置经校验的 Go 沙箱可供普通演示与实验使用，不需要额外安装 Go。

1. 完整解压项目，双击 **启动.bat** 运行本地演示。
2. 双击 **实验.bat** 执行故障及工作流试验。
3. 双击 **检查.bat** 进行本地自检。Go 源码构建与测试需要另装 Go 1.21+。

演示服务仅监听本机回环接口。遇到问题请查看本机生成的日志，**不要公开包含设备信息的日志或密钥**。

## What it does

| 能力 | 作用 |
| --- | --- |
| Trusted identity | 同一可信业务引用不会因 Agent 改写步骤名而变成新业务 |
| Persistent ledger | 写操作前持久登记，支持单机恢复与执行代次约束 |
| Evidence-aware recovery | 按回读证据与服务端幂等契约决定回放、重试或停止 |
| Safe stop & compensation | 未知写操作不盲目重发，补偿需授权及依赖检查 |
| Research benchmarks | Go 故障沙箱、B0–B3 对照、参考 Saga 和统计模块 |

## Source layout

| 模块 | 内容 |
| --- | --- |
| Python Gateway | 请求校验与恢复入口 |
| Identity & Ledger | 可信身份和持久状态 |
| Go Sandbox | 故障与外部效果真值 |
| Benchmarks | 对照实验与统计 |
| Tests & Scripts | 回归验证及分发检查 |

## Licensing and distribution

项目按 [Apache License 2.0](LICENSE) 开源；署名 **SuKoYa**，联系邮箱 **[lovesky514514@gmail.com](mailto:lovesky514514@gmail.com)**。分发说明见 [NOTICE](NOTICE)。

研究结果目前仅供探索，不代表 SCI 论文创新性已经得到验证。

业务身份重规划与证据不可用的受控对照，可参考 [实验协议](docs/EXPERIMENTS.md)。
