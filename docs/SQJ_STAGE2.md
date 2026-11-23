# SQJ 第二阶段：真实 Go 来源消融与 Agent 重新规划探针

本阶段保持冻结的 B5、RAGuard Gateway 和安全门禁不变；所有真实写入只发生在独立的本地 Go 沙箱。新增两个**不同研究层次**，不得将运行次数相加冒充同一实验矩阵。

## A. 证据来源单规则消融

```bash
python -m bench.provenance_ablation --repeats 3 --output results/provenance_ablation
```

每个案例启动独立 Go 服务，写入真实模拟效果；目标业务请求的身份、作用域、指纹与诱饵请求之间最多改变一个字段。默认审核检查目标字段，研究对照只在**内存中**撤销指定字段比较；不修改 Gateway、B5 或 Go 服务策略。最后由独立效果记录判断是否出现反事实的错误确认。

五种条件为 `valid`、`absent`、`wrong_identity`、`wrong_scope`、`wrong_fingerprint`。三个消融指标分别检验身份、作用域与指纹绑定的必要性。没有覆盖密码学鉴权、签名伪造、恶意第三方 Provider 或真实网络中间人。

## B. Agent 决策与本地执行结果分离

先执行不含 LLM 的脚本连通性实验：

```bash
python -m bench.agent_replan_probe --mode scripted --repeats 1 --output results/agent_scripted
```

脚本固定遍历 `stop`、`retry_same`、`retry_replanned` 三种建议，两套真实持久化恢复引擎在相同故障前提下分别运行，每次创建新 Go 沙箱。脚本测试**不构成真实大语言模型验证**。

真实模型必须使用 OpenAI 风格的 `/v1/chat/completions` 兼容接口，可对本地服务显式指定：

```bash
python -m bench.agent_replan_probe --mode model --endpoint http://127.0.0.1:11434/v1/chat/completions --model local-model-name --repeats 3 --output results/agent_model
```

远程 HTTPS 端点需另外显式添加 `--allow-remote`；可选认证令牌从环境变量 `RAGUARD_LLM_API_KEY` 读取，不保存于结果。模型提示中**不包含**真实物理效果、故障位置、内部系统密钥或业务数据。原文模型回复只保存 SHA-256。真实模型数据应记录准确模型名、版本、温度、提供商、端点环境与随机种子支持情况；提供商未保证可复现时不能声称确定性。

**安全边界**：模型只是给出恢复建议。真正执行由 B5 或 Gateway 原有逻辑判断，模型不能直接设置已提交状态、篡改可信签名、修改工具参数或读取 evaluator-only 物理真值。重规划诱饵用于研究观察，无法证明所有 Agent 在真实生产系统的安全性。

## 统计与论文表述

每个模块输出 `episodes.csv`、`protocol.json`、`REPORT.md`、`SHA256SUMS.json`。真实模型对照应报告模型所选三类动作、最终确认、未解决、额外效果与真实 HTTP 请求量，不以响应状态替代物理真值。

来源消融的反事实错误确认只能说明指定验证规则对**构造的**反例必要，不能推导生产系统存在该漏洞，也不能将一组弱化规则的对照冒充公平 B5 基线。论文应分别描述固定场景重复、来源消融和真实模型探针。
