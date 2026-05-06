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
		print("Trade Skipped: TP1 is missing. Telegram strategy requires TP1 for direct-entry timing.")
		return

	if tp2 is None:
		print("Trade Skipped: TP2 is missing. Telegram strategy requires TP2 for the main entry.")
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

	def direct_entry_allowed():
		distance_to_tp1 = tp1 - current_price if action == "BUY" else current_price - tp1
		return distance_to_tp1 >= 0 and distance_to_tp1 <= DIRECT_ENTRY_TP_DISTANCE

	def build_layer_price():
		if action == "BUY":
			layer_price = current_price - LAYER_ENTRY_DISTANCE
			if layer_price <= sl:
				return None
			return round(layer_price, symbol_info.digits)

		layer_price = current_price + LAYER_ENTRY_DISTANCE
		if layer_price >= sl:
			return None
		return round(layer_price, symbol_info.digits)

	if not is_autotrading_enabled():
		print(
			"Trade Skipped: MT5 AutoTrading is disabled. "
			"Enable Algo Trading button and terminal setting."
		)
		return

	order_type = None
	planned_price = None
	tp_target = None
	trade_action = None
	type_filling = None
	comment_suffix = ""

	symbol_info = mt5.symbol_info(symbol)
	if symbol_info is None:
		print(f"Failed to get symbol info for {symbol}")
		return

	if direct_entry_allowed():
		# If signal arrives when price is already reaching TP1, do NOT enter immediately.
		# Instead monitor price: when it returns to the zone (within DIRECT_ENTRY_TP_DISTANCE
		# of TP1) and TP2 has not been hit since the signal, enter using the same logic.
		print("Signal arrived while price is near TP1 — will monitor for re-entry instead of immediate order.")

		start_ts = time.time()
		while time.time() - start_ts <= MONITOR_SECONDS:
			tick_now = mt5.symbol_info_tick(symbol)
			if tick_now is None:
				print("Monitor: failed to fetch tick, retrying")
				time.sleep(MONITOR_POLL_INTERVAL)
				continue

			cur_price = float(tick_now.bid if action == "SELL" else tick_now.ask)

			# If TP2 already reached after signal -> abandon
			if action == "BUY" and cur_price >= float(tp2):
				print(f"Monitor: TP2 {tp2} was reached ({cur_price}); no entry.")
				return
			if action == "SELL" and cur_price <= float(tp2):
				print(f"Monitor: TP2 {tp2} was reached ({cur_price}); no entry.")
				return

			# If price returned into the allowed entry window -> place order
			distance_to_tp1 = tp1 - cur_price if action == "BUY" else cur_price - tp1
			if distance_to_tp1 >= 0 and distance_to_tp1 <= DIRECT_ENTRY_TP_DISTANCE:
				print(f"Monitor: price returned to zone ({cur_price}). Placing entry.")
				# set up for immediate market entry to TP2
				order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
				planned_price = round(cur_price, symbol_info.digits)
				tp_target = float(tp2)
				trade_action = mt5.TRADE_ACTION_DEAL
				type_filling = mt5.ORDER_FILLING_IOC
				comment_suffix = " DIRECT TP2 (MONITORED)"
				break

			time.sleep(MONITOR_POLL_INTERVAL)

		else:
			print("Monitor timeout: no re-entry; abandoning signal.")
			return
	else:
		planned_layer_price = build_layer_price()
		if planned_layer_price is not None:
			if tp3 is None:
				print("Trade Skipped: TP3 is missing. Telegram strategy requires TP3 for the layered entry.")
				return
			order_type = mt5.ORDER_TYPE_SELL_LIMIT if action == "SELL" else mt5.ORDER_TYPE_BUY_LIMIT
			planned_price = planned_layer_price
			tp_target = float(tp3)
			trade_action = mt5.TRADE_ACTION_PENDING
			type_filling = mt5.ORDER_FILLING_RETURN
			comment_suffix = f" LAYER CA={tp2}"
			print(
				f"Layering order planned at {planned_price} with TP3 {tp_target}; "
				f"will cancel if price reaches TP2 {tp2} first"
			)
		else:
			if tp3 is None:
				print("Trade Skipped: TP3 is missing. Telegram strategy requires TP3 for the layered fallback.")
				return
			order_type = mt5.ORDER_TYPE_SELL if action == "SELL" else mt5.ORDER_TYPE_BUY
			planned_price = round(current_price, symbol_info.digits)
			tp_target = float(tp3)
			trade_action = mt5.TRADE_ACTION_DEAL
			type_filling = mt5.ORDER_FILLING_IOC
			comment_suffix = " LAYER TP3"
			print(
				f"Layering fallback: market {action} at {planned_price} with TP3 {tp_target} "
				f"because a 5-dollar layered limit would cross SL"
			)

	if not login_demo():
		print("Trade Skipped: Could not login to demo account.")
		return

	request = {
		"action": trade_action,
		"symbol": symbol,
		"volume": float(lot),
		"type": order_type,
		"price": float(planned_price),
		"sl": float(sl),
		"tp": float(tp_target),
		"deviation": 20,
		"magic": MAGIC_TELEGRAM,
		"comment": f"{comment}{comment_suffix}",
		"type_time": mt5.ORDER_TIME_GTC,
		"type_filling": type_filling,
	}

	def _send_request(req):
		res = mt5.order_send(req)
		if res is None:
			print(f"Order send returned None: {mt5.last_error()}")
			return None
		if res.retcode != mt5.TRADE_RETCODE_DONE:
			print(f"Order failed, retcode={res.retcode}")
			print(res._asdict())
			if res.retcode == 10027:
				print("MT5 AutoTrading is disabled by client.")
			return res
		print(f"Order successfully placed! Ticket: {res.order}")
		return res

	result = _send_request(request)


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
