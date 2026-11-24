# Stage 5：服务端幂等契约边界的真实 HTTP 配对实验

Stage 4 的真实 Qwen2.5:7b 模型在 27 次决策中全部建议 `retry_same`。Stage 5 不再请求模型：固定恢复行为，考察相同业务键在服务端幂等窗口到期、作用域变化或没有去重时，是否产生重复物理效果。不得将本实验称作新的真实 LLM 推理。

## 研究问题与冻结矩阵

真实执行层：本地 Go `windowlab`，独立 SQLite 状态的真实 B5 `DurableBaseline` 和 RAGuard `Gateway`。初始故障适配器最多放行一次物理写入，之后在初始链路中拒绝重复写入；恢复阶段才恢复发送。所有物理效果历史只供独立判分器读取，不提供给契约门禁。

| 条件 | 故障 | TTL/恢复 tick | 作用域 | 服务端去重 | 已知保证 |
|---|---|---|---|---|---|
| native_inside | after_commit | 6/2 | 同 | 有 | 是 |
| native_at_expiry | after_commit | 6/6 | 同 | 有 | 是 |
| native_outside | after_commit | 6/8 | 同 | 有 | 是 |
| native_cross_scope | after_commit | 6/2 | 改变 | 有 | 是 |
| no_dedupe | after_commit | 6/2 | 同 | 无 | 否 |
| unknown_guarantee | after_commit | 6/2 | 同 | 有 | 否 |
| before_commit_inside | before_commit | 6/2 | 同 | 有 | 是 |
| before_commit_no_dedupe | before_commit | 6/2 | 同 | 无 | 否 |

`unaware` 恢复准入始终允许同一请求再次进入真实 B5/Gateway，用于诊断无合同门禁的结果；`contract` 要求可信保护期限、同一作用域且时间未到期时才准入。**两套方法使用同一合同判定函数。** `unaware` 不是弱化 B5 本身：B5 仍执行其真实持久化代码。

Go 默认使用原来的服务端去重。新增的 `NewServerWithDedupe` 以及 `windowlab --no-dedupe` 仅用于研究模式；不要将开关接入真实订单、支付或生产接口。

## 运行（Windows PowerShell）

要求 Python 3.12、Go 1.23（兼容 Go 1.21+）。在项目根目录执行：

```powershell
python -m unittest bench.test_stage5_contract_boundary -v
python -m bench.stage5_contract_boundary --repeats 3 --seed 20261124 --output results/stage5_contract_local
```

8 个固定条件 × 2 种策略 × 2 套引擎 × 3 轮 = **96 条方法级真实 HTTP 记录**；不是 96 种独立条件。不同轮次运行新的 Go 服务和数据库。记录全部成功、失败和未解决状态，不得删除不利结果。

## 提交证据

实验生成 `episodes.csv`（包括初始、恢复写入次数和物理效果真值）、`summary.csv`、`fault_contracts.json`、`REPORT.md`、`SHA256SUMS.json`。

```powershell
Compress-Archive -Path ".\results\stage5_contract_local\*" -DestinationPath ".\RAGuard_Stage5_Results.zip" -Force
```

上传该 ZIP 供统计与独立 SHA-256 复核。无模型密钥、商业订单或第三方平台 API 参与。

## 解释边界

`duplicate_effects` 和 `wrong_scope_effects` 可能同时发生，不得简单相加。必须同时报告 `unsafe_confirmation`、`safe_confirmation`、`unresolved` 与网络写入成本，防止单纯通过阻止所有业务获取零风险结果。

模拟的 TTL 使用研究时钟 tick，并假定旧请求已完全停止，契约可被核实。没有测量真实供应商 TTL、网络时钟偏差、旧请求仍在途或生产故障发生率。合同门禁由研究框架共享，**不能归因于 RAGuard 独有算法优势**；B5 与 RAGuard 持平必须直接报告。无去重模式是本机研究沙箱，不是向现实服务尝试破坏性重试。
