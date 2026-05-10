import time
import MetaTrader5 as mt5
from dotenv import load_dotenv
import os
import re
from datetime import datetime, timedelta
import asyncio

from readsignal import iter_whatsapp_notifications

load_dotenv()

# Account routing placeholders (set manually before each run)
PLACE_LIVE = False
PLACE_DEMO = True

# Constant loss strategy: $9 max loss
MAX_LOSS_USD = 9.0        # Maximum loss when SL is hit
SL_PLUS_OFFSET_1 = 6.0    # First buy limit at SL + $6
SL_PLUS_OFFSET_2 = 3.0    # Second buy limit at SL + $3
SL_PROFIT_OFFSET = 0.40   # Move SL to entry + $0.40 after TP1 hit

LAYER_LOT = [0.01, 0.01]
LAYERING_GAP_PIPS = 300 # pip gap from each layer (kept for reference, not used in new logic)
DIRECT_TP1_MIN_DISTANCE = 20.0
MONITOR_SECONDS = int(os.getenv("WA_MONITOR_SECONDS", "300"))
MONITOR_POLL_INTERVAL = float(os.getenv("WA_MONITOR_POLL_INTERVAL", "1.0"))
BREAKEVEN_BUFFER = float(os.getenv("WA_BREAKEVEN_BUFFER", "0.40"))


def calculate_lot_for_max_loss(sl_distance_pips, symbol_info, max_loss_usd=MAX_LOSS_USD):
	"""
	Calculate the lot size to ensure max loss is exactly max_loss_usd when SL is hit.
	Formula: lot = max_loss_usd / (sl_distance_pips * pip_value)
	"""
	if sl_distance_pips <= 0:
		return 0.01
	
	# Pip value is typically 0.0001 for 4-digit quotes, 0.00001 for 5-digit
	pip_value = 0.01 if symbol_info.point == 0.0001 else 0.001
	
	lot = max_loss_usd / (sl_distance_pips * pip_value)
	return max(lot, 0.01)  # Minimum 0.01 lot


def calculate_dual_entry_prices(current_price, zone_low, zone_high, sl, action):
	"""
	Calculate the two entry prices for dual order strategy.
	For BUY:
	  - Order 1: if current_price within range, place at current; else at SL + 6
	  - Order 2: place at SL + 3
	For SELL: inverted logic
	"""
	zone_low = float(zone_low)
	zone_high = float(zone_high)
	sl = float(sl)
	current_price = float(current_price)
	
	if action == "BUY":
		# Check if price is within zone
		if zone_low <= current_price <= zone_high:
			order1_price = current_price
		else:
			order1_price = sl + SL_PLUS_OFFSET_1
		order2_price = sl + SL_PLUS_OFFSET_2
	else:  # SELL
		if zone_low <= current_price <= zone_high:
			order1_price = current_price
		else:
			order1_price = sl - SL_PLUS_OFFSET_1
		order2_price = sl - SL_PLUS_OFFSET_2
	
	return order1_price, order2_price


def get_pip_size(symbol_info):
    """Return the pip size for the current symbol so layer spacing stays consistent."""
    if symbol_info is None:
        return 0.0

    if symbol_info.digits in (3, 5):
        return symbol_info.point * 10

    return symbol_info.point


def build_layer_prices(action, anchor_price, sl, layer_count, digits, layer_gap):
    """
    Build up to layer_count prices with fixed spacing from anchor_price.

    BUY layers step lower than the anchor price, SELL layers step higher,
    while never crossing SL.
    """
    if layer_count <= 0:
        return []

    if layer_count == 1:
        return [round(anchor_price, digits)]

    prices = []
    for layer_index in range(layer_count):
        offset = layer_gap * layer_index
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
	Implement dual-order constant loss strategy with $9 max loss:
	- First order at current price (if within zone) or SL+6, targets TP1
	- Second order at SL+3, targets TP2
	- Monitor for TP1 hits and adjust second order SL or cancel if pending
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
	magic = 102030

	if tp1 is None:
		print("Trade Skipped: TP1 is missing.")
		return

	if tp2 is None:
		print("Trade Skipped: TP2 is missing.")
		return

	if not select_symbol_with_recovery(symbol):
		return

	tick = mt5.symbol_info_tick(symbol)
	if tick is None:
		print(f"Failed to get tick for {symbol}")
		return

	current_price = tick.bid if action == "SELL" else tick.ask
	current_price = float(current_price)

	# Sanity check: price within reasonable distance from zone
	distance_to_zone = min(abs(current_price - zone_low), abs(current_price - zone_high))
	if distance_to_zone > 10.0:
		print(f"Trade Skipped: Typo detected. Market price ({current_price}) is too far from signal zone ({zone_low}-{zone_high}).")
		return

	if not is_autotrading_enabled():
		print("Trade Skipped: MT5 AutoTrading is disabled.")
		return

	symbol_info = mt5.symbol_info(symbol)
	if symbol_info is None:
		print(f"Failed to get symbol info for {symbol}")
		return

	# Calculate dual entry prices
	order1_price, order2_price = calculate_dual_entry_prices(current_price, zone_low, zone_high, sl, action)

	# Calculate lot size to ensure $9 max loss
	sl_distance_pips = abs(order1_price - sl) / symbol_info.point
	calculated_lot = calculate_lot_for_max_loss(sl_distance_pips, symbol_info)

	# Generate unique signal identifier based on timestamp
	signal_time = time.strftime('%H:%M:%S')
	signal_tag = f"WA SIG {signal_time}"

	account_logins = []
	if PLACE_DEMO:
		account_logins.append(("demo", login_demo))
	if PLACE_LIVE:
		account_logins.append(("live", login_live))

	if not account_logins:
		print("Trade Skipped: both PLACE_DEMO and PLACE_LIVE are False.")
		return

	for account_name, login_fn in account_logins:
		if not login_fn():
			print(f"Trade Skipped on {account_name}: could not login.")
			continue

		# Place first buy/sell limit order, targeting TP1
		order1_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
		request1 = {
			"action": mt5.TRADE_ACTION_PENDING,
			"symbol": symbol,
			"volume": float(calculated_lot),
			"type": order1_type,
			"price": float(round(order1_price, symbol_info.digits)),
			"sl": float(sl),
			"tp": float(tp1),
			"deviation": 20,
			"magic": magic,
			"comment": f"{signal_tag} ORDER1",
			"type_time": mt5.ORDER_TIME_GTC,
			"type_filling": mt5.ORDER_FILLING_RETURN,
		}

		# Place second buy/sell limit order at SL±3, targeting TP2
		request2 = {
			"action": mt5.TRADE_ACTION_PENDING,
			"symbol": symbol,
			"volume": float(calculated_lot),
			"type": order1_type,
			"price": float(round(order2_price, symbol_info.digits)),
			"sl": float(sl),
			"tp": float(tp2),
			"deviation": 20,
			"magic": magic,
			"comment": f"{signal_tag} ORDER2",
			"type_time": mt5.ORDER_TIME_GTC,
			"type_filling": mt5.ORDER_FILLING_RETURN,
		}

		result1 = mt5.order_send(request1)
		if result1 is None or result1.retcode != mt5.TRADE_RETCODE_DONE:
			print(f"{account_name.upper()} Order 1 failed: {result1.retcode if result1 else mt5.last_error()}")
			continue

		ticket1 = result1.order
		print(f"{account_name.upper()} Order 1 placed! Ticket: {ticket1} at {order1_price}, TP1={tp1}")

		result2 = mt5.order_send(request2)
		if result2 is None or result2.retcode != mt5.TRADE_RETCODE_DONE:
			print(f"{account_name.upper()} Order 2 failed: {result2.retcode if result2 else mt5.last_error()}")
			# Cancel order 1 since order 2 failed
			cancel_request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket1}
			mt5.order_send(cancel_request)
			continue

		ticket2 = result2.order
		print(f"{account_name.upper()} Order 2 placed! Ticket: {ticket2} at {order2_price}, TP2={tp2}")

		# Monitor orders and market
		monitor_dual_wa_orders(symbol, action, ticket1, ticket2, tp1, tp2, sl, order1_price, order2_price, signal_tag, symbol_info, magic)


def monitor_dual_wa_orders(symbol, action, ticket1, ticket2, tp1, tp2, sl, entry1, entry2, signal_tag, symbol_info, magic):
	"""
	Monitor dual orders for:
	1. Both filled, TP1 hit: Order 1 closes by TP1, move Order 2 SL to entry+0.40 to secure profit and fight for TP2
	2. One filled, TP1 hit: Close filled order, cancel the pending SL+3 limit order
	3. No fill, TP1 reached: Cancel both pending orders (no loss, no profit)
	4. SL hit: Both close at SL = exactly $9 max loss
	"""
	start_ts = time.time()
	order1_filled = False
	order2_filled = False
	
	print(f"Monitoring dual orders: {ticket1} and {ticket2} for TP1={tp1}")

	while time.time() - start_ts <= MONITOR_SECONDS:
		tick = mt5.symbol_info_tick(symbol)
		if tick is None:
			time.sleep(MONITOR_POLL_INTERVAL)
			continue

		current_price = tick.bid if action == "SELL" else tick.ask
		current_price = float(current_price)

		# Check if TP1 was hit
		tp1_hit = False
		if action == "BUY" and current_price >= float(tp1):
			tp1_hit = True
		elif action == "SELL" and current_price <= float(tp1):
			tp1_hit = True

		# Check order states
		position1 = mt5.positions_get(ticket=ticket1)
		position2 = mt5.positions_get(ticket=ticket2)
		
		if position1 and len(position1) > 0:
			order1_filled = True
		if position2 and len(position2) > 0:
			order2_filled = True

		if tp1_hit:
			print(f"TP1 {tp1} was hit at price {current_price}")

			if order1_filled and order2_filled:
				# Condition 1: Both filled, TP1 hit
				print("Condition 1: Both orders filled, TP1 hit")
				# Order 1 should close by TP1 automatically
				# Modify Order 2's SL to entry + 0.40 to secure profit and fight for TP2
				if position2 and len(position2) > 0:
					# For BUY: SL = entry + 0.40; For SELL: SL = entry - 0.40
					if action == "BUY":
						new_sl = round(entry2 + SL_PROFIT_OFFSET, symbol_info.digits)
					else:  # SELL
						new_sl = round(entry2 - SL_PROFIT_OFFSET, symbol_info.digits)
					modify_position_sl(position2[0], ticket2, new_sl)
					print(f"Order 2 SL moved to {new_sl} to secure profit, now fighting for TP2 {tp2}")
				return

			elif order1_filled or order2_filled:
				# Condition 2: One filled, TP1 hit
				print("Condition 2: One order filled, TP1 hit")
				if order1_filled:
					print(f"Order 1 (ticket {ticket1}) closed by TP1")
					# Cancel the pending Order 2 using signal_tag
					cancel_order_by_tag(symbol, signal_tag, "ORDER2")
				else:
					print(f"Order 2 (ticket {ticket2}) closed by TP1")
					# Cancel the pending Order 1 using signal_tag
					cancel_order_by_tag(symbol, signal_tag, "ORDER1")
				return

			else:
				# Condition 3: No activation, TP1 reached
				print("Condition 3: No orders activated, TP1 reached - no loss, no profit")
				cancel_order_by_tag(symbol, signal_tag, "ORDER1")
				cancel_order_by_tag(symbol, signal_tag, "ORDER2")
				return

		# Check if SL was hit
		sl_hit = False
		if action == "BUY" and current_price <= sl:
			sl_hit = True
		elif action == "SELL" and current_price >= sl:
			sl_hit = True

		if sl_hit:
			print(f"Condition 4: SL {sl} hit at price {current_price} - max loss $9 achieved")
			if position1:
				print(f"Position 1 (ticket {ticket1}) closed by SL")
			if position2:
				print(f"Position 2 (ticket {ticket2}) closed by SL")
			# Cancel any remaining pending orders
			cancel_order_by_tag(symbol, signal_tag, "ORDER1")
			cancel_order_by_tag(symbol, signal_tag, "ORDER2")
			return

		time.sleep(MONITOR_POLL_INTERVAL)

	print("Monitor timeout: no TP1 or SL hit")


def modify_position_sl(position, ticket, new_sl):
	"""Modify stop loss of a position"""
	request = {
		"action": mt5.TRADE_ACTION_SLTP,
		"position": ticket,
		"sl": new_sl,
		"tp": position.tp,
		"magic": 102030,
	}
	
	result = mt5.order_send(request)
	if result and result.retcode == mt5.TRADE_RETCODE_DONE:
		print(f"Position {ticket} SL modified to {new_sl}")
		return True
	else:
		print(f"Failed to modify SL for position {ticket}: {result.retcode if result else mt5.last_error()}")
		return False


def cancel_order_by_tag(symbol, signal_tag, order_label):
	"""
	Cancel a pending order by searching for it using signal_tag and order_label.
	This ensures we only cancel the correct order associated with this signal.
	"""
	orders = mt5.orders_get(symbol=symbol)
	if orders is None:
		return False
	
	for order in orders:
		comment = str(order.comment or "").strip()
		# Look for exact match: "WA SIG HH:MM:SS ORDER1" or "WA SIG HH:MM:SS ORDER2"
		if signal_tag in comment and order_label in comment:
			request = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
			result = mt5.order_send(request)
			if result and result.retcode == mt5.TRADE_RETCODE_DONE:
				print(f"Order {order.ticket} ({order_label}) cancelled successfully")
				return True
			else:
				print(f"Failed to cancel order {order.ticket}: {result.retcode if result else mt5.last_error()}")
				return False
	
	# Order not found (might already be filled or cancelled)
	print(f"No pending order found for {signal_tag} {order_label}")
	return False


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