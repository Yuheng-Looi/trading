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

When running the installer, check the two boxes at the bottom (including "Add Python to PATH"), then continue with the recommended installation.

## 2) Download the Project

### Basic:
1. Download zip: https://github.com/Yuheng-Looi/trading/archive/refs/heads/main.zip
2. Extract (unzip) it.

### Advanced:
1. Install Git for your OS:
   https://git-scm.com/install/
2. Open terminal in the desired folder and run:
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

## 4) Configure Environment Variables (.env)

Open the `.env` file in a text editor and add the WhatsApp notification filters:

```env
TARGET_WA_GROUP=Zeus Trading VIP Signals
WA_BREAKEVEN_BUFFER=0.40
WA_MONITOR_SECONDS=3600
WA_MONITOR_POLL_INTERVAL=0.5
```

## 5) Manage Accounts in accounts.json

Instead of loading accounts from `.env`, the script reads logins directly from `signaltrade/accounts.json`. Open `signaltrade/accounts.json` and customize it:

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

### Configuration Parameters:
* **`server`**: The MT5 broker server name (e.g. `FortunePrime-Demo2`).
* **`name`**: Metadata identifier for records (does not affect MT5 login).
* **`account_id`**: The login account number.
* **`password`**: The password to authorize the account.
* **`lotsize`**: The specific lot size for transactions. Setting `lotsize` to `0.00` disables trading for that account.

## 6) Run the Script

In the Command Prompt / Terminal:

```bash
cd signaltrade
python whatsappsignal.py
```
