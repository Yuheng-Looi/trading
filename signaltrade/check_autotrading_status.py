import os
import MetaTrader5 as mt5
from dotenv import load_dotenv


def get_bool_attr(obj, name, default=False):
    value = getattr(obj, name, default)
    return bool(value)


def check_autotrading_for_account(account_label, login_key, pass_key, server_key):
    login_raw = os.getenv(login_key, "").strip()
    password = os.getenv(pass_key, "").strip()
    server = os.getenv(server_key, "").strip()

    print(f"\n===== {account_label} =====")

    if not login_raw or not password or not server:
        print("Missing credentials in .env")
        return

    try:
        login = int(login_raw)
    except ValueError:
        print(f"Invalid login id: {login_raw}")
        return

    if not mt5.initialize():
        print(f"mt5.initialize() failed: {mt5.last_error()}")
        return

    authorized = mt5.login(login, password=password, server=server)
    print(f"Login success: {authorized}")

    if not authorized:
        print(f"mt5.login() failed: {mt5.last_error()}")
        mt5.shutdown()
        return

    account_info = mt5.account_info()
    terminal_info = mt5.terminal_info()

    if account_info is None:
        print(f"account_info() failed: {mt5.last_error()}")
        mt5.shutdown()
        return

    if terminal_info is None:
        print(f"terminal_info() failed: {mt5.last_error()}")
        mt5.shutdown()
        return

    terminal_trade_allowed = get_bool_attr(terminal_info, "trade_allowed", True)
    terminal_tradeapi_disabled = get_bool_attr(terminal_info, "tradeapi_disabled", False)
    account_trade_allowed = get_bool_attr(account_info, "trade_allowed", True)

    autotrading_enabled = (
        terminal_trade_allowed
        and (not terminal_tradeapi_disabled)
        and account_trade_allowed
    )

    print(f"Active login: {account_info.login}")
    print(f"Server: {account_info.server}")
    print(f"Terminal trade_allowed: {terminal_trade_allowed}")
    print(f"Terminal tradeapi_disabled: {terminal_tradeapi_disabled}")
    print(f"Account trade_allowed: {account_trade_allowed}")
    print(f"Autotrading verdict: {'ENABLED' if autotrading_enabled else 'DISABLED'}")

    mt5.shutdown()


def main():
    # .env expected at workspace root (one level above signaltrade)
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    check_autotrading_for_account("DEMO", "DEMO_LOGIN", "DEMO_PASS", "DEMO_MT5_SERVER")
    check_autotrading_for_account("LIVE", "LIVE_LOGIN", "LIVE_PASS", "LIVE_MT5_SERVER")


if __name__ == "__main__":
    main()
