# quant-trading

面向 Python 新手的币安量化交易学习项目。后端使用 Python，第一版界面使用 Streamlit，后续用 pandas 处理行情、Plotly 绘图、CCXT 读取公共行情。

**当前版本：第 1 阶段，离线 PAPER（模拟交易）安全基础。** 所有交易仅改变内存中的虚拟账户，不连接交易所，也不需要 API 密钥。

## 现在可以做什么

- 在网页查看初始资金为 **10,000 USDT** 的虚拟账户。
- 使用固定示例价格 **BTC/USDT = 50,000** 模拟买入、卖出，并查看余额与交易记录。
- 按 **0.1% 手续费**和 **0.05% 滑点**计算成交；虚拟 BTC 持仓受到 **25% 仓位上限**约束。
- 下载当前账户的 JSON 快照，保存到自己的电脑。

示例价格不是币安实时行情。当前账户保存在 Streamlit 会话中；服务重启或开启新会话会重新开始。下载的 JSON 暂不支持导入恢复。当前版本还没有公共行情、K 线、回测、持久化账户或自动交易策略。

## 安全边界

- 初始模式必须为 `PAPER`；当前版本仅支持这个模式。
- 真实下单、提现、转账及杠杆功能全部关闭，并使用独立安全开关防止误启。
- 现阶段不提供交易所订单适配器，不要求输入任何交易所 API 密钥。
- 以后读取 USDT 本位合约行情时，也只做行情分析，不启用杠杆、做空或合约交易。
- 密钥不得写入源码、配置示例、日志或提交到 Git。
- 凡涉及真实资金的后续需求，必须先停止并解释风险；本项目不会通过当前界面打开真实交易。

手续费和滑点是可验证的简化模型，不代表真实交易所的完整成交规则。模拟结果不能保证实盘收益。

## Windows 从这里开始

按 [Windows 安装与运行指南](docs/windows.md) 安装 Python、下载代码、安装依赖并运行测试。命令不需要激活虚拟环境，也不需要修改 PowerShell 执行策略。

更简单的方式：下载 [quant-trading-windows.zip](https://github.com/66624935782y-commits/quant-trading/raw/refs/heads/main/downloads/quant-trading-windows.zip)，完整解压后双击 `setup_windows.cmd`，自动检测或安装官方 Python、安装项目依赖、运行测试并启动页面；以后双击 `start_windows.cmd`。缺少 WinGet 时按指南手动安装 Python。双击脚本经过代码审查，尚未在 Windows 实机运行。

在项目目录中，常用命令为：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m streamlit run app.py --browser.gatherUsageStats=false --server.address=127.0.0.1
```

最后一条命令会启动本机网页，通常会自动打开浏览器。终端需要保持开启；在终端按 `Ctrl+C` 可以停止服务。

## 分阶段开发

每个阶段只增加一组可验证功能，完成后运行相应测试。

| 阶段 | 实施内容 | 验收重点 |
| --- | --- | --- |
| 1：安全基础 | PAPER 配置、虚拟现货账户、离线网页、基础测试 | 手续费和滑点计算正确；限制资金与仓位；危险开关不能开启；模拟交易不调用真实订单接口 |
| 2：公共行情 | 读取现货与 USDT 本位合约的公开 K 线；pandas 清洗；Plotly 展示；保存原始数据 | 不需要密钥；时间戳与重复数据处理正确；网络失败能解释；行情可从本地重新读取 |
| 3：历史回测 | 简单策略、下一根 K 线成交、费用与滑点、资金管理、资金曲线与统计 | 防止未来数据泄漏；用已知样本核对收益和最大回撤；保存可复现的回测结果 |
| 4：持久化模拟交易 | 保存账户及交易流水，支持中断恢复与重复操作保护 | 恢复后的余额和持仓一致；记录完整；失败不产生半笔交易；仍不调用真实订单接口 |
| 5：适配器设计 | 设计交易所模拟盘与实盘接口边界、能力检查和安全开关 | 只形成设计与测试替身；真实资金功能保持关闭，不实现真实下单 |

详细分层与测试要求见 [项目架构](docs/architecture.md)。

第 1 阶段已在 Linux 云环境使用 Python 3.12 验证：49 项自动化测试全部通过，依赖一致性检查通过，Streamlit 服务启动与健康检查通过，重复安装脚本通过。测试包括实际页面虚拟买卖，以及禁止调用真实资金接口的测试替身。已配置 Windows/Linux 的 GitHub Actions，实际执行状态请查看仓库的 Actions 页面；Windows 实机尚未验证，需要按指南在自己的电脑运行测试。

## 文件说明

| 路径 | 用途 |
| --- | --- |
| `app.py` | Streamlit 网页入口 |
| `quant_trading/config.py` | PAPER 模式与安全配置 |
| `quant_trading/paper.py` | 纯 Python 虚拟账户与成交计算 |
| `tests/` | 自动化测试 |
| `pyproject.toml` | Python 版本要求和项目配置 |
| `requirements.lock` | 固定版本与文件哈希的依赖清单 |
| `docs/` | 架构说明与 Windows 指南 |

后续阶段会将原始行情和回测结果保存到被 Git 忽略的 `data/` 目录，并加入来源、时间范围、参数与数据校验信息。Git 忽略不等于备份；需要另外保存重要数据和结果。

开发者可在项目根目录运行 `python scripts/build_windows_zip.py`，重新生成 `downloads/quant-trading-windows.zip` 和对应 SHA256 文件。压缩包仅含项目源码、测试、文档和启动脚本，不含虚拟环境、行情、日志、密钥或下载目录本身。
