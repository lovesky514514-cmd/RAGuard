# RAGuard Stage 2：真实 LLM 本机实验操作指南

本文件对应研究分支 `research/sqj-stage2-agent-ablation`，不是正式论文结果。实验只会在本机 Go HTTP 沙箱执行模拟订单，不会访问真实支付、订单或云服务。

## 1. 环境

Windows 10/11，Python 3.12（CI 使用 3.12）、Go 1.23（CI 使用 1.23）、可用的 OpenAI 兼容 Chat Completions 模型服务。代码使用 Python 标准库，无需安装 Python 第三方依赖。先验证：

```powershell
python --version
go version
```

建议把压缩包解压到英文路径（如 `C:\Research\RAGuard_Stage2`），并在该目录打开 PowerShell。确认目录中同时存在 `bench`、`raguard`、`mock` 与 `scripts`。

## 2. 先测试隔离沙箱（不需要模型）

```powershell
python -m unittest bench.test_agent_replan_probe bench.test_provenance_ablation -v
python -m bench.agent_replan_probe --mode scripted --repeats 1 --output results/agent_scripted_local
```

以上脚本实验应产生 12 条方法级记录，验证的是 Go 沙箱与 B5/Gateway 的调用链，**不是 LLM 推理实验**。若 Go 尚未配置到 PATH，先修复 Go 环境。

## 3. 使用本机 Ollama（推荐，避免外部上传）

在本机安装并启动 Ollama，使用 `ollama list` 查看可用模型。尚无模型时可以自行下载一个 Chat 模型，例如：

```powershell
ollama pull qwen2.5:7b
```

Ollama 应在 `127.0.0.1:11434` 提供 OpenAI 兼容接口。Windows 上 Ollama 可能已经常驻，无须重复运行 `ollama serve`；如果没有监听端口，另开终端启动 `ollama serve`。模型名称以 `ollama list` 为准。

在仓库根目录运行：

```powershell
python -m bench.agent_replan_probe --mode model --endpoint http://127.0.0.1:11434/v1/chat/completions --model qwen2.5:7b --repeats 3 --output results/agent_model_qwen25_7b
```

或使用 PowerShell 包装脚本（需要该脚本可执行）：

```powershell
.\scripts\run_real_llm.ps1 -Model 'qwen2.5:7b' -Repeats 3
```

如果 PowerShell 执行策略限制脚本，直接使用上方 `python -m` 命令即可，不需要改变系统执行策略。

每轮测试两个故障时机，每种故障只调用模型一次，并把**同一模型建议**分别交给 B5 和 RAGuard。因此 `--repeats 3` 对应 6 次模型建议、12 次方法级 Go 沙箱执行。脚本支持 `stop`、`retry_same`、`retry_replanned`，不能认为所有模型都会均匀选择三类动作。

## 4. 使用第三方 HTTPS 模型（可选）

必须使用提供 OpenAI 兼容 `/v1/chat/completions` 路径的 HTTPS 端点；代码默认拒绝访问远程模型，需显式加 `--allow-remote`。示例（把地址和模型名称替换为真实提供商参数）：

```powershell
$env:RAGUARD_LLM_API_KEY = 'YOUR_API_KEY'
python -m bench.agent_replan_probe --mode model --endpoint https://provider.example/v1/chat/completions --model your-model-name --allow-remote --repeats 3 --output results/agent_model_remote
Remove-Item Env:RAGUARD_LLM_API_KEY
```

不要上传密钥、令牌、环境变量截图或包含真实个人信息的第三方服务日志。模型提示只包含模拟订单和模糊提交状态，不包含物理真值与内部业务秘密。

## 5. 输出和交付

实验输出位于 `results/agent_model_qwen25_7b/`（或指定目录）：

- `episodes.csv`：每条方法执行的建议、最终 HTTP 状态、模拟物理效果与错误确认计数。
- `protocol.json`：模型名、模式、实验边界。
- `REPORT.md`：从真实输出生成的基本说明；需结合 CSV 统计分析。
- `SHA256SUMS.json`：原始实验文件的 SHA-256。

运行结束后，可以只把该 `results` 子目录打包发回，不必发送整个源码或密钥。建议同时记录：Python/Go/Ollama 版本、所用模型版本、机器配置、模型的运行设置以及故障与异常日志（删去任何令牌）。

## 6. 科研声明限制

本实验是**真实 LLM 决策 + 本地 Go 模拟写入的集成探针**，并非对真实第三方业务 API 的端到端验证，也不是完整自主 Agent benchmark。模型输出只是建议，不能绕过真实恢复引擎的业务身份和写入门禁。固定协议的三次重复不等于随机化故障多样性；必须保留失败和未解决行，不得挑选有利结果。

如果模型响应不包含严格 JSON `{"action":"..."}`，脚本会报错，不应悄悄将失败样本删除；请保留错误信息供后续诊断。
