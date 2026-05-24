# 交易项目 - 环境设置

## 1) 安装 Python

根据电脑操作系统（OS）的种类，下载MT5平台：

https://www.metatrader5.com/en/download

下载并安装 Python（独立安装程序）：

https://www.python.org/ftp/python/3.14.5/python-3.14.5-amd64.exe

运行安装程序时，勾选底部两个复选框，然后继续推荐安装。

## 2) 下载项目

1. 点击 GitHub 上的 **<> Code** 下拉菜单。
2. 下载 ZIP 文件。
3. 解压（unzip）。

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

## 4) 配置环境变量

使用记事本或任何文本编辑器打开 `.env`，然后添加：

```env
# 服务器信息
DEMO_MT5_SERVER=FortunePrime-Demo2
LIVE_MT5_SERVER=FortunePrime-Live2
DEMO_LOGIN=__YOUR_ACCOUNT1__,__YOUR_ACCOUNT2__
DEMO_PASS=__YOUR_PASS1__,__YOUR_PASS2__
TARGET_WA_GROUP=Zeus Trading VIP Signals
```

将账户号和密码替换为您的真实凭证。

## 5) 运行脚本

回到命令提示符 / 终端：

```bash
cd signaltarde
python whatsappsignal.py
```
