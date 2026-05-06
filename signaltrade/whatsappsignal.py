import time
import MetaTrader5 as mt5
from dotenv import load_dotenv
import os
import math
import re
from datetime import datetime, timedelta
import asyncio

from readsignal import iter_whatsapp_notifications

load_dotenv()

# Account routing placeholders (set manually before each run)
PLACE_LIVE = False
PLACE_DEMO = True

# Layering uses a fixed maximum of 3 entries: current entry + 2 deeper layers.
LAYER_LOT = [0.01, 0.01, 0.02]
LAYERING_GAP_PIPS = 30 # pip gap from each layer
DIRECT_TP1_MIN_DISTANCE = 2.0
MONITOR_SECONDS = int(os.getenv("WA_MONITOR_SECONDS", "300"))
MONITOR_POLL_INTERVAL = float(os.getenv("WA_MONITOR_POLL_INTERVAL", "2"))
BREAKEVEN_BUFFER = float(os.getenv("WA_BREAKEVEN_BUFFER", "0.40"))


def get_pip_size(symbol_info):
    """Return the pip size for the current symbol so layer spacing stays consistent."""
    if symbol_info is None:
        return 0.0

    if symbol_info.digits in (3, 5):
        return symbol_info.point * 10

    return symbol_info.point


def build_layer_prices(action, anchor_price, sl, layer_count, digits, point):
    """
    Build up to layer_count prices starting from anchor_price.

    BUY layers step lower than the anchor price, SELL layers step higher.
    The spacing is derived from the distance to SL so the deepest layer stays
    on the safe side of the stop loss.
    """
    if layer_count <= 0:
        return []

    if layer_count == 1:
        return [round(anchor_price, digits)]

    raw_step = abs(anchor_price - sl) / float(layer_count - 1)
    step = math.floor(raw_step)
    if step <= 0:
        step = point

    prices = []
    for layer_index in range(layer_count):
        offset = step * layer_index
        if action == "BUY":
            price = anchor_price - offset
            if price <= sl:
                break
        else:
            price = anchor_price + offset
            if price >= sl:
                break

        prices.append(round(price, digits))

    return prices

def login_live():
    if not mt5.initialize():
        print("Initialiazation failed")
        return False
    
    login = int(os.getenv("LIVE_LOGIN"))
    password = os.getenv("LIVE_PASS")
    server = os.getenv("LIVE_MT5_SERVER")

    authorized = mt5.login(login, password=password, server=server)

    if authorized:
        print(f"--- Successfully connected to account {login} ---")
        return True
    else:
        print(f"Failed to connect: {mt5.last_error()}")
        return False

def login_demo():

    if not mt5.initialize():
        print("Initialiazation failed")
        return False
    
    login = int(os.getenv("DEMO_LOGIN"))
    password = os.getenv("DEMO_PASS")
    server = os.getenv("DEMO_MT5_SERVER")

    authorized = mt5.login(login, password=password, server=server)

    if authorized:
        print(f"--- Successfully connected to account {login} ---")
        return True
    else:
        print(f"Failed to connect: {mt5.last_error()}")
        return False


def parse_signal(text):
    """
    Parses the trading signal text and extracts Symbol, Action, Zone, SL, TP1, and optional TP2.
    """
    text = text.upper()
    
    # Corrected logic: Skip if NEITHER "BUY" nor "SELL" are in the text
    if "BUY" not in text and "SELL" not in text:
        return None

    try:
        # 1. Extract Symbol and Action (e.g., XAUUSD SELL)
        # Looks for uppercase letters followed by BUY or SELL
        match_action = re.search(r'([A-Z]+)\s+(BUY|SELL)', text)
        if not match_action:
            return None
        symbol = match_action.group(1) + '-P'
        action = match_action.group(2)

        # 2. Extract Zone (e.g., Zone - 5183 - 5187)
        match_zone = re.search(r'ZONE\s*-\s*([\d.]+)\s*-\s*([\d.]+)', text)
        if not match_zone:
            return None
        zone_1 = float(match_zone.group(1))
        zone_2 = float(match_zone.group(2))
        
        # Ensure we know which is low and which is high regardless of signal format
        zone_low = min(zone_1, zone_2)
        zone_high = max(zone_1, zone_2)

        # 3. Extract Stop Loss (e.g., SL - 5190)
        match_sl = re.search(r'SL\s*-\s*([\d.]+)', text)
        if not match_sl:
            return None
        sl = float(match_sl.group(1))

        # 4. Extract Take Profit 1 (e.g., TP 1 - 5180)
        match_tp1 = re.search(r'TP\s*1\s*-\s*([\d.]+)', text)
        if not match_tp1:
            return None
        tp1 = float(match_tp1.group(1))

        # 5. Extract optional Take Profit 2 (e.g., TP 2 - 5177)
        match_tp2 = re.search(r'TP\s*2\s*-\s*([\d.]+)', text)
        tp2 = float(match_tp2.group(1)) if match_tp2 else None

        match_tp3 = re.search(r'TP\s*3\s*-\s*([\d.]+)', text)
        tp3 = float(match_tp3.group(1)) if match_tp3 else None

        # Compile and return the JSON/Dictionary
        return {
            "symbol": symbol,
            "action": action,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "comment": "WhatsApp Signal",
        }

    except Exception as e:
        print(f"Error parsing signal: {e}")
        return None


def select_symbol_with_recovery(symbol):
    """
    Try to select symbol on current session, then recover by re-login on configured accounts.
    """
    if mt5.symbol_select(symbol, True):
        return True

    print(f"Failed to select {symbol}. Trying to recover MT5 session/login and retry...")

    recovery_logins = []
    if PLACE_LIVE:
        recovery_logins.append(("live", login_live))
    if PLACE_DEMO:
        recovery_logins.append(("demo", login_demo))

    for account_name, login_fn in recovery_logins:
        if not login_fn():
            continue
        if mt5.symbol_select(symbol, True):
            print(f"Recovered symbol selection on {account_name} account for {symbol}.")
            return True

    # Extra debug hint for broker symbol naming mismatches.
    base = symbol.split("-")[0]
    candidates = mt5.symbols_get(f"*{base}*")
    candidate_names = [s.name for s in candidates[:10]] if candidates else []
    print(f"Still failed to select {symbol}. Similar symbols: {candidate_names}")
    return False


def send_trade(signal_data):
    """
    Evaluates current price against the signal zone, runs sanity checks for typos,
    and executes the MT5 order.
    """
    if not signal_data:
        return

    symbol = signal_data['symbol']
    action = signal_data['action']
    zone_low = signal_data['zone_low']
    zone_high = signal_data['zone_high']
    sl = signal_data['sl']
    tp1 = signal_data['tp1']
    tp2 = signal_data.get('tp2')
    tp3 = signal_data.get('tp3')
    comment = signal_data['comment']
    magic = 102030

    if not select_symbol_with_recovery(symbol):
        return

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"Failed to get tick for {symbol}")
        return

    # Set our maximum allowed distances (in absolute price terms for XAUUSD)
    MAX_SL_RISK = 10.0     # Max distance from entry to SL
    MAX_PRICE_GAP = 5.0   # Max distance from current price to the signal zone

    # Determine current market price based on action
    current_price = tick.bid if action == "SELL" else tick.ask

    # --- SANITY CHECK 1: Is the signal completely disconnected from reality? ---
    # If the closest part of the zone is more than 30 dollars away, it's a typo.
    distance_to_zone = min(abs(current_price - zone_low), abs(current_price - zone_high))
    if distance_to_zone > MAX_PRICE_GAP:
        print(f"Trade Skipped: Typo detected. Market price ({current_price}) is too far from signal zone ({zone_low}-{zone_high}).")
        return

    # Determine order parameters based on logic
    order_type = None
    planned_price = None
    
    if not is_autotrading_enabled():
        print("Trade Skipped: MT5 AutoTrading is disabled. Enable the Algo Trading button in MT5 and allow algo trading in terminal settings.")
        return

    # if is_recent_duplicate_order(symbol, action, magic, cooldown_seconds=300):
    #     print("Trade Skipped: Duplicate protection active (same symbol/action within 5 minutes).")
    #     return

    # --- ACTION LOGIC & SANITY CHECK 2 ---
    if action == "SELL":
        if zone_low <= current_price <= zone_high:
            order_type = mt5.ORDER_TYPE_SELL
            planned_price = current_price
            print(f"Price inside range. Planned Market SELL at {planned_price}")
            
        elif current_price < zone_low:
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
            planned_price = (zone_low+zone_high)/2  # Take the middle of zone - balance risk and reward
            print(f"Price dropped fast. Planned SELL LIMIT at {planned_price}")
            
        elif current_price > zone_high:
            print("Trade Skipped: Price went above entry zone (near SL). Signal invalid.")
            return

        # Check SL Risk for SELL
        if (sl - planned_price) > MAX_SL_RISK:
            print(f"Trade Skipped: SL is too wide. Risk is {sl - planned_price:.2f} (Max allowed: {MAX_SL_RISK}).")
            return

    elif action == "BUY":
        if zone_low <= current_price <= zone_high:
            order_type = mt5.ORDER_TYPE_BUY
            planned_price = current_price
            print(f"Price inside range. Planned Market BUY at {planned_price}")
            
        elif current_price > zone_high:
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
            planned_price = (zone_low+zone_high)/2  # Take the middle of zone - balance risk and reward
            print(f"Price spiked fast. Planned BUY LIMIT at {planned_price}")
            
        elif current_price < zone_low:
            print("Trade Skipped: Price went below entry zone (near SL). Signal invalid.")
            return

        # Check SL Risk for BUY
        if (planned_price - sl) > MAX_SL_RISK:
            print(f"Trade Skipped: SL is too wide. Risk is {planned_price - sl:.2f} (Max allowed: {MAX_SL_RISK}).")
            return

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"Failed to get symbol info for {symbol}")
        return

    layer_count = min(len(LAYER_LOT), 3)
    layer_prices = build_layer_prices(
        action=action,
        anchor_price=planned_price,
        sl=sl,
        layer_count=layer_count,
        digits=symbol_info.digits,
        point=symbol_info.point,
    )

    if not layer_prices:
        print("Trade Skipped: could not build any valid layer prices.")
        return

    pip_size = get_pip_size(symbol_info)
    if pip_size <= 0:
        print("Trade Skipped: could not determine pip size for layering.")
        return

    layer_gap = LAYERING_GAP_PIPS * pip_size

    # Build layer prices using fixed pip gap from planned entry and LAYER_LOT length.
    layer_prices = []
    for i in range(layer_count):
        if i == 0:
            layer_prices.append(round(planned_price, symbol_info.digits))
            continue
        if action == "BUY":
            price = planned_price - layer_gap * i
            if price <= sl:
                break
        else:
            price = planned_price + layer_gap * i
            if price >= sl:
                break
        layer_prices.append(round(price, symbol_info.digits))

    if not layer_prices:
        print("Trade Skipped: could not build any valid layer prices.")
        return

    # Assign TP: first order uses TP1, layered orders use TP2 (fallback to TP1 if TP2 missing)
    layer_tps = []
    for idx in range(len(layer_prices)):
        if idx == 0:
            layer_tps.append(tp1)
        else:
            layer_tps.append(tp2 if tp2 is not None else tp1)

    account_logins = []
    if PLACE_DEMO:
        account_logins.append(("demo", login_demo))
    if PLACE_LIVE:
        account_logins.append(("live", login_live))

    if not account_logins:
        print("Trade Skipped: both PLACE_DEMO and PLACE_LIVE are False.")
        return

    # Tag this signal so we can manage/cancel only same-signal orders
    signal_time = datetime.now().strftime('%H:%M:%S')
    comment_base = f"WA signal {signal_time}"

    for account_name, login_fn in account_logins:
        if not login_fn():
            print(f"Trade Skipped on {account_name}: could not login.")
            continue

        pending_tickets = []
        placed_positions = []

        for layer_index, layer_price in enumerate(layer_prices):
            # Determine lot: use LAYER_LOT when placing at different levels
            lot = LAYER_LOT[layer_index] if layer_index < len(LAYER_LOT) else LAYER_LOT[0]
            tp_target = layer_tps[layer_index] if layer_index < len(layer_tps) else tp1
            if tp_target is None:
                print(f"Skipping layer {layer_index + 1}: missing TP target.")
                continue
            if float(lot) <= 0:
                print(f"Skipping layer {layer_index + 1}: lot is {lot:.2f}.")
                continue
            # Decide limit vs market for first layer (first layer is immediate market)
            if layer_index == 0:
                layer_order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
                layer_action = mt5.TRADE_ACTION_DEAL
                type_filling = mt5.ORDER_FILLING_IOC
            else:
                layer_order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
                layer_action = mt5.TRADE_ACTION_PENDING
                type_filling = mt5.ORDER_FILLING_RETURN

            request = {
                "action": layer_action,
                "symbol": symbol,
                "volume": float(lot),
                "type": layer_order_type,
                "price": float(layer_price),
                "sl": float(sl),
                "tp": float(tp_target),
                "deviation": 20,
                "magic": magic,
                "comment": f"{comment_base} {account_name.upper()} L{layer_index + 1}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": type_filling,
            }

            result = mt5.order_send(request)
            if result is None:
                print(f"{account_name.upper()} L{layer_index + 1} order_send returned None: {mt5.last_error()}")
                continue

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"{account_name.upper()} L{layer_index + 1} order failed, retcode={result.retcode}")
                print(result._asdict())
                if result.retcode == 10027:
                    print("MT5 AutoTrading is disabled by client.")
            else:
                print(
                    f"{account_name.upper()} L{layer_index + 1} order placed! "
                    f"Ticket: {result.order}, lot={lot}, price={layer_price}, tp={tp_target}"
                )
                if layer_action == mt5.TRADE_ACTION_PENDING:
                    pending_tickets.append(result.order)
                else:
                    placed_positions.append(result.order)

        # If layering could not be built beyond the immediate layer (i.e. only first present)
        # but the signal's deeper layers would cross SL, place up to 2 orders at current price
        # using first LAYER_LOT value and aim TP1 and TP2 respectively.
        if len(layer_prices) == 1 and len(LAYER_LOT) > 0:
            # compute first and second market orders at current price
            fallback_lot = float(LAYER_LOT[0])
            # place first market order (TP1)
            m1_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": fallback_lot,
                "type": mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
                "price": float(planned_price),
                "sl": float(sl),
                "tp": float(tp1),
                "deviation": 20,
                "magic": magic,
                "comment": f"{comment_base} {account_name.upper()} FLAT1",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            r1 = mt5.order_send(m1_request)
            if r1 is not None and r1.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"{account_name.upper()} FLAT1 placed ticket={r1.order}")
                placed_positions.append(r1.order)
            else:
                print(f"{account_name.upper()} FLAT1 failed: {None if r1 is None else r1.retcode}")

            # place second market order (TP2)
            if tp2 is not None:
                m2_request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": fallback_lot,
                    "type": mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
                    "price": float(planned_price),
                    "sl": float(sl),
                    "tp": float(tp2),
                    "deviation": 20,
                    "magic": magic,
                    "comment": f"{comment_base} {account_name.upper()} FLAT2",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                r2 = mt5.order_send(m2_request)
                if r2 is not None and r2.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"{account_name.upper()} FLAT2 placed ticket={r2.order}")
                    placed_positions.append(r2.order)
                else:
                    print(f"{account_name.upper()} FLAT2 failed: {None if r2 is None else r2.retcode}")

        # Start a monitor loop for this signal to cancel pending same-signal limit orders
        # when TP1 is reached and to apply breakeven for TP2 position.
        monitor_start = time.time()
        while time.time() - monitor_start <= MONITOR_SECONDS:
            tick_now = mt5.symbol_info_tick(symbol)
            if tick_now is None:
                time.sleep(MONITOR_POLL_INTERVAL)
                continue
            cur_price = float(tick_now.bid if action == "SELL" else tick_now.ask)

            # If TP1 reached, cancel pending same-signal limit orders
            tp1_reached = (action == "BUY" and cur_price >= float(tp1)) or (action == "SELL" and cur_price <= float(tp1))
            if tp1_reached:
                # cancel pending orders with this comment_base
                orders = mt5.orders_get()
                if orders is not None:
                    for order in orders:
                        if str(order.comment or "").upper().startswith(comment_base.upper()):
                            # only cancel pending limits
                            if order.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT):
                                req = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
                                res = mt5.order_send(req)
                                print(f"Cancel attempt for order {order.ticket}: {None if res is None else res.retcode}")

                # Apply breakeven: find TP2 position and move its SL to entry+buffer
                positions = mt5.positions_get(symbol=symbol)
                if positions is not None:
                    for pos in positions:
                        if pos.magic == magic and str(pos.comment or "").upper().startswith(comment_base.upper()):
                            # identify TP2 position by its tp value
                            try:
                                pos_tp = float(getattr(pos, 'tp', 0.0) or 0.0)
                                pos_price = float(getattr(pos, 'price_open', getattr(pos, 'price', 0.0)))
                            except Exception:
                                continue
                            if tp2 is not None and abs(pos_tp - float(tp2)) < 0.0001:
                                # compute new SL
                                if action == "BUY":
                                    new_sl = pos_price + BREAKEVEN_BUFFER
                                else:
                                    new_sl = pos_price - BREAKEVEN_BUFFER
                                # try modifying position SL
                                try:
                                    modify_req = {"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "sl": float(new_sl), "tp": pos_tp}
                                    mod_res = mt5.order_send(modify_req)
                                    print(f"Breakeven modify res for pos {pos.ticket}: {None if mod_res is None else mod_res.retcode}")
                                except Exception as e:
                                    print(f"Breakeven modify failed: {e}")

                break

            time.sleep(MONITOR_POLL_INTERVAL)


def is_autotrading_enabled():
    """Return True only when terminal/account allow trading via Python API."""
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        print(f"Warning: Could not read terminal info: {mt5.last_error()}")
        return False

    trade_allowed = bool(getattr(terminal_info, "trade_allowed", True))
    tradeapi_disabled = bool(getattr(terminal_info, "tradeapi_disabled", False))
    if not trade_allowed or tradeapi_disabled:
        return False

    account_info = mt5.account_info()
    if account_info is None:
        print(f"Warning: Could not read account info: {mt5.last_error()}")
        return False

    account_trade_allowed = bool(getattr(account_info, "trade_allowed", True))
    return account_trade_allowed


def is_recent_duplicate_order(symbol, action, magic, cooldown_seconds=300):
    """
    Prevent duplicate orders without storing local state.
    Checks only MT5 order history and blocks if last matching order was placed recently.
    """
    now_ts = int(datetime.now().timestamp())
    from_time = datetime.now() - timedelta(seconds=cooldown_seconds)

    if action == "BUY":
        valid_types = {
            mt5.ORDER_TYPE_BUY,
            mt5.ORDER_TYPE_BUY_LIMIT,
            mt5.ORDER_TYPE_BUY_STOP,
            getattr(mt5, "ORDER_TYPE_BUY_STOP_LIMIT", -1),
        }
    else:
        valid_types = {
            mt5.ORDER_TYPE_SELL,
            mt5.ORDER_TYPE_SELL_LIMIT,
            mt5.ORDER_TYPE_SELL_STOP,
            getattr(mt5, "ORDER_TYPE_SELL_STOP_LIMIT", -1),
        }

    # 1) Check active pending orders first (catches immediate duplicates)
    active_orders = mt5.orders_get(symbol=symbol)
    if active_orders is not None:
        for order in active_orders:
            if order.magic == magic and order.type in valid_types:
                if (now_ts - int(order.time_setup)) < cooldown_seconds:
                    return True

    # 2) Check active positions (for market orders already opened)
    positions = mt5.positions_get(symbol=symbol)
    if positions is not None:
        for pos in positions:
            is_same_direction = (
                (action == "BUY" and pos.type == mt5.POSITION_TYPE_BUY) or
                (action == "SELL" and pos.type == mt5.POSITION_TYPE_SELL)
            )
            if pos.magic == magic and is_same_direction:
                if (now_ts - int(pos.time)) < cooldown_seconds:
                    return True

    # 3) Check recent order history as final fallback
    history_orders = mt5.history_orders_get(from_time, datetime.now())
    if history_orders is not None:
        for order in history_orders:
            if order.symbol == symbol and order.magic == magic and order.type in valid_types:
                if (now_ts - int(order.time_setup)) < cooldown_seconds:
                    return True

    # 4) Check recent deal history (executed trades)
    history_deals = mt5.history_deals_get(from_time, datetime.now())
    if history_deals is not None:
        valid_deal_types = {mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL}
        for deal in history_deals:
            if deal.symbol != symbol or deal.magic != magic or deal.type not in valid_deal_types:
                continue

            if action == "BUY" and deal.type != mt5.DEAL_TYPE_BUY:
                continue
            if action == "SELL" and deal.type != mt5.DEAL_TYPE_SELL:
                continue

            if (now_ts - int(deal.time)) < cooldown_seconds:
                return True

    return False

async def monitor_signals_from_notifications():
    target_group = os.getenv("TARGET_WA_GROUP", "").strip()

    if target_group:
        print(f"Monitoring WhatsApp group: '{target_group}'")
    else:
        print("TARGET_WA_GROUP is empty; all WhatsApp notifications will be considered.")

    while True:
        try:
            async for notification in iter_whatsapp_notifications(poll_interval=0.0):
                lines = notification.get("lines", [])
                if not lines:
                    continue

                group_name = lines[0].strip()
                if target_group and group_name != target_group:
                    continue

                # Keep signal format close to the previous pipeline.
                body = "\n".join(lines)

                print("\nNEW SIGNAL RECEIVED (WhatsApp notification):")
                print(f"Group: {group_name}")

                signal_json = parse_signal(body)
                print("Parsed Signal:", signal_json)

                send_trade(signal_json)

        except Exception as e:
            print(f"Notification read error: {e}")
            print("Retrying in 3 seconds...")
            await asyncio.sleep(3)
            if PLACE_DEMO:
                login_demo()
            if PLACE_LIVE:
                login_live()


try:
    if PLACE_DEMO:
        login_demo()
    if PLACE_LIVE:
        login_live()
    asyncio.run(monitor_signals_from_notifications())
except KeyboardInterrupt:
    print("Stopped by user")