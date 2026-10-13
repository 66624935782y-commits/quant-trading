# Windows 安装与运行指南

本指南使用 Windows 的 PowerShell。每次只复制代码框内的命令，不要复制说明文字。当前版本完全离线使用虚拟资金，不需要注册币安账户，也不需要 API 密钥。

## 推荐：双击安装，无需输入命令

1. 下载 [quant-trading-windows.zip](https://github.com/66624935782y-commits/quant-trading/raw/refs/heads/main/downloads/quant-trading-windows.zip)，右键选择 **全部解压缩**，进入含 `app.py` 的 `quant-trading` 文件夹。不要在 ZIP 内直接运行脚本。
2. 双击 **setup_windows.cmd**。它会检测 Python 3.12/3.13；缺少 Python 时，用 Windows 自带的 WinGet 安装官方 Python 3.12，然后安装项目依赖、检查依赖并运行测试。它仅为当前用户安装 Python，依赖放进项目的 `.venv`，并保留现有文件。
3. 全部检查通过后会自动启动 PAPER 页面并尝试打开浏览器。若浏览器未打开，使用窗口中显示的本地地址。保持命令窗口开启。
4. 以后启动只需双击 **start_windows.cmd**；按 `Ctrl+C` 或关闭运行窗口停止页面。若询问 `Terminate batch job (Y/N)?`，输入 `Y` 并按回车。

初次安装需要联网，可能要等待几分钟。脚本保留安装包校验、依赖哈希和 TLS 验证，不需要安装 Git、VS Code，也不需要激活脚本或修改 PowerShell 执行策略。PAPER 安全检查不通过时会停止，不会覆盖危险开关绕过检查。

若 Windows 没有 WinGet，脚本会暂停并提示按下方步骤安装官方 Python，再双击重新运行。遇到失败，保留窗口里的错误文字，尤其最后几行；不要删除哈希、关闭证书验证或删除已有 `.venv` 来强行继续。

双击脚本已经过代码审查，但当前云环境是 Linux，尚未在 Windows 电脑实际执行。下方手动命令仍可用于诊断或替代安装。

## 1. 安装 Python 3.12

1. 打开 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/)。
2. 选择 Python **3.12** 的官方 **Windows installer (64-bit)** 安装程序。项目要求 Python 版本不低于 3.12、低于 3.14；本指南统一使用 3.12。
3. 运行安装程序，勾选 **Add python.exe to PATH**，保留 Python Launcher 的安装选项，然后按安装程序提示完成安装。
4. 关闭已打开的终端，重新打开 PowerShell，运行：

```powershell
py -3.12 --version
```

看到 `Python 3.12.x` 即可继续。`x` 是小版本号，不需要与你看到的示例完全一致。

若提示找不到 `py`，先重新打开 PowerShell；仍失败时，重新运行官方安装程序，使用修复或修改选项检查 Python Launcher 是否已安装。不要从不明网站下载替代安装包。

## 2. 把项目下载到电脑

推荐下载上述 `quant-trading-windows.zip`，其中包含双击安装及启动脚本。也可以按下面的 GitHub ZIP 或 Git 克隆方式获取源码。如果仓库是私有的，下载前需在浏览器登录拥有该仓库访问权限的 GitHub 账号。

打开 [项目 GitHub 页面](https://github.com/66624935782y-commits/quant-trading)，点击 **Code → Download ZIP**。右键下载的 ZIP，选择 **全部解压缩**。

打开解压后的目录，直到可以看到 `app.py`、`requirements.lock` 和 `README.md`。这是项目目录；GitHub ZIP 常见目录名为 `quant-trading-main`，请以实际目录为准。

在资源管理器的地址栏复制该目录路径。打开 PowerShell，用下面的命令切换目录，把示例路径替换为自己的路径：

```powershell
Set-Location "C:\quant-trading"
```

路径含空格时也要保留双引号。运行以下命令确认目录正确：

```powershell
Get-ChildItem
```

应能看到 `app.py` 和 `requirements.lock`。如果只有一个子文件夹，再进入这个子文件夹。

若已经安装 Git，也可以使用下面的方式取得代码，之后进入克隆目录；下载 ZIP 与 Git 克隆只需要选择一种：

```powershell
git clone https://github.com/66624935782y-commits/quant-trading.git
Set-Location quant-trading
```

## 3. 创建项目专用 Python 环境

在项目目录运行：

```powershell
py -3.12 -m venv .venv
```

这会创建 `.venv` 文件夹，把项目依赖与电脑上其他 Python 项目隔离。命令执行成功时通常没有输出。

之后的命令直接使用这个文件夹内的 Python。**无需执行激活脚本，也无需修改 PowerShell 执行策略。** 不要把 `.venv` 上传到 Git。

## 4. 安装固定版本依赖

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
```

首次安装需要联网。锁文件固定依赖版本，并使用文件哈希校验安装包。安装完成后，离线示例页面不需要连接币安。

若出现连接超时，可以检查网络后重试同一条命令。若出现证书错误或哈希校验失败，请保留错误信息，排查网络或安装包来源；不要通过关闭证书检查、删除哈希或使用 `--trusted-host` 绕过验证。

## 5. 先运行自动化测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

输出最后一行应显示 `passed`，并且没有 `failed` 或 `error`。这是运行界面前的检查：验证虚拟账户计算和安全限制。如果失败，先处理失败原因，不要把它当成已经完成安装。

这些命令适用于 Windows；云环境中的测试结果不能替代在你电脑上实际执行这一步。

## 6. 启动模拟交易网页

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --browser.gatherUsageStats=false --server.address=127.0.0.1
```

浏览器通常会自动打开本机网页。若没有打开，可按终端显示的本地地址手动打开。保持 PowerShell 窗口开启，页面才能继续工作。命令将服务绑定到本机，并关闭 Streamlit 使用情况统计。

第一次使用时：

1. 确认页面模式是 **PAPER**，初始余额是 **10,000 USDT**。
2. 确认价格标注为离线示例，BTC/USDT 的示例价格为 **50,000**。
3. 尝试模拟买入少量 BTC，例如 `0.01` BTC，观察现金减少、持仓增加和手续费记录。
4. 再模拟卖出 `0.01` BTC，观察持仓归零。即使示例行情不变，手续费和滑点也会让最终余额低于初始余额。
5. 下载 JSON 账户快照，在电脑上另存一份，了解当前记录内容。

`USDT` 是这里用于记账的单位；所有余额都是虚拟数字，不对应你的交易所资产。页面没有任何真实下单、提现、转账或杠杆功能。

## 7. 停止与下次启动

在 PowerShell 中按 `Ctrl+C` 停止服务。下次只需打开 PowerShell、进入项目目录，再运行启动命令，不必重复创建环境和安装依赖。

当前账户只保存在 Streamlit 会话中。服务重启或开启新会话会重新获得初始虚拟资金；JSON 快照暂时不能导入恢复。重要练习记录请及时下载，持久化账户会在第 4 阶段实现。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 找不到 `app.py` 或 `requirements.lock` | 用 `Get-ChildItem` 检查当前目录，进入真正包含这些文件的项目目录 |
| 找不到 `.\.venv\Scripts\python.exe` | 在项目目录先执行第 3 步的创建环境命令 |
| 显示 `No module named streamlit` 或 `pytest` | 用第 4 步的命令安装依赖，确认没有换用系统 Python |
| 页面打不开 | 检查终端是否仍在运行、是否显示报错，再使用终端给出的本地地址 |
| 提示端口被占用 | 先关闭自己此前启动的 Streamlit；也可在启动命令最后追加 `--server.port=8502` |
| 模拟买入被拒绝 | 检查输入数量、剩余现金和仓位上限；不要通过修改安全配置绕过限制 |
| 重启后交易记录消失 | 当前版本尚未持久化账户，使用下载快照保留练习记录 |

完成这一步后，下一阶段会加入币安公开行情的只读获取、原始数据保存与 K 线图；仍使用 PAPER 模式，不需要交易所密钥。
