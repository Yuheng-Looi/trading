import time
from datetime import datetime
import os
import re
from dotenv import load_dotenv
import MetaTrader5 as mt5

load_dotenv()

# Re-login delay after detecting account switched away from live.
LIVE_RELOGIN_SECONDS = 60


def extract_cancel_threshold(comment):
    if not comment:
        return None

    match = re.search(r"\bCA\s*=\s*([\d.]+)", str(comment).upper())
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


# ---------- helpers ----------

def login_live():
    if not mt5.initialize():
        print("Initialiazation failed")
        return False

    login_str = os.getenv("LIVE_LOGIN", "")
    password = os.getenv("LIVE_PASS", "")
    server = os.getenv("LIVE_MT5_SERVER", "")

    if not login_str or not password or not server:
        print("❌ Missing LIVE credentials in .env file")
        return False

    try:
        login = int(login_str)
    except ValueError:
        print(f"❌ Invalid login ID: {login_str}")
        return False

    authorized = mt5.login(login, password=password, server=server)
    if authorized:
        print(f"--- Successfully connected to account {login} ---")
        return True

    print(f"Failed to connect: {mt5.last_error()}")
    return False


def login_demo():
    if not mt5.initialize():
        print("Initialiazation failed")
        return False

    login_str = os.getenv("DEMO_LOGIN", "")
    password = os.getenv("DEMO_PASS", "")
    server = os.getenv("DEMO_MT5_SERVER", "")

    if not login_str or not password or not server:
        print("❌ Missing DEMO credentials in .env file")
        return False

    try:
        login = int(login_str)
    except ValueError:
        print(f"❌ Invalid login ID: {login_str}")
        return False

    authorized = mt5.login(login, password=password, server=server)
    if authorized:
        print(f"--- Successfully connected to account {login} ---")
        return True

    print(f"Failed to connect: {mt5.last_error()}")
    return False


def check_limit_orders():
    print("--- Monitoring limit orders (Ctrl+C to stop) ---")
    live_login_str = os.getenv("DEMO_LOGIN", "").strip()
    try:
        live_login = int(live_login_str)
    except ValueError:
        print(f"❌ Invalid DEMO_LOGIN in .env: {live_login_str}")
        return

    relogin_due_ts = None

    while True:
        now_ts = time.time()

        account_info = mt5.account_info()
        current_login = getattr(account_info, "login", None) if account_info else None
        if current_login != live_login:
            if relogin_due_ts is None:
                relogin_due_ts = now_ts + LIVE_RELOGIN_SECONDS
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Detected non-live session "
                    f"(current={current_login}, expected={live_login}). "
                    f"Will re-login live in {LIVE_RELOGIN_SECONDS}s."
                )
            elif now_ts >= relogin_due_ts:
                if login_demo():
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Live account re-login succeeded")
                    relogin_due_ts = None
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Live account re-login failed; retry in 60s")
                    relogin_due_ts = now_ts + LIVE_RELOGIN_SECONDS

            # Never manage orders while connected to non-live account.
            time.sleep(1)
            continue

        if relogin_due_ts is not None:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Back on live account; resume monitoring")
            relogin_due_ts = None

        orders = mt5.orders_get()
        if orders is None:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Failed to fetch orders: {mt5.last_error()}")
            time.sleep(1)
            continue

        limit_orders = [
            order for order in orders
            if order.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT)
        ]

        for order in limit_orders:
            # Only manage bot-created orders. Manual orders usually have both magic=0 and empty comment.
            if int(order.magic or 0) == 0 and not str(order.comment or "").strip():
                continue

            tp = float(order.tp or 0.0)
            if tp <= 0:
                continue

            cancel_at = extract_cancel_threshold(order.comment) or tp

            tick = mt5.symbol_info_tick(order.symbol)
            if tick is None:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] No tick for {order.symbol}, skip order {order.ticket}")
                continue

            should_cancel = False
            current_price = None

            if order.type == mt5.ORDER_TYPE_BUY_LIMIT:
                current_price = tick.bid
                should_cancel = current_price >= cancel_at
            elif order.type == mt5.ORDER_TYPE_SELL_LIMIT:
                current_price = tick.ask
                should_cancel = current_price <= cancel_at

            if not should_cancel:
                continue

            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": order.ticket,
            }
            result = mt5.order_send(request)

            ts = datetime.now().strftime('%H:%M:%S')
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                print(
                    f"[{ts}] Cancelled order {order.ticket} ({order.symbol}) | "
                    f"type={order.type} price={current_price:.5f} tp={tp:.5f} cancel_at={cancel_at:.5f}"
                )
            else:
                print(
                    f"[{ts}] Failed to cancel order {order.ticket} ({order.symbol}) | "
                    f"ret={None if result is None else result.retcode} err={mt5.last_error()}"
                )

        time.sleep(1)

if __name__ == "__main__":
    if login_demo():  # Change to login_live() for live account
        try:
            check_limit_orders()
        except KeyboardInterrupt:
            print("\nStopped by user")
        finally:
            mt5.shutdown()