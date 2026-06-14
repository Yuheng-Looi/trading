<p align="middle">
  <a href="README.md">English</a> •
  <a href="README-CN.md">中文</a>
</p>

# 交易项目 - 环境设置

## 1) 安装 MT5 和 Python

根据您的操作系统下载并安装 MT5 平台：
https://www.metavar5.com/en/download （注意选择官方或您的经纪商对应的MT5版本）

下载并安装 Python（独立安装程序）：
https://www.python.org/ftp/python/3.13.14/python-3.13.14-amd64.exe

运行安装程序时，勾选底部的两个复选框（包括“Add Python to PATH”），然后继续推荐的安装。

## 2) 下载项目

### 基础版：
1. 下载 zip 压缩包：https://github.com/Yuheng-Looi/trading/archive/refs/heads/main.zip
2. 解压缩它。

### 进阶版：
1. 安装适用于您的操作系统的 Git：
   https://git-scm.com/install/
2. 在目标文件夹中打开终端并运行：
   ```bash
   git clone https://github.com/Yuheng-Looi/trading.git
   ```

## 3) 创建虚拟环境

在项目目录中打开命令提示符 / 终端。

### Windows
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
type nul > .env
```

### macOS / Linux
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
touch .env
```

## 4) 配置环境变量 (.env)

使用文本编辑器打开 `.env` 文件并添加 WhatsApp 消息过滤器及监控设置：

```env
TARGET_WA_GROUP=Zeus Trading VIP Signals
WA_BREAKEVEN_BUFFER=0.40
WA_MONITOR_SECONDS=3600
WA_MONITOR_POLL_INTERVAL=0.5
```

## 5) 在 accounts.json 中管理账户

该脚本不再从 `.env` 加载账户，而是直接从 `signaltrade/accounts.json` 读取登录信息。打开并编辑 `signaltrade/accounts.json`：

```json
{
    "live": {
        "server": "FortunePrime-Live2",
        "accounts": [
            {
                "name": "John",
                "account_id": "100003289",
                "password": "securepassword123",
                "lotsize": 0.00
            }
        ]
    },
    "demo": {
        "server": "FortunePrime-Demo2",
        "accounts": [
            {
                "name": "Yu Heng",
                "account_id": "100003289",
                "password": "BcI-6jRj",
                "lotsize": 0.01
            },
            {
                "name": "Timothy",
                "account_id": "100007541",
                "password": "*e1oEiSa",
                "lotsize": 0.02
            }
        ]
    }
}
```

### 配置参数说明：
* **`server`**: MT5 经纪商服务器名称 (例如 `FortunePrime-Demo2`)。
* **`name`**: 用于记录的额外元数据（不影响 MT5 登录）。
* **`account_id`**: 登录账户的账号。
* **`password`**: 授权该账户登录的密码。
* **`lotsize`**: 该账户交易的具体手数。将 `lotsize` 设置为 `0.00` 将禁用该账户的交易。

## 6) 运行脚本

在命令提示符 / 终端中：

```bash
cd signaltrade
python whatsappsignal.py
```
