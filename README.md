<p align="middle">
  <a href="README.md">English</a> •
  <a href="README-CN.md">中文</a>
</p>

# Trading Project - Environment Setup

## 1) Install MT5 and Python

Download MT5 according to your OS (e.g. Windows) from:

https://www.metatrader5.com/en/download

Download and install Python (standalone installer):

https://www.python.org/ftp/python/3.13.14/python-3.13.14-amd64.exe

When running the installer, check the two boxes at bottom, then continue with the recommended installation.

## 2) Download the Project

Basic:
1. Click the **<> Code** dropdown on GitHub.
2. Download the ZIP file.
3. Extract (unzip) it.


Advance: 
1. Install git with repective OS (Win/Mac/Linux):
https://git-scm.com/install/

2. After install successfully, open terminal in desired folder and run: 
    ```bash
    git clone https://github.com/Yuheng-Looi/trading.git 
    ```

## 3) Create Virtual Environment

Open Command Prompt / Terminal in the project directory.

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

## 4) Configure Environment Variables

Open `.env` using Notepad or any text editor, then add:

```env
# Server Info
DEMO_MT5_SERVER=FortunePrime-Demo2
LIVE_MT5_SERVER=FortunePrime-Live2
DEMO_LOGIN=__YOUR_ACCOUNT1__,__YOUR_ACCOUNT2__
DEMO_PASS=__YOUR_PASS1__,__YOUR_PASS2__
TARGET_WA_GROUP=Zeus Trading VIP Signals
```

Replace account numbers and passwords with your real credentials.

## 5) Run the Script

Back in Command Prompt / Terminal:

```bash
cd signaltarde
python whatsappsignal.py
```
