import asyncio
import os
import re
import time
from datetime import datetime

import MetaTrader5 as mt5
from dotenv import load_dotenv

from readsignal import iter_whatsapp_notifications

load_dotenv()

MAGIC = 102030
LOT = 0.01
TP1_BREAKEVEN_BUFFER = float(os.getenv("WA_BREAKEVEN_BUFFER", "0.40"))
MONITOR_SECONDS = int(os.getenv("WA_MONITOR_SECONDS", "3600"))
MONITOR_POLL_INTERVAL = float(os.getenv("WA_MONITOR_POLL_INTERVAL", "0.5"))


def split_csv_env(value):
	if not value:
		return []
	return [item.strip() for item in value.split(",") if item.strip()]


def load_demo_accounts():
	login_values = split_csv_env(os.getenv("DEMO_LOGIN", ""))
	password_values = split_csv_env(os.getenv("DEMO_PASS", ""))
	server = os.getenv("DEMO_MT5_SERVER", "").strip()

	if not login_values:
		print("No DEMO_LOGIN accounts configured.")
		return []

	if not server:
		print("Missing DEMO_MT5_SERVER in .env.")
		return []

	accounts = []
	for index, login_value in enumerate(login_values, start=1):
		try:
			login = int(login_value)
		except ValueError:
			print(f"Skipping invalid DEMO_LOGIN entry: {login_value}")
			continue

		if len(password_values) == 1:
			password = password_values[0]
		elif index - 1 < len(password_values):
			password = password_values[index - 1]
		else:
			print(f"Skipping account {login}: missing matching password in DEMO_PASS.")
			continue

		accounts.append({
			"label": f"DEMO-{index}",
			"login": login,
			"password": password,
			"server": server,
		})

	return accounts


def login_account(account):
	mt5.shutdown()
	if not mt5.initialize():
		print(f"[{account['label']}] mt5.initialize failed: {mt5.last_error()}")
		return False

	authorized = mt5.login(account["login"], password=account["password"], server=account["server"])
	if not authorized:
		print(f"[{account['label']}] login failed for {account['login']}: {mt5.last_error()}")
		mt5.shutdown()
		return False

	print(f"[{account['label']}] connected to account {account['login']}")
	return True


def is_autotrading_enabled():
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

	return bool(getattr(account_info, "trade_allowed", True))


def parse_signal(text):
	if not text:
		return None

	normalized = text.upper()
	if "BUY" not in normalized and "SELL" not in normalized:
		return None

	match_action = re.search(r"([A-Z]+)\s+(BUY|SELL)", normalized)
	if not match_action:
		return None

	symbol = f"{match_action.group(1)}-P"
	action = match_action.group(2)

	match_range = re.search(r"(?:RANGE|ZONE)\s*-\s*([\d.]+)\s*-\s*([\d.]+)", normalized)
	if not match_range:
		return None

	range_a = float(match_range.group(1))
	range_b = float(match_range.group(2))
	range_low = min(range_a, range_b)
	range_high = max(range_a, range_b)

	match_sl = re.search(r"SL\s*-\s*([\d.]+)", normalized)
	if not match_sl:
		return None
	sl = float(match_sl.group(1))

	match_tp1 = re.search(r"TP\s*1\s*-\s*([\d.]+)", normalized)
	match_tp2 = re.search(r"TP\s*2\s*-\s*([\d.]+)", normalized)
	match_tp3 = re.search(r"TP\s*3\s*-\s*([\d.]+)", normalized)
	if not match_tp1 or not match_tp2 or not match_tp3:
		return None

	return {
		"symbol": symbol,
		"action": action,
		"range_low": range_low,
		"range_high": range_high,
		"sl": sl,
		"tp1": float(match_tp1.group(1)),
		"tp2": float(match_tp2.group(1)),
		"tp3": float(match_tp3.group(1)),
	}


def get_monitor_price(tick, action):
	return float(tick.ask if action == "BUY" else tick.bid)


def build_comment():
	return f"WA SIG {datetime.now().strftime('%H%M%S')}"


def signal_side_limit_type(action):
	return mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT


def signal_side_market_type(action):
	return mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL


def signal_is_live(account_label, symbol):
	if not is_autotrading_enabled():
		print(f"[{account_label}] autotrading is disabled.")
		return False

	if not mt5.symbol_select(symbol, True):
		print(f"[{account_label}] failed to select {symbol}: {mt5.last_error()}")
		return False

	return True


def send_order_request(account_label, request, description):
	result = mt5.order_send(request)
	if result is None:
		print(f"[{account_label}] {description} failed: {mt5.last_error()}")
		return None

	if result.retcode != mt5.TRADE_RETCODE_DONE:
		error = mt5.last_error()
		print(f"[{account_label}] {description} failed retcode={result.retcode} error={error}")
		return None

	print(f"[{account_label}] {description} placed ticket={result.order}")
	return result


def build_entry_plan(signal_data, current_price):
	action = signal_data["action"]
	range_low = signal_data["range_low"]
	range_high = signal_data["range_high"]
	sl = signal_data["sl"]

	if action == "BUY":
		if range_low <= current_price <= range_high:
			return [
				{"kind": "market", "price": current_price, "tp": signal_data["tp2"]},
				{"kind": "pending", "price": min(range_low, current_price - 4.0), "tp": signal_data["tp3"]},
			]
		if current_price > range_high:
			return [
				{"kind": "pending", "price": range_high, "tp": signal_data["tp2"]},
				{"kind": "pending", "price": range_low, "tp": signal_data["tp3"]},
			]
		if current_price > sl:
			return [
				{"kind": "market", "price": current_price, "tp": signal_data["tp2"]},
				{"kind": "market", "price": current_price, "tp": signal_data["tp3"]},
			]
		return None

	if range_low <= current_price <= range_high:
		return [
			{"kind": "market", "price": current_price, "tp": signal_data["tp2"]},
			{"kind": "pending", "price": max(range_high, current_price + 4.0), "tp": signal_data["tp3"]},
		]
	if current_price < range_low:
		return [
			{"kind": "pending", "price": range_low, "tp": signal_data["tp2"]},
			{"kind": "pending", "price": range_high, "tp": signal_data["tp3"]},
		]
	if current_price < sl:
		return [
			{"kind": "market", "price": current_price, "tp": signal_data["tp2"]},
			{"kind": "market", "price": current_price, "tp": signal_data["tp3"]},
		]
	return None


def place_signal_orders_for_account(account_label, signal_data, signal_tag, current_price):
	symbol = signal_data["symbol"]
	action = signal_data["action"]
	sl = signal_data["sl"]

	if not signal_is_live(account_label, symbol):
		return False

	symbol_info = mt5.symbol_info(symbol)
	if symbol_info is None:
		print(f"[{account_label}] failed to read symbol info for {symbol}: {mt5.last_error()}")
		return False

	local_tick = mt5.symbol_info_tick(symbol)
	if local_tick is None:
		print(f"[{account_label}] failed to read live tick for {symbol}: {mt5.last_error()}")
		return False
	local_market_price = get_monitor_price(local_tick, action)

	entry_plan = build_entry_plan(signal_data, current_price)
	if not entry_plan:
		print(f"[{account_label}] no entry plan available at price {current_price}")
		return False

	placed_any = False
	for index, entry in enumerate(entry_plan, start=1):
		is_market = entry["kind"] == "market"
		price = float(round(local_market_price if is_market else entry["price"], symbol_info.digits))
		tp = float(entry["tp"])

		if is_market:
			order_type = signal_side_market_type(action)
			action_type = mt5.TRADE_ACTION_DEAL
		else:
			order_type = signal_side_limit_type(action)
			action_type = mt5.TRADE_ACTION_PENDING

		request = {
			"action": action_type,
			"symbol": symbol,
			"volume": float(LOT),
			"type": order_type,
			"sl": float(sl),
			"tp": tp,
			"deviation": 20,
			"magic": MAGIC,
			"comment": signal_tag,
			"type_time": mt5.ORDER_TIME_GTC,
			"type_filling": mt5.ORDER_FILLING_RETURN,
		}

		# For pure market orders, price is typically ignored or set to 0 in MT5.
		if not is_market:
			request["price"] = price
		else:
			request["price"] = 0.0

		result = send_order_request(account_label, request, f"entry {index}")
		if result is None:
			if index > 1 and entry_plan[0]["kind"] == "pending":
				pending_orders = mt5.orders_get(symbol=symbol) or []
				for pending_order in pending_orders:
					if str(getattr(pending_order, "comment", "") or "") == signal_tag:
						mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": pending_order.ticket})
						print(f"[{account_label}] rolled back pending order {pending_order.ticket} after later entry failure")
			if placed_any:
				print(f"[{account_label}] partial fill detected for {signal_tag}; manual review may be needed.")
			return False

		placed_any = True

	return placed_any


def modify_signal_positions_for_account(account_label, signal_data, signal_tag):
	symbol = signal_data["symbol"]
	action = signal_data["action"]

	if not signal_is_live(account_label, symbol):
		return False

	positions = mt5.positions_get(symbol=symbol)
	if not positions:
		print(f"[{account_label}] no open positions found for {signal_tag}")
		return True

	modified_any = False
	for position in positions:
		comment = str(getattr(position, "comment", "") or "")
		if comment != signal_tag:
			continue

		entry_price = float(getattr(position, "price_open", 0.0) or 0.0)
		symbol_info = mt5.symbol_info(symbol)
		if symbol_info is None:
			print(f"[{account_label}] failed to read symbol info for {symbol}: {mt5.last_error()}")
			continue
		digits = symbol_info.digits
		if action == "BUY":
			new_sl = round(entry_price + TP1_BREAKEVEN_BUFFER, digits)
		else:
			new_sl = round(entry_price - TP1_BREAKEVEN_BUFFER, digits)

		request = {
			"action": mt5.TRADE_ACTION_SLTP,
			"position": position.ticket,
			"symbol": symbol,
			"sl": float(new_sl),
			"tp": float(getattr(position, "tp", 0.0) or 0.0),
			"magic": MAGIC,
		}
		result = mt5.order_send(request)
		if result and result.retcode == mt5.TRADE_RETCODE_DONE:
			print(f"[{account_label}] position {position.ticket} SL moved to {new_sl}")
			modified_any = True
		else:
			print(f"[{account_label}] failed to modify SL for position {position.ticket}: {result.retcode if result else mt5.last_error()}")

	return modified_any


def cancel_signal_pending_orders_for_account(account_label, signal_data, signal_tag):
	symbol = signal_data["symbol"]

	if not signal_is_live(account_label, symbol):
		return False

	orders = mt5.orders_get(symbol=symbol)
	if not orders:
		print(f"[{account_label}] no pending orders found for {signal_tag}")
		return True

	cancelled_any = False
	for order in orders:
		comment = str(getattr(order, "comment", "") or "")
		if comment != signal_tag:
			continue

		request = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
		result = mt5.order_send(request)
		if result and result.retcode == mt5.TRADE_RETCODE_DONE:
			print(f"[{account_label}] pending order {order.ticket} cancelled")
			cancelled_any = True
		else:
			print(f"[{account_label}] failed to cancel order {order.ticket}: {result.retcode if result else mt5.last_error()}")

	return cancelled_any


def get_current_mt5_login():
	account_info = mt5.account_info()
	if account_info is None:
		return None
	return getattr(account_info, "login", None)


def run_for_all_accounts(accounts, monitor_account, handler):
	current_login = get_current_mt5_login()
	for account in accounts:
		already_active = (
			monitor_account is not None
			and account["login"] == monitor_account["login"]
			and account["server"] == monitor_account["server"]
			and current_login == monitor_account["login"]
		)

		if already_active:
			print(f"[{account['label']}] already active monitor account; using current session")
			try:
				handler(account)
			finally:
				mt5.shutdown()
			continue

		if not login_account(account):
			print(f"[{account['label']}] skipped")
			continue
		try:
			handler(account)
		finally:
			mt5.shutdown()

	if monitor_account:
		return login_account(monitor_account)

	return False


def send_trade(signal_data, accounts, monitor_account):
	if not signal_data:
		return

	symbol = signal_data["symbol"]
	action = signal_data["action"]
	range_low = signal_data["range_low"]
	range_high = signal_data["range_high"]
	sl = signal_data["sl"]
	tp1 = signal_data["tp1"]

	if not symbol.startswith("XAUUSD"):
		print(f"Trade skipped: only XAUUSD signals are supported, got {symbol}.")
		return

	if action not in {"BUY", "SELL"}:
		print(f"Trade skipped: unsupported action {action}.")
		return

	if not mt5.symbol_select(symbol, True):
		print(f"Failed to select {symbol} on monitor account: {mt5.last_error()}")
		return

	tick = mt5.symbol_info_tick(symbol)
	if tick is None:
		print(f"Failed to read current price for {symbol}: {mt5.last_error()}")
		return

	current_price = get_monitor_price(tick, action)
	if action == "BUY" and current_price <= sl:
		print(f"Trade skipped: current price {current_price} is already at/below SL {sl}.")
		return
	if action == "SELL" and current_price >= sl:
		print(f"Trade skipped: current price {current_price} is already at/above SL {sl}.")
		return

	signal_tag = build_comment()
	print(f"Processing signal {signal_tag}: {symbol} {action} range={range_low}-{range_high} SL={sl} TP1={tp1}")

	placed_state = {"placed": False}

	def place_entries(account):
		placed = place_signal_orders_for_account(account["label"], signal_data, signal_tag, current_price)
		placed_state["placed"] = placed_state["placed"] or placed

	if not run_for_all_accounts(accounts, monitor_account, place_entries):
		print("Trade flow stopped: monitor account could not be restored after entry placement.")
		return

	if not placed_state["placed"]:
		print(f"No orders were placed for {signal_tag}.")
		return

	start_ts = time.time()
	while time.time() - start_ts <= MONITOR_SECONDS:
		tick = mt5.symbol_info_tick(symbol)
		if tick is None:
			time.sleep(MONITOR_POLL_INTERVAL)
			continue

		current_price = get_monitor_price(tick, action)
		tp1_hit = current_price >= tp1 if action == "BUY" else current_price <= tp1
		sl_hit = current_price <= sl if action == "BUY" else current_price >= sl

		if tp1_hit:
			print(f"TP1 reached at {current_price}; moving SL to breakeven buffer and cancelling pending orders.")

			def tp1_actions(account):
				modify_signal_positions_for_account(account["label"], signal_data, signal_tag)
				cancel_signal_pending_orders_for_account(account["label"], signal_data, signal_tag)

			if not run_for_all_accounts(accounts, monitor_account, tp1_actions):
				print("Post-TP1 account restoration failed.")
			return

		if sl_hit:
			print(f"SL reached at {current_price}; cancelling any remaining pending orders.")

			def sl_cleanup(account):
				cancel_signal_pending_orders_for_account(account["label"], signal_data, signal_tag)

			if not run_for_all_accounts(accounts, monitor_account, sl_cleanup):
				print("Post-SL account restoration failed.")
			return

		time.sleep(MONITOR_POLL_INTERVAL)

	print(f"Monitor timeout reached for {signal_tag}.")


async def monitor_signals_from_notifications(accounts, monitor_account):
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

				body = "\n".join(lines)
				print("\nNEW SIGNAL RECEIVED (WhatsApp notification):")
				print(f"Group: {group_name}")

				signal_json = parse_signal(body)
				print("Parsed Signal:", signal_json)

				send_trade(signal_json, accounts, monitor_account)

		except Exception as e:
			print(f"Notification read error: {e}")
			print("Retrying in 3 seconds...")
			await asyncio.sleep(3)
			if monitor_account:
				login_account(monitor_account)


def main():
	accounts = load_demo_accounts()
	if not accounts:
		return

	monitor_account = None
	for account in accounts:
		if login_account(account):
			monitor_account = account
			break

	if monitor_account is None:
		print("No demo account could be logged in for monitoring.")
		return

	try:
		asyncio.run(monitor_signals_from_notifications(accounts, monitor_account))
	except KeyboardInterrupt:
		print("Stopped by user")
	finally:
		mt5.shutdown()


if __name__ == "__main__":
	main()