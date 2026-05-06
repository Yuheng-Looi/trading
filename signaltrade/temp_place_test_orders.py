import os
from datetime import datetime

import MetaTrader5 as mt5
from dotenv import load_dotenv


# Temporary test toggles
PLACE_DEMO = False
PLACE_LIVE = True

# Order setup
SYMBOL = "XAUUSD-P"
ORDER_KIND = "BUY_LIMIT"  # BUY_LIMIT or SELL_LIMIT
LOT = 0.01
DISTANCE_ABS = 100.0  # Price distance away from market so order stays pending
MAGIC = 909090
COMMENT = "TEMP-CROSS-ACCOUNT-TEST"


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(env_path)


def login_account(login_key, pass_key, server_key):
    login_raw = os.getenv(login_key, "").strip()
    password = os.getenv(pass_key, "").strip()
    server = os.getenv(server_key, "").strip()

    if not login_raw or not password or not server:
        print(f"Missing credentials: {login_key}, {pass_key}, {server_key}")
        return False, None

    try:
        login = int(login_raw)
    except ValueError:
        print(f"Invalid login id in {login_key}: {login_raw}")
        return False, None

    if not mt5.initialize():
        print(f"mt5.initialize failed: {mt5.last_error()}")
        return False, None

    ok = mt5.login(login, password=password, server=server)
    if not ok:
        print(f"mt5.login failed for {login}: {mt5.last_error()}")
        mt5.shutdown()
        return False, None

    return True, login


def build_price(order_kind, bid, ask, digits):
    if order_kind == "BUY_LIMIT":
        raw = bid - DISTANCE_ABS
        return round(raw, digits), mt5.ORDER_TYPE_BUY_LIMIT
    if order_kind == "SELL_LIMIT":
        raw = ask + DISTANCE_ABS
        return round(raw, digits), mt5.ORDER_TYPE_SELL_LIMIT

    raise ValueError("ORDER_KIND must be BUY_LIMIT or SELL_LIMIT")


def place_test_limit_for_logged_in_account(account_label):
    if not mt5.symbol_select(SYMBOL, True):
        print(f"[{account_label}] symbol_select failed for {SYMBOL}: {mt5.last_error()}")
        return

    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        print(f"[{account_label}] symbol_info failed for {SYMBOL}: {mt5.last_error()}")
        return

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print(f"[{account_label}] symbol_info_tick failed for {SYMBOL}: {mt5.last_error()}")
        return

    price, order_type = build_price(ORDER_KIND, tick.bid, tick.ask, symbol_info.digits)

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": SYMBOL,
        "volume": float(LOT),
        "type": order_type,
        "price": float(price),
        "sl": 0.0,
        "tp": 0.0,
        "deviation": 20,
        "magic": MAGIC,
        "comment": COMMENT,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

    result = mt5.order_send(request)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if result is None:
        print(f"[{ts}] [{account_label}] order_send returned None: {mt5.last_error()}")
        return

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[{ts}] [{account_label}] order failed retcode={result.retcode}")
        print(result._asdict())
        return

    print(
        f"[{ts}] [{account_label}] order placed | "
        f"ticket={result.order} symbol={SYMBOL} type={ORDER_KIND} lot={LOT} price={price}"
    )


def run_for_account(account_label, login_key, pass_key, server_key):
    ok, login = login_account(login_key, pass_key, server_key)
    if not ok:
        return

    account_info = mt5.account_info()
    terminal_info = mt5.terminal_info()
    print(f"\n===== {account_label} =====")
    print(f"Logged in target account: {login}")
    print(f"Active account login: {getattr(account_info, 'login', None)}")
    print(f"Active server: {getattr(account_info, 'server', None)}")
    print(f"Terminal trade_allowed: {bool(getattr(terminal_info, 'trade_allowed', True))}")
    print(f"Terminal tradeapi_disabled: {bool(getattr(terminal_info, 'tradeapi_disabled', False))}")
    print(f"Account trade_allowed: {bool(getattr(account_info, 'trade_allowed', True))}")

    place_test_limit_for_logged_in_account(account_label)
    mt5.shutdown()


def main():
    load_env()

    if PLACE_DEMO:
        run_for_account("DEMO", "DEMO_LOGIN", "DEMO_PASS", "DEMO_MT5_SERVER")

    if PLACE_LIVE:
        run_for_account("LIVE", "LIVE_LOGIN", "LIVE_PASS", "LIVE_MT5_SERVER")


if __name__ == "__main__":
    main()
