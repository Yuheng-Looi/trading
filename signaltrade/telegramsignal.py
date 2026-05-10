import asyncio
import json
import os
import re
import unicodedata
import time

import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

LOT = 0.01
MAGIC_TELEGRAM = 102040
CHANNEL_KEYWORD = "OMAR TRADER"
DIRECT_ENTRY_TP_DISTANCE = 2.0
LAYER_ENTRY_DISTANCE = 5.0
MONITOR_SECONDS = int(os.getenv("TELEGRAM_MONITOR_SECONDS", "3600"))
MONITOR_POLL_INTERVAL = float(os.getenv("TELEGRAM_MONITOR_POLL_INTERVAL", "2"))
MAX_LOSS_USD = 15.0  # Maximum loss when SL is hit
SL_PLUS_OFFSET_1 = 10.0  # First buy limit at SL + $10
SL_PLUS_OFFSET_2 = 5.0   # Second buy limit at SL + $5
TP2_PROFIT_OFFSET = 0.40  # Move SL to entry + $0.40 after TP2 hit


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
	print(f"Failed to connect: {mt5.last_error()}")
	return False

#  --------------------------above remains unchanged------------------------------------

def expand_shorthand_price(base_price_text, short_price_text):
	"""
	Expand shorthand pricing like 4780/74 -> 4780 and 4774.
	"""
	base_price = float(base_price_text)
	short = short_price_text.strip()

	if "." in short:
		return base_price, float(short)

	base_int = int(round(base_price))
	digits = len(short)
	prefix = str(base_int)[:-digits]

	if not prefix:
		expanded = float(short)
	else:
		expanded = float(prefix + short)

	return base_price, expanded


def calculate_lot_for_max_loss(sl_distance_pips, symbol_info, max_loss_usd=MAX_LOSS_USD):
	"""
	Calculate the lot size to ensure max loss is exactly max_loss_usd when SL is hit.
	Formula: lot = max_loss_usd / (sl_distance_pips * pip_value)
	"""
	if sl_distance_pips <= 0:
		return LOT
	
	# Pip value is typically 0.0001 for 4-digit quotes, 0.00001 for 5-digit
	pip_value = 0.01 if symbol_info.point == 0.0001 else 0.001
	
	lot = max_loss_usd / (sl_distance_pips * pip_value)
	return max(lot, 0.01)  # Minimum 0.01 lot


def calculate_dual_entry_prices(current_price, sl, action):
	"""
	Calculate the two entry prices for dual order strategy.
	For BUY:
	  - Order 1: if current_price - SL < 10, place at current_price; else at SL + 10
	  - Order 2: place at SL + 5
	For SELL: inverted logic
	"""
	if action == "BUY":
		price_distance = current_price - sl
		if price_distance < SL_PLUS_OFFSET_1:
			order1_price = current_price
		else:
			order1_price = sl + SL_PLUS_OFFSET_1
		order2_price = sl + SL_PLUS_OFFSET_2
	else:  # SELL
		price_distance = sl - current_price
		if price_distance < SL_PLUS_OFFSET_1:
			order1_price = current_price
		else:
			order1_price = sl - SL_PLUS_OFFSET_1
		order2_price = sl - SL_PLUS_OFFSET_2
	
	return order1_price, order2_price


def normalize_for_match(value):
	"""Normalize stylized unicode text so keyword matching is more reliable."""
	if value is None:
		return ""
	text = unicodedata.normalize("NFKC", str(value)).upper()
	return re.sub(r"[^A-Z0-9]+", "", text)


def parse_signal(text):
	"""
	Parse messages in the pattern:
	XAUUSD BUY 4780/74
	TP 4784
	TP 4788
	TP 4794
	TP 4798
	SL 4763.
	"""
	if not text:
		return None

	normalized = text.upper().strip()
	header_match = re.search(
		r"\b#?([A-Z]{3,12})\s+(BUY|SELL)\s+([\d.]+)(?:\s*/\s*([\d.]+))?",
		normalized,
	)
	if not header_match:
		return None

	symbol = header_match.group(1)
	action = header_match.group(2)
	zone_a_raw = header_match.group(3)
	zone_b_raw = header_match.group(4)

	if zone_b_raw:
		zone_a, zone_b = expand_shorthand_price(zone_a_raw, zone_b_raw)
	else:
		zone_a = float(zone_a_raw)
		zone_b = float(zone_a_raw)
	zone_low = min(zone_a, zone_b)
	zone_high = max(zone_a, zone_b)

	tp_matches = re.findall(r"\bTP\s*\.?\s*([\d.]+)", normalized)
	tps = [float(tp.rstrip(".")) for tp in tp_matches]
	if not tps:
		return None

	sl_match = re.search(r"\bSL\s*\.?\s*([\d.]+)", normalized)
	if not sl_match:
		return None
	sl = float(sl_match.group(1).rstrip("."))

	return {
		"symbol": f"{symbol}-P",
		"action": action,
		"zone_low": zone_low,
		"zone_high": zone_high,
		"sl": sl,
		"tp1": tps[0],
		"tp2": tps[1] if len(tps) > 1 else None,
		"tp3": tps[2] if len(tps) > 2 else None,
		"tp4": tps[3] if len(tps) > 3 else None,
		"take_profits": tps,
		"lot": LOT,
		"comment": "Telegram Signal",
	}


def is_autotrading_enabled():
	terminal_info = mt5.terminal_info()
	if terminal_info is None:
		print(f"Warning: Could not read terminal info: {mt5.last_error()}")
		return False

	trade_allowed = bool(getattr(terminal_info, "trade_allowed", True))
	tradeapi_disabled = bool(getattr(terminal_info, "tradeapi_disabled", False))

	account_info = mt5.account_info()
	if account_info is None:
		print(f"Warning: Could not read account info: {mt5.last_error()}")
		return False

	terminal_login = getattr(terminal_info, "login", None)
	account_trade_allowed = bool(getattr(account_info, "trade_allowed", True))

	print(
		"Autotrading check: "
		f"terminal_login={terminal_login}, "
		f"account_login={account_info.login}, "
		f"terminal_trade_allowed={trade_allowed}, "
		f"terminal_tradeapi_disabled={tradeapi_disabled}, "
		f"account_trade_allowed={account_trade_allowed}"
	)

	if not trade_allowed or tradeapi_disabled:
		return False

	return account_trade_allowed


def send_trade(signal_data):
	"""
	Implement dual-order constant loss strategy:
	- Place two buy/sell limit orders at calculated distances from SL
	- Monitor for TP2 hits and order activations
	- Manage positions according to scenario conditions
	"""
	if not signal_data:
		return

	symbol = signal_data["symbol"]
	action = signal_data["action"]
	sl = signal_data["sl"]
	tp1 = signal_data.get("tp1")
	tp2 = signal_data.get("tp2")
	tp3 = signal_data.get("tp3")
	lot = signal_data["lot"]
	comment = signal_data["comment"]

	if tp1 is None:
		print("Trade Skipped: TP1 is missing.")
		return

	if tp2 is None:
		print("Trade Skipped: TP2 is missing.")
		return

	if not mt5.symbol_select(symbol, True):
		print(f"Failed to select {symbol}")
		return

	tick = mt5.symbol_info_tick(symbol)
	if tick is None:
		print(f"Failed to get tick for {symbol}")
		return

	current_price = tick.bid if action == "SELL" else tick.ask
	current_price = float(current_price)

	if not is_autotrading_enabled():
		print("Trade Skipped: MT5 AutoTrading is disabled.")
		return

	symbol_info = mt5.symbol_info(symbol)
	if symbol_info is None:
		print(f"Failed to get symbol info for {symbol}")
		return

	# Calculate dual entry prices
	order1_price, order2_price = calculate_dual_entry_prices(current_price, sl, action)

	# Calculate lot size to ensure $15 max loss
	sl_distance_pips = abs(order1_price - sl) / symbol_info.point
	calculated_lot = calculate_lot_for_max_loss(sl_distance_pips, symbol_info)

	if not login_demo():
		print("Trade Skipped: Could not login to demo account.")
		return

	# Generate unique signal identifier based on timestamp
	signal_time = time.strftime('%H:%M:%S')
	signal_tag = f"TG SIG {signal_time}"

	# Place first buy/sell limit order at SL+10 (or current price), targeting TP2
	order1_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
	request1 = {
		"action": mt5.TRADE_ACTION_PENDING,
		"symbol": symbol,
		"volume": float(calculated_lot),
		"type": order1_type,
		"price": float(round(order1_price, symbol_info.digits)),
		"sl": float(sl),
		"tp": float(tp2),
		"deviation": 20,
		"magic": MAGIC_TELEGRAM,
		"comment": f"{signal_tag} ORDER1",
		"type_time": mt5.ORDER_TIME_GTC,
		"type_filling": mt5.ORDER_FILLING_RETURN,
	}

	# Place second buy/sell limit order at SL+5, targeting TP3
	request2 = {
		"action": mt5.TRADE_ACTION_PENDING,
		"symbol": symbol,
		"volume": float(calculated_lot),
		"type": order1_type,
		"price": float(round(order2_price, symbol_info.digits)),
		"sl": float(sl),
		"tp": float(tp3) if tp3 else float(tp2),
		"deviation": 20,
		"magic": MAGIC_TELEGRAM,
		"comment": f"{signal_tag} ORDER2",
		"type_time": mt5.ORDER_TIME_GTC,
		"type_filling": mt5.ORDER_FILLING_RETURN,
	}

	result1 = mt5.order_send(request1)
	if result1 is None or result1.retcode != mt5.TRADE_RETCODE_DONE:
		print(f"Order 1 failed: {result1.retcode if result1 else mt5.last_error()}")
		return

	ticket1 = result1.order
	print(f"Order 1 placed! Ticket: {ticket1} at {order1_price}")

	result2 = mt5.order_send(request2)
	if result2 is None or result2.retcode != mt5.TRADE_RETCODE_DONE:
		print(f"Order 2 failed: {result2.retcode if result2 else mt5.last_error()}")
		# Cancel order 1 since order 2 failed
		cancel_request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket1}
		mt5.order_send(cancel_request)
		return

	ticket2 = result2.order
	print(f"Order 2 placed! Ticket: {ticket2} at {order2_price}")

	# Monitor orders and market
	monitor_dual_orders(symbol, action, ticket1, ticket2, tp2, tp3, sl, order1_price, order2_price, signal_tag)


def monitor_dual_orders(symbol, action, ticket1, ticket2, tp2, tp3, sl, entry1, entry2, signal_tag):
	"""
	Monitor dual orders for:
	1. Both activated, TP2 hit: Order 1 closes by TP2, move Order 2 SL to entry+0.40 to secure profit and fight for TP3
	2. One activated, TP2 hit: Close filled order, cancel the pending SL+5 limit order (using signal_tag to identify)
	3. No activation, TP2 reached: Cancel both pending orders (no loss, no profit)
	4. SL hit with orders activated: Both close at SL = exactly $15 max loss
	"""
	start_ts = time.time()
	order1_filled = False
	order2_filled = False
	order1_closed = False
	order2_closed = False
	
	print(f"Monitoring dual orders: {ticket1} and {ticket2} for TP2={tp2}")

	while time.time() - start_ts <= MONITOR_SECONDS:
		tick = mt5.symbol_info_tick(symbol)
		if tick is None:
			time.sleep(MONITOR_POLL_INTERVAL)
			continue

		current_price = tick.bid if action == "SELL" else tick.ask
		current_price = float(current_price)

		# Check if TP2 was hit
		tp2_hit = False
		if action == "BUY" and current_price >= float(tp2):
			tp2_hit = True
		elif action == "SELL" and current_price <= float(tp2):
			tp2_hit = True

		# Check order states
		position1 = mt5.positions_get(ticket=ticket1)
		position2 = mt5.positions_get(ticket=ticket2)
		
		# Check if orders were filled (converted to positions)
		if position1 and len(position1) > 0:
			order1_filled = True
		if position2 and len(position2) > 0:
			order2_filled = True

		# Get pending orders to check if they still exist
		pending1 = mt5.orders_get(ticket=ticket1)
		pending2 = mt5.orders_get(ticket=ticket2)

		if tp2_hit:
			print(f"TP2 {tp2} was hit at price {current_price}")

			if order1_filled and order2_filled:
				# Condition 1: Both activated, TP2 hit
				print("Condition 1: Both orders filled, TP2 hit")
				# Order 1 should close by TP2 automatically (no need to manually close)
				# Modify Order 2's SL to entry + 0.40 to secure profit and fight for TP3
				if position2 and len(position2) > 0:
					symbol_info = mt5.symbol_info(symbol)
					# For BUY: SL = entry + 0.40; For SELL: SL = entry - 0.40
					if action == "BUY":
						new_sl = round(entry2 + TP2_PROFIT_OFFSET, symbol_info.digits)
					else:  # SELL
						new_sl = round(entry2 - TP2_PROFIT_OFFSET, symbol_info.digits)
					modify_position_sl(position2[0], ticket2, new_sl)
					print(f"Order 2 SL moved to {new_sl} to secure profit, now fighting for TP3 {tp3}")
				return

			elif order1_filled or order2_filled:
				# Condition 2: One filled, TP2 hit
				print("Condition 2: One order filled, TP2 hit")
				# The filled order closes by its TP automatically - just verify and log
				if order1_filled:
					print(f"Order 1 (ticket {ticket1}) closed by TP2")
					# Cancel the pending Order 2 using signal_tag to ensure correct order
					cancel_order_by_tag(symbol, signal_tag, "ORDER2")
				else:  # order2_filled
					print(f"Order 2 (ticket {ticket2}) closed by TP2")
					# Cancel the pending Order 1 using signal_tag to ensure correct order
					cancel_order_by_tag(symbol, signal_tag, "ORDER1")
				return

			else:
				# Condition 3: No activation, TP2 reached
				print("Condition 3: No orders activated, TP2 reached - no loss, no profit")
				# Cancel both pending orders using signal_tag
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
			print(f"Condition 4: SL {sl} hit at price {current_price} - max loss $15 achieved")
			# Orders should have hit SL automatically, verify positions exist
			if position1:
				print(f"Position 1 (ticket {ticket1}) closed by SL")
			if position2:
				print(f"Position 2 (ticket {ticket2}) closed by SL")
		# Cancel any remaining pending orders using signal_tag
		cancel_order_by_tag(symbol, signal_tag, "ORDER1")
		cancel_order_by_tag(symbol, signal_tag, "ORDER2")
def close_position(position, ticket):
	"""Close a position"""
	symbol = position.symbol
	volume = position.volume
	action = position.type
	
	# Reverse action: BUY position needs SELL to close
	close_type = mt5.ORDER_TYPE_SELL if action == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
	
	tick = mt5.symbol_info_tick(symbol)
	if tick is None:
		print(f"Failed to get tick for {symbol}")
		return False
	
	price = tick.ask if close_type == mt5.ORDER_TYPE_BUY else tick.bid
	
	request = {
		"action": mt5.TRADE_ACTION_DEAL,
		"symbol": symbol,
		"volume": volume,
		"type": close_type,
		"price": price,
		"deviation": 20,
		"magic": MAGIC_TELEGRAM,
		"comment": f"Close position {ticket}",
		"type_time": mt5.ORDER_TIME_GTC,
		"type_filling": mt5.ORDER_FILLING_IOC,
	}
	
	result = mt5.order_send(request)
	if result and result.retcode == mt5.TRADE_RETCODE_DONE:
		print(f"Position {ticket} closed successfully")
		return True
	else:
		print(f"Failed to close position {ticket}: {result.retcode if result else mt5.last_error()}")
		return False


def modify_position_sl(position, ticket, new_sl):
	"""Modify stop loss of a position"""
	request = {
		"action": mt5.TRADE_ACTION_SLTP,
		"position": ticket,
		"sl": new_sl,
		"tp": position.tp,
		"magic": MAGIC_TELEGRAM,
	}
	
	result = mt5.order_send(request)
	if result and result.retcode == mt5.TRADE_RETCODE_DONE:
		print(f"Position {ticket} SL modified to {new_sl}")
		return True
	else:
		print(f"Failed to modify SL for position {ticket}: {result.retcode if result else mt5.last_error()}")
		return False


def cancel_order(ticket):
	"""Cancel a pending order"""
	request = {
		"action": mt5.TRADE_ACTION_REMOVE,
		"order": ticket,
	}
	
	result = mt5.order_send(request)
	if result and result.retcode == mt5.TRADE_RETCODE_DONE:
		print(f"Order {ticket} cancelled successfully")
		return True
	else:
		print(f"Failed to cancel order {ticket}: {result.retcode if result else mt5.last_error()}")
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
		# Look for exact match: "TG SIG HH:MM:SS ORDER1" or "TG SIG HH:MM:SS ORDER2"
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


def channel_matches(chat, keyword):
	if chat is None:
		return False

	key = normalize_for_match(keyword)
	candidates = [
		getattr(chat, "title", "") or "",
		getattr(chat, "username", "") or "",
		getattr(chat, "first_name", "") or "",
		getattr(chat, "last_name", "") or "",
	]
	return any(key and key in normalize_for_match(candidate) for candidate in candidates if candidate)


def run_static_parse_test():
	sample = """XAUUSD SELL 4780/74

TP 4770
TP 4768
TP 4764
TP 4760

SL 4788."""

	parsed = parse_signal(sample)
	print("Static parse test output:")
	print(json.dumps(parsed, indent=2))

	expected = {
		"symbol": "XAUUSD-P",
		"action": "SELL",
		"zone_low": 4774.0,
		"zone_high": 4780.0,
		"sl": 4788.0,
		"tp1": 4770.0,
		"tp2": 4768.0,
		"tp3": 4764.0,
		"tp4": 4760.0,
	}

	ok = bool(parsed)
	for key, value in expected.items():
		if parsed.get(key) != value:
			ok = False
			print(f"Mismatch: {key} expected={value} got={parsed.get(key)}")

	print("Static parse test:", "PASS" if ok else "FAIL")
	return ok


async def monitor_signals_from_telegram():
	try:
		from telethon import TelegramClient, events
	except ImportError:
		print("telethon is not installed. Install with: pip install telethon")
		return

	api_id = os.getenv("TELEGRAM_API_ID", "").strip()
	api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
	session_name = os.getenv("TELEGRAM_SESSION", "telegram_signal_session").strip()
	channel_keyword = os.getenv("TELEGRAM_CHANNEL_KEYWORD", CHANNEL_KEYWORD).strip()
	chat_id_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
	chat_id = int(chat_id_raw) if chat_id_raw else None

	if not api_id or not api_hash:
		print("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH in .env")
		return

	if not login_demo():
		print("Could not login to demo account.")
		return

	client = TelegramClient(session_name, int(api_id), api_hash)

	@client.on(events.NewMessage(incoming=True))
	async def handler(event):
		chat = await event.get_chat()
		chat_ok = False
		if chat_id is not None and getattr(event, "chat_id", None) == chat_id:
			chat_ok = True
		elif channel_matches(chat, channel_keyword):
			chat_ok = True

		if not chat_ok:
			return

		body = (event.raw_text or "").strip()
		if not body:
			return

		signal_json = parse_signal(body)
		if not signal_json:
			return

		print("\nNEW SIGNAL RECEIVED (Telegram API):")
		print(f"Channel: {getattr(chat, 'title', '')}")
		print("Parsed Signal:", json.dumps(signal_json, indent=2))
		send_trade(signal_json)

	await client.start()
	print(f"Monitoring Telegram channels containing: '{channel_keyword}'")
	await client.run_until_disconnected()


if __name__ == "__main__":
	run_static_parse_test()
	try:
		asyncio.run(monitor_signals_from_telegram())
	except KeyboardInterrupt:
		print("Stopped by user")
