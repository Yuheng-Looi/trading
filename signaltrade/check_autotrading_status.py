import os
import json
import MetaTrader5 as mt5

def load_all_accounts_from_json():
    json_path = os.path.join(os.path.dirname(__file__), "accounts.json")
    if not os.path.exists(json_path):
        print(f"Error: accounts.json not found at {json_path}")
        return []
    
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error parsing accounts.json: {e}")
        return []
        
    accounts = []
    for group_name, group_data in data.items():
        server = group_data.get("server", "").strip()
        for acc in group_data.get("accounts", []):
            name = acc.get("name", "").strip()
            account_id = acc.get("account_id", "").strip()
            password = acc.get("password", "").strip()
            lotsize_val = acc.get("lotsize", 0.0)
            
            try:
                lotsize = float(lotsize_val)
            except (ValueError, TypeError):
                lotsize = 0.0
                
            if not account_id:
                continue
            try:
                login = int(account_id)
            except ValueError:
                continue
                
            label = f"{group_name.upper()}-{name}-{login}"
            accounts.append({
                "label": label,
                "login": login,
                "password": password,
                "server": server,
                "lotsize": lotsize,
                "name": name,
                "group": group_name
            })
    return accounts

def check_account_autotrading(account):
    # Setup default info dict
    info = {
        "label": account["label"],
        "Account ID": str(account["login"]),
        "Name": account["name"],
        "Server": account["server"],
        "Lotsize": f"{account['lotsize']:.2f}",
        "Login Status": "FAILED",
        "Terminal trade_allowed": "",
        "Terminal tradeapi_disabled": "",
        "Account trade_allowed": "",
        "Autotrading verdict": ""
    }
    
    try:
        mt5.shutdown()
        # Initialize directly with credentials to avoid Terminal: Authorization failed (-6)
        initialized = mt5.initialize(
            login=account["login"],
            password=account["password"],
            server=account["server"]
        )
        if not initialized:
            info["Login Status"] = "INIT_FAILED"
            return info
            
        authorized = mt5.login(account["login"], password=account["password"], server=account["server"])
        if not authorized:
            mt5.shutdown()
            return info
            
        info["Login Status"] = "SUCCESS"
        
        account_info = mt5.account_info()
        terminal_info = mt5.terminal_info()
        
        if account_info is None or terminal_info is None:
            mt5.shutdown()
            return info
            
        terminal_trade_allowed = bool(getattr(terminal_info, "trade_allowed", True))
        terminal_tradeapi_disabled = bool(getattr(terminal_info, "tradeapi_disabled", False))
        account_trade_allowed = bool(getattr(account_info, "trade_allowed", True))
        
        autotrading_enabled = (
            terminal_trade_allowed
            and (not terminal_tradeapi_disabled)
            and account_trade_allowed
        )
        
        info["Terminal trade_allowed"] = str(terminal_trade_allowed)
        info["Terminal tradeapi_disabled"] = str(terminal_tradeapi_disabled)
        info["Account trade_allowed"] = str(account_trade_allowed)
        info["Autotrading verdict"] = "ENABLED" if autotrading_enabled else "DISABLED"
        
    except Exception as e:
        print(f"Exception checking account {account['label']}: {e}")
    finally:
        mt5.shutdown()
        
    return info

def print_transposed_table(accounts_data):
    row_keys = [
        "Account ID",
        "Name",
        "Server",
        "Lotsize",
        "Login Status",
        "Terminal trade_allowed",
        "Terminal tradeapi_disabled",
        "Account trade_allowed",
        "Autotrading verdict"
    ]
    
    headers = ["Account Name"] + [data["Name"] for data in accounts_data]
    
    # Calculate column widths
    col_widths = []
    max_attr_len = max(len(k) for k in row_keys + ["Account Name"])
    col_widths.append(max_attr_len)
    
    for data in accounts_data:
        max_val_len = len(data["Name"])
        for k in row_keys:
            val = data.get(k, "")
            max_val_len = max(max_val_len, len(val))
        col_widths.append(max_val_len)
        
    # Print headers
    header_str = " | ".join(f"{headers[i]:<{col_widths[i]}}" for i in range(len(headers)))
    border_line = "=" * len(header_str)
    
    print("\n" + border_line)
    print(header_str)
    print("-" * len(header_str))
    
    # Print rows
    for k in row_keys:
        row_cells = [k]
        for data in accounts_data:
            row_cells.append(data.get(k, ""))
        row_str = " | ".join(f"{row_cells[i]:<{col_widths[i]}}" for i in range(len(row_cells)))
        print(row_str)
        
    print(border_line + "\n")

def main():
    print("Loading accounts from accounts.json...")
    accounts = load_all_accounts_from_json()
    if not accounts:
        print("No accounts to check.")
        return
        
    print(f"Checking autotrading status for {len(accounts)} accounts...")
    results = []
    for acc in accounts:
        print(f"  Checking {acc['label']}...")
        res = check_account_autotrading(acc)
        results.append(res)
        
    print_transposed_table(results)

if __name__ == "__main__":
    main()
