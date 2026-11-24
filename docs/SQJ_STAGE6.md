# Stage 6：在途旧请求、时钟不确定性与并发恢复争用

本阶段不是新的 LLM 实验，也不声称发明新的重试或 exactly-once 算法。Stage 5 已检验固定重试下服务端 TTL 和作用域的边界；本阶段补充**原始请求尚未完成，多个恢复线程在同一持久化业务身份上争用**的交互验证。

## 研究问题及既有工作重叠

- [LIMBO：*Where Does Exactly-Once Live?*](https://arxiv.org/html/2609.29095)（Li, 2026, arXiv:2609.29095）已研究服务端晚提交、重投递、读回路径和幂等契约，证明无已知在途上界时仅靠回读不足以保证 exactly-once；本研究不能把这种机制声称为新发现。
- [*Checking Retry Transparency in Agent Tools*](https://arxiv.org/abs/2611.23307)（Li, 2026, arXiv:2611.23307）使用具体软件工具的执行契约与交错测试验证本地“可安全重试”声明，直接重叠于恢复时旧操作仍可执行的边界。
- 本仓库既有 `bench/window_uncertainty.py` 已覆盖单恢复者的晚提交、简单时钟偏差和作用域变更。Stage 6 的局部增量在于：**将多恢复者并发、共享 B5/Gateway SQLite 账本、真实 Go HoldGate、服务端无去重以及带时钟误差的准入条件放到同一个可复核的冻结配对矩阵中**。这仍只是工程机制验证，不足以单独证明期刊创新性。

## 冻结实验矩阵

每个条件均在新的本机 Go 研究服务与 SQLite 工作目录中独立运行。旧请求先到达 Go `HoldGate` 并由客户端超时，**仍在途且未提交**。监督器等待 `pending=1`，才推进模拟服务端时钟；2 或 4 个线程经同一 `Barrier` 放行进行恢复请求，线程结束后监督器才释放旧请求。`pending` 与最终效果日志仅用于实验控制与评分，不给恢复策略读取。

| 条件 | 恢复 tick | 旧请求释放 tick | 客户端时钟偏移 | 作用域 | 服务端去重 | 并发恢复线程 |
|---|---:|---:|---:|---|---|---:|
| native_early_release | 3 | 5 | 0 | same | yes | 2 |
| native_late_commit | 3 | 10 | 0 | same | yes | 2 |
| native_clock_under | 6 | 12 | -3 | same | yes | 2 |
| native_clock_over | 3 | 10 | +3 | same | yes | 2 |
| native_scope_move | 3 | 10 | 0 | moved | yes | 2 |
| no_dedupe_two | 3 | 10 | 0 | same | no | 2 |
| no_dedupe_four | 3 | 10 | 0 | same | no | 4 |
| native_four_workers | 3 | 10 | 0 | same | yes | 4 |
| unknown_guarantee | 3 | 10 | 0 | same | yes, unverified | 2 |

所有 Go TTL 固定为 6 个模拟 tick。`--no-dedupe` 仅扩展本机 `uncertainty` 研究命令，默认 Go 服务仍具有幂等性。对照策略：`unaware`（始终准入）、`client_age`（使用自身钟表简单判断，故意忽略老请求状态的消融）、`verified`（承认旧请求尚未完成，停止恢复）。**三种策略都提供给真正的 B5 与 RAGuard，两套引擎不得获得不同的合同或真值信息。** 后两种停止或准入规则不是新加进 RAGuard 生产 Gateway 的功能。

## 评价与科学边界

每条方法级记录保留所有恢复线程 HTTP 返回状态（200/202/409）、请求次数、Go 独立效果历史、错作用域、重复物理效果、确认时的事后安全性及未解决。即使客户端已经收到 200，如果延迟的旧请求随后产生了额外物理效果，按 `unsafe_confirmation=1` 计算。它是一条**方法级**观测，不能把同一物理重复既记为一次重复效果又记成另一独立事故。

`safe_completion` 要求至少一名恢复者报告 200，并且最终仅有一项正确作用域效果；`unresolved` 表示没有恢复线程收到 200，**不等于业务最终未执行**，因为旧请求随后仍可能提交。Go 真实并发调度无法由 Python `Barrier` 控制为严格同时到达，所以各次真实 HTTP 数量或并发线程的最终响应允许不同，不应把固定矩阵重复当作随机总体抽样。任何显著性结论必须另行验证。

`verified` 组已知“原请求尚未完成”这一前提；在真实部署中如何可靠获得这一事实仍未解决。该组测量**有正确信息时保守停止的代价**，不是提供了一种可直接部署的旧请求检测算法。不得推广到真实支付、邮件、第三方供应商 SLA 或未知时钟。

## 实现和复现

在解压后项目根目录执行（需 Python 3.12、Go 1.23+）：

```powershell
python -m unittest bench.test_stage6_concurrent_recovery -v
python -m bench.stage6_concurrent_recovery --repeats 3 --seed 20261124 --output results/stage6_concurrent_local
```

预期 9 条固定条件 × 3 个策略 × 2 个引擎 × 3 轮 = **162 条方法级执行，81 组公平配对**。输出：`episodes.csv`、`paired_summary.csv`、`summary.csv`、`fault_contracts.json`、`REPORT.md`、`SHA256SUMS.json`。所有结果写完后才形成完整数据包；不能用 `--quick` 的 3 条条件子矩阵冒充完整实验。

```powershell
Compress-Archive -Path ".\results\stage6_concurrent_local\*" -DestinationPath ".\RAGuard_Stage6_Results.zip" -Force
```

不连接真实外部业务 API；不得提交密钥、个人绝对路径或真实业务数据。PR 应保持 Draft，直到独立研究质量审查和 Windows/Linux CI 完成。
