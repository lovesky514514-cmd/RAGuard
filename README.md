# RAGuard

**面向工具调用型 AI Agent 的安全恢复与可复现 HTTP 故障评测框架**

Python 负责可信业务身份、持久化账本与恢复策略，Go 提供隔离故障服务及独立物理效果日志。核心问题是：**工具超时，并不意味着外部操作没有提交**。本项目研究如何在未知提交、回执丢失、幂等窗口到期等情况下依据证据安全确认或停止。

> **研究边界：** 本仓库不是生产级支付/订单网关，不保证通用 exactly-once；相同证据能力下，现有实验未证明 RAGuard 优于独立持久化 B5 强基线。

[英文论文初稿](research/SoftwareX_manuscript.md) · [实验复现](research/SoftwareX_reproducibility.md) · [系统设计](docs/DESIGN.md) · [安全边界](docs/SECURITY.md)

## 功能与研究价值

| 组件 | 作用 | 特别说明 |
| --- | --- | --- |
| Trusted identity | 将业务引用与参数、主体绑定 | 不允许 Agent 自由改写业务身份 |
| Persistent ledger | 写入前记录意图、未知状态与执行代次 | 回执丢失不代表未执行 |
| Go fault sandbox | 模拟提交前断开、提交后丢回执、延迟提交与幂等窗口 | 只接本地回环 |
| Evidence audit | 审计来源权限、身份、时效及冲突 | 回环信任不等于数字签名 |
| Paired benchmarks | 独立 B5、RAGuard 和物理效果 oracle 配对评分 | oracle 不进入恢复决策 |

## 快速开始

运行环境：Python **3.11+**。从源码构建沙箱、复现实验建议安装 Go **1.23**（与 CI 一致）。Windows x64 演示附带带校验值的沙箱可执行文件，普通使用不强制安装 Go。

```bash
git clone https://github.com/lovesky514514-cmd/RAGuard.git
cd RAGuard
git checkout maintenance/repository-quality
python scripts/check_public_release.py
python scripts/verify_manifest.py
```

**Windows**：双击 `启动.bat` 运行演示；`检查.bat` 检查环境；`实验.bat` 运行基础故障实验。遇到错误可查看日志，不要公开包含本机路径、令牌或私有数据的日志。

**Linux / macOS**：`sh scripts/start.sh`、`sh scripts/check.sh`、`sh scripts/run_smoke.sh`。

演示服务只监听回环接口，Go 默认 `127.0.0.1:8181`，Python 网关默认 `127.0.0.1:8088`。实验服务使用独立临时端口，不连接真实业务系统。

## 学术复现

在仓库根目录运行：

```bash
python scripts/check_utf8.py
python scripts/check_code_style.py
python -m unittest discover -s tests -v
python -m unittest discover -s bench -p 'test_*.py' -v
python -m bench.audited_recovery_http --output results/audited_recovery_http
```

若安装了 Go，继续执行：

```bash
cd mock
go test ./...
go vet ./...
cd ..
```

已归档的隔离 HTTP 恢复实验覆盖 **2 引擎 × 2 故障 × 11 证据状态，共 44 个场景**，观察到 2 次安全确认、42 次未完成、0 次不安全确认与 0 次恢复阶段新增 HTTP 写入请求。**B5 与 RAGuard 在该协议下持平**。研究适配器对双方都禁止二次写入；不能将这个结果宣传为生产端 Gateway 的独有安全能力或统计显著优势。

原始文件为 `episodes.csv`、`protocol.json`、`REPORT.md` 和 `SHA256SUMS.json`。完整实验前提、如何独立复现及失效条件见 [复现协议](research/SoftwareX_reproducibility.md)，CI 工件见 [GitHub Actions](https://github.com/lovesky514514-cmd/RAGuard/actions)。

## 项目结构

```text
raguard/   可信身份、持久账本、恢复策略、HTTP 入口
mock/      Go 故障服务器与独立物理效果记录
bench/     B5 强基线、配对实验、预算和证据审计
tests/     核心逻辑与安全回归
scripts/   启动、自检、隐私扫描、哈希校验
docs/      设计、使用、安全边界、唯一问题记录
research/  论文草稿、复现协议与研究判断
results/   固定参考样本；本机生成数据默认不提交
```

## 维护约定

新增代码采用中文的简短 docstring / 注释、UTF-8 无 BOM 和清晰命名；Go 代码使用 `gofmt`；不使用 `==========` 一类长装饰分隔线。维护前请阅读 [代码规范](CODING_STANDARDS.md)。

```bash
python scripts/check_code_style.py
python scripts/check_public_release.py
python scripts/verify_manifest.py
```

复现或报告问题请说明固定提交 SHA、Python / Go 版本、操作系统、命令和实际输出。统一维护 [问题记录](docs/问题记录.md)，不要新增多个版本化问题文档。提交 PR 应包含最小复现和回归测试，正式 Gateway 的写入安全边界不允许因研究方便而被放宽。

## 论文与发布

- [SoftwareX 英文稿](research/SoftwareX_manuscript.md)
- [实验复现说明](research/SoftwareX_reproducibility.md)
- [原创性及投稿定位](research/SoftwareX_positioning.md)
- [投稿前验收](research/投稿验收.md)

条件式证据规划（ECF/CCEP）仅作为探索性研究保留；与掌握相同元信息的强规则持平，目前不支持新算法优越性声明。论文投稿还需要独立环境复现、永久归档及作者信息审核。

项目依据 [Apache License 2.0](LICENSE) 开源，署名见 [NOTICE](NOTICE)。不要提交 API 密钥、个人绝对路径、私有邮箱、客户数据或真实支付记录。
