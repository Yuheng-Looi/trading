# Trading Project - Environment Setup

## 1) Install Python

Download and install Python (standalone installer):

https://www.python.org/ftp/python/3.14.5/python-3.14.5-amd64.exe

When running the installer, check the two boxes at bottom, then continue with the recommended installation.

## 2) Download the Project

1. Click the **<> Code** dropdown on GitHub.
2. Download the ZIP file.
3. Extract (unzip) it.

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
