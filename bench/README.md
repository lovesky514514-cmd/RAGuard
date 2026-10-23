# 实验模块

以隔离 Go HTTP 服务的 Episode 为单位。故障选择按工具、故障类型、强度、重复编号和种子确定，与方法无关；每种方法都在独立沙箱上执行。

- `runner.py`：单步骤 B0/B1/B2/B3/RAGuard 运行器。稳定键 B3 不削弱；B1/B2 有意模拟不可信 Agent 改写意图。
- `saga.py`：持久化步骤日志、恢复时仅信任正向证据、不可确认的写入停机、按反向依赖安全补偿。
- `workflows.py`：真实跨步骤 B4/RAGuard 对照，不能和单步骤结果合并做一张成功率表。
- `oracle.py`：真实业务键和物理效果 ID 判分，补偿不抹掉历史重复提交。
- `analyze.py`：汇总、P95、严格配对及分层 Bootstrap；缺失配对或重复行会直接报错。
- `faultmatrix.py`：故障矩阵与原始 CSV、研究环境 manifest。

```bash
python -m bench.faultmatrix --mode smoke --repeats 2 --output results/smoke
python -m bench.faultmatrix --mode full --repeats 30 --output results/full
python -m bench.workflows
```

完整单步矩阵为 4 × 6 × 5 × 5 × 30 = 18,000 Episode。故障强度是 episode 级选中概率，1% 档每格 30 次通常不足以形成可靠效果估计。现有结果只有科研工程探索意义。
