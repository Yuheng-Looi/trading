import asyncio
import json
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
TP1_BREAKEVEN_BUFFER = float(os.getenv("WA_BREAKEVEN_BUFFER", "1.00"))
MONITOR_SECONDS = int(os.getenv("WA_MONITOR_SECONDS", "3600"))
MONITOR_POLL_INTERVAL = float(os.getenv("WA_MONITOR_POLL_INTERVAL", "0.5"))


def log_event(message, level="INFO", detail=None):
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
	log_line = f"[{timestamp}] [{level}] {message}\n"
	
	log_dir = os.path.dirname(__file__)
	full_log_path = os.path.join(log_dir, "full_event.log")
	try:
		with open(full_log_path, "a", encoding="utf-8") as f:
			f.write(log_line)
	except Exception as e:
		print(f"Failed to write to full_event.log: {e}")
		
	print(log_line.strip())
	
	if level == "ERROR":
		error_log_path = os.path.join(log_dir, "error.log")
		error_line = log_line
		if detail:
			error_line += f"Detail: {detail}\n"
		error_line += "-" * 50 + "\n"
		try:
			with open(error_log_path, "a", encoding="utf-8") as f:
				f.write(error_line)
		except Exception as e:
			print(f"Failed to write to error.log: {e}")

def log_info(message):
	log_event(message, level="INFO")

def log_error(message, detail=None):
	log_event(message, level="ERROR", detail=detail)


def split_csv_env(value):
	if not value:
		return []
	return [item.strip() for item in value.split(",") if item.strip()]


def load_accounts_from_json():
	json_path = os.path.join(os.path.dirname(__file__), "accounts.json")
	if not os.path.exists(json_path):
		log_error(f"Error: accounts.json not found at {json_path}")
		return []

	log_info(f"Loading accounts from: {json_path}")
	try:
		with open(json_path, "r", encoding="utf-8") as f:
			data = json.load(f)
	except Exception as e:
		log_error(f"Error parsing accounts.json: {e}", detail=str(e))
		return []

	accounts = []
	log_info("\n================ ACCOUNTS LOADED FROM JSON ================")
	for group_name, group_data in data.items():
		server = group_data.get("server", "").strip()
		group_accounts = group_data.get("accounts", [])
		log_info(f"Group: {group_name} | Server: {server}")
		for index, acc in enumerate(group_accounts, start=1):
			name = acc.get("name", "").strip()
			account_id = acc.get("account_id", "").strip()
			password = acc.get("password", "").strip()
			lotsize_val = acc.get("lotsize", 0.0)

			try:
				lotsize = float(lotsize_val)
			except (ValueError, TypeError):
				lotsize = 0.0

			log_info(f"  Account #{index}: Name='{name}', ID={account_id}, Password={'*' * len(password)}, Lotsize={lotsize}")

			if not account_id:
				log_info("    -> Skipped: Missing account_id")
				continue

			try:
				login = int(account_id)
			except ValueError:
				log_info("    -> Skipped: Invalid account_id (not an integer)")
				continue

			if lotsize == 0.0:
				log_info("    -> Skipped: Lotsize is 0.0")
				continue

			label = f"{group_name.upper()}-{name}-{login}"
			accounts.append({
				"label": label,
				"login": login,
				"password": password,
				"server": server,
				"lotsize": lotsize,
			})
	log_info(f"Total active accounts loaded (lotsize > 0): {len(accounts)}")
	log_info("===========================================================\n")
	return accounts


def login_account(account):
	# 1. Check if we are already connected and logged into this account
	try:
		terminal = mt5.terminal_info()
		if terminal is not None:
			acc = mt5.account_info()
			if acc is not None and getattr(acc, "login", None) == account["login"]:
				# Already logged into the correct account and connection is alive
				return True
	except Exception:
		pass

	# 2. Try to initialize
	if not mt5.initialize():
		mt5.shutdown()
		if not mt5.initialize():
			log_error(f"[{account['label']}] mt5.initialize failed: {mt5.last_error()}", detail=str(mt5.last_error()))
			return False

	# 3. Login to the account
	authorized = mt5.login(account["login"], password=account["password"], server=account["server"])
	if not authorized:
		log_error(f"[{account['label']}] login failed for {account['login']}: {mt5.last_error()}", detail=str(mt5.last_error()))
		return False

	log_info(f"[{account['label']}] connected to account {account['login']}")
	return True


def is_autotrading_enabled():
	terminal_info = mt5.terminal_info()
	if terminal_info is None:
		log_error(f"Warning: Could not read terminal info: {mt5.last_error()}", detail=str(mt5.last_error()))
		return False

	trade_allowed = bool(getattr(terminal_info, "trade_allowed", True))
	tradeapi_disabled = bool(getattr(terminal_info, "tradeapi_disabled", False))
	if not trade_allowed or tradeapi_disabled:
		return False

	account_info = mt5.account_info()
	if account_info is None:
		log_error(f"Warning: Could not read account info: {mt5.last_error()}", detail=str(mt5.last_error()))
		return False

	return bool(getattr(account_info, "trade_allowed", True))


def parse_signal(text):
	if not text:
		return None

	if "high risk" in text.lower():
		current_time = datetime.now().strftime('%H:%M:%S')
		log_info(f"[{current_time}] high risk signal is skipped, do not trade according to this signal.")
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
		log_info(f"[{account_label}] autotrading is disabled.")
		return False

	if not mt5.symbol_select(symbol, True):
		log_error(f"[{account_label}] failed to select {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
		return False

	return True


def send_order_request(account_label, request, description):
	result = mt5.order_send(request)
	if result is None:
		log_error(f"[{account_label}] {description} failed: {mt5.last_error()}", detail=str(mt5.last_error()))
		return None

	if result.retcode != mt5.TRADE_RETCODE_DONE:
		error = mt5.last_error()
		# Provide more detailed error messages for specific retcodes
		retcode_name = "UNKNOWN"
		if result.retcode == 10015:
			retcode_name = "TRADE_BUY_ONLY (10015)"
		elif result.retcode == 10016:
			retcode_name = "TRADE_ONLY_REAL (10016)"
		elif result.retcode == 10018:
			retcode_name = "TRADE_NO_MONEY (10018)"
		elif result.retcode == 10019:
			retcode_name = "TRADE_PROHIBITED (10019)"
		
		# Log symbol and account info for debugging
		account_info = mt5.account_info()
		positions = mt5.positions_get(symbol=request.get("symbol", "")) or []
		orders = mt5.orders_get(symbol=request.get("symbol", "")) or []
		
		detail_msg = (
			f"Retcode Name: {retcode_name}\n"
			f"MT5 Last Error: {error}\n"
			f"Account Balance: {getattr(account_info, 'balance', 'N/A')}\n"
			f"Free Margin: {getattr(account_info, 'free_margin', 'N/A')}\n"
			f"Existing Positions: {len(positions)}\n"
			f"Existing Orders: {len(orders)}\n"
			f"Order Request: {request}"
		)
		log_error(f"[{account_label}] {description} failed retcode={result.retcode} ({retcode_name})", detail=detail_msg)
		return None

	log_info(f"[{account_label}] {description} placed ticket={result.order}")
	return result


async def monitor_signal_async(accounts, monitor_account, signal_data, signal_tag):
	"""
	Async monitoring coroutine for a single signal.
	Runs independently and doesn't block other signals from being placed.
	Monitors TP1 to move SL to entry + 1.0 and cancel pending orders.
	Monitors TP2 to move SL of the TP3 position to TP1.
	"""
	symbol = signal_data["symbol"]
	action = signal_data["action"]
	sl = signal_data["sl"]
	tp1 = signal_data["tp1"]
	tp2 = signal_data["tp2"]

	start_ts = time.time()
	log_info(f"[ASYNC] Started monitoring {signal_tag} for {symbol}")

	tp1_processed = False
	tp2_processed = False

	while time.time() - start_ts <= MONITOR_SECONDS:
		try:
			# Log in to monitor account to read live price
			if not login_account(monitor_account):
				await asyncio.sleep(MONITOR_POLL_INTERVAL)
				continue

			tick = mt5.symbol_info_tick(symbol)
			if tick is None:
				await asyncio.sleep(MONITOR_POLL_INTERVAL)
				continue

			current_price = get_monitor_price(tick, action)
			
			# Check SL hit
			sl_hit = current_price <= sl if action == "BUY" else current_price >= sl
			if sl_hit:
				log_info(f"[ASYNC] {signal_tag}: SL reached at {current_price}; cancelling any remaining pending orders.")
				def sl_cleanup(account):
					cancel_signal_pending_orders_for_account(account["label"], signal_data, signal_tag)
				run_for_all_accounts(accounts, monitor_account, sl_cleanup)
				return

			# Check TP1 hit
			if not tp1_processed:
				tp1_hit = current_price >= tp1 if action == "BUY" else current_price <= tp1
				if tp1_hit:
					log_info(f"[ASYNC] {signal_tag}: TP1 reached at {current_price}; moving SL to entry + buffer and cancelling pending orders.")
					def tp1_actions(account):
						modify_signal_positions_for_account(account["label"], signal_data, signal_tag)
						cancel_signal_pending_orders_for_account(account["label"], signal_data, signal_tag)
					run_for_all_accounts(accounts, monitor_account, tp1_actions)
					tp1_processed = True

			# Check TP2 hit (only after TP1 is processed)
			if tp1_processed and not tp2_processed:
				tp2_hit = current_price >= tp2 if action == "BUY" else current_price <= tp2
				if tp2_hit:
					log_info(f"[ASYNC] {signal_tag}: TP2 reached at {current_price}; moving TP3 position SL to TP1 ({tp1}).")
					def tp2_actions(account):
						modify_tp3_positions_to_tp1_for_account(account["label"], signal_data, signal_tag)
					run_for_all_accounts(accounts, monitor_account, tp2_actions)
					tp2_processed = True

			# End monitoring if all positions and orders are closed
			if tp1_processed:
				exists = False
				for account in accounts:
					if not login_account(account):
						continue
					orders = mt5.orders_get(symbol=symbol) or []
					active_orders = [o for o in orders if str(getattr(o, "comment", "") or "").strip() == signal_tag]
					positions = mt5.positions_get(symbol=symbol) or []
					active_positions = [p for p in positions if str(getattr(p, "comment", "") or "").strip() == signal_tag]
					if len(active_orders) > 0 or len(active_positions) > 0:
						exists = True
						break
				if not exists:
					log_info(f"[ASYNC] {signal_tag}: All trades closed. Monitoring complete.")
					return

			await asyncio.sleep(MONITOR_POLL_INTERVAL)

		except Exception as e:
			log_error(f"[ASYNC] {signal_tag}: Monitoring error: {e}", detail=str(e))
			await asyncio.sleep(MONITOR_POLL_INTERVAL)

	log_info(f"[ASYNC] {signal_tag}: Monitor timeout reached.")


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


def place_signal_orders_for_account(account, signal_data, signal_tag, current_price):
	account_label = account["label"]
	lotsize = account["lotsize"]
	symbol = signal_data["symbol"]
	action = signal_data["action"]
	sl = signal_data["sl"]

	if not signal_is_live(account_label, symbol):
		return False

	symbol_info = mt5.symbol_info(symbol)
	if symbol_info is None:
		log_error(f"[{account_label}] failed to read symbol info for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
		return False

	local_tick = mt5.symbol_info_tick(symbol)
	if local_tick is None:
		log_error(f"[{account_label}] failed to read live tick for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
		return False
	local_market_price = get_monitor_price(local_tick, action)

	entry_plan = build_entry_plan(signal_data, current_price)
	if not entry_plan:
		log_info(f"[{account_label}] no entry plan available at price {current_price}")
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

		# Determine filling mode dynamically based on symbol properties
		filling_mode = mt5.ORDER_FILLING_RETURN
		if symbol_info is not None:
			if symbol_info.filling_mode & 1:
				filling_mode = mt5.ORDER_FILLING_FOK
			elif symbol_info.filling_mode & 2:
				filling_mode = mt5.ORDER_FILLING_IOC

		request = {
			"action": action_type,
			"symbol": symbol,
			"volume": float(lotsize),
			"type": order_type,
			"sl": float(sl),
			"tp": tp,
			"deviation": 20,
			"magic": MAGIC,
			"comment": signal_tag,
			"type_time": mt5.ORDER_TIME_GTC,
			"type_filling": filling_mode,
		}

		# For pure market orders, price is typically ignored or set to 0 in MT5.
		if not is_market:
			request["price"] = price
			# Validate SL/TP stops before sending to avoid Invalid Stops error
			if action == "BUY":
				if sl >= price:
					log_info(f"[{account_label}] entry {index} skipped: SL {sl} is above/equal to pending entry price {price} (invalid stops)")
					continue
				if tp <= price:
					log_info(f"[{account_label}] entry {index} skipped: TP {tp} is below/equal to pending entry price {price} (invalid stops)")
					continue
			elif action == "SELL":
				if sl <= price:
					log_info(f"[{account_label}] entry {index} skipped: SL {sl} is below/equal to pending entry price {price} (invalid stops)")
					continue
				if tp >= price:
					log_info(f"[{account_label}] entry {index} skipped: TP {tp} is above/equal to pending entry price {price} (invalid stops)")
					continue
		else:
			request["price"] = 0.0

		result = send_order_request(account_label, request, f"entry {index}")
		if result is None:
			if index > 1 and entry_plan[0]["kind"] == "pending":
				pending_orders = mt5.orders_get(symbol=symbol) or []
				for pending_order in pending_orders:
					if str(getattr(pending_order, "comment", "") or "") == signal_tag:
						mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": pending_order.ticket})
						log_info(f"[{account_label}] rolled back pending order {pending_order.ticket} after later entry failure")
			if placed_any:
				log_error(f"[{account_label}] partial fill detected for {signal_tag}; manual review may be needed.")
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
		log_info(f"[{account_label}] no open positions found for {signal_tag}")
		return True

	modified_any = False
	for position in positions:
		comment = str(getattr(position, "comment", "") or "")
		if comment != signal_tag:
			continue

		entry_price = float(getattr(position, "price_open", 0.0) or 0.0)
		symbol_info = mt5.symbol_info(symbol)
		if symbol_info is None:
			log_error(f"[{account_label}] failed to read symbol info for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
			continue
		digits = symbol_info.digits

		# Validate and adjust stop loss to prevent 10016 (Invalid Stops) error
		tick = mt5.symbol_info_tick(symbol)
		if tick is None:
			log_error(f"[{account_label}] failed to read current tick for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
			continue

		point = symbol_info.point
		stops_level = getattr(symbol_info, "trade_stops_level", 0)
		safety_margin = max(stops_level, 50) * point

		if action == "BUY":
			new_sl = round(entry_price + TP1_BREAKEVEN_BUFFER, digits)
			max_allowed_sl = round(tick.bid - safety_margin, digits)
			if new_sl > max_allowed_sl:
				log_info(f"[{account_label}] new SL {new_sl} is too close/above bid {tick.bid}. Adjusting to safe SL: {max_allowed_sl}")
				new_sl = max_allowed_sl
		else:
			new_sl = round(entry_price - TP1_BREAKEVEN_BUFFER, digits)
			min_allowed_sl = round(tick.ask + safety_margin, digits)
			if new_sl < min_allowed_sl:
				log_info(f"[{account_label}] new SL {new_sl} is too close/below ask {tick.ask}. Adjusting to safe SL: {min_allowed_sl}")
				new_sl = min_allowed_sl

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
			log_info(f"[{account_label}] position {position.ticket} SL moved to {new_sl}")
			modified_any = True
		else:
			err_code = result.retcode if result else "No Result"
			log_error(f"[{account_label}] failed to modify SL for position {position.ticket}: {err_code}", detail=f"Result: {result}, Error: {mt5.last_error()}")

	return modified_any


def modify_tp3_positions_to_tp1_for_account(account_label, signal_data, signal_tag):
	symbol = signal_data["symbol"]
	action = signal_data["action"]
	tp1 = signal_data["tp1"]
	tp3 = signal_data["tp3"]

	if not signal_is_live(account_label, symbol):
		return False

	positions = mt5.positions_get(symbol=symbol)
	if not positions:
		log_info(f"[{account_label}] no open positions found for {signal_tag}")
		return False

	modified_any = False
	for position in positions:
		comment = str(getattr(position, "comment", "") or "")
		if comment != signal_tag:
			continue
		
		# Check if this position aims TP3
		pos_tp = float(getattr(position, "tp", 0.0) or 0.0)
		if abs(pos_tp - tp3) > 0.01:
			continue

		symbol_info = mt5.symbol_info(symbol)
		if symbol_info is None:
			log_error(f"[{account_label}] failed to read symbol info for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
			continue
		digits = symbol_info.digits

		# Validate and adjust stop loss to prevent 10016 (Invalid Stops) error
		tick = mt5.symbol_info_tick(symbol)
		if tick is None:
			log_error(f"[{account_label}] failed to read current tick for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
			continue

		point = symbol_info.point
		stops_level = getattr(symbol_info, "trade_stops_level", 0)
		safety_margin = max(stops_level, 50) * point

		new_sl = tp1
		if action == "BUY":
			max_allowed_sl = round(tick.bid - safety_margin, digits)
			if new_sl > max_allowed_sl:
				log_info(f"[{account_label}] TP3 SL TP1 {new_sl} is too close/above bid {tick.bid}. Adjusting to safe SL: {max_allowed_sl}")
				new_sl = max_allowed_sl
		else:
			min_allowed_sl = round(tick.ask + safety_margin, digits)
			if new_sl < min_allowed_sl:
				log_info(f"[{account_label}] TP3 SL TP1 {new_sl} is too close/below ask {tick.ask}. Adjusting to safe SL: {min_allowed_sl}")
				new_sl = min_allowed_sl

		request = {
			"action": mt5.TRADE_ACTION_SLTP,
			"position": position.ticket,
			"symbol": symbol,
			"sl": float(new_sl),
			"tp": pos_tp,
			"magic": MAGIC,
		}
		result = mt5.order_send(request)
		if result and result.retcode == mt5.TRADE_RETCODE_DONE:
			log_info(f"[{account_label}] position {position.ticket} (aims TP3) SL moved to TP1 ({new_sl})")
			modified_any = True
		else:
			err_code = result.retcode if result else "No Result"
			log_error(f"[{account_label}] failed to modify SL to TP1 for position {position.ticket}: {err_code}", detail=f"Result: {result}, Error: {mt5.last_error()}")

	return modified_any


def cancel_signal_pending_orders_for_account(account_label, signal_data, signal_tag):
	symbol = signal_data["symbol"]

	if not signal_is_live(account_label, symbol):
		return False

	orders = mt5.orders_get(symbol=symbol)
	if not orders:
		log_info(f"[{account_label}] no pending orders found for {signal_tag}")
		return True

	cancelled_any = False
	for order in orders:
		comment = str(getattr(order, "comment", "") or "")
		if comment != signal_tag:
			continue

		request = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
		result = mt5.order_send(request)
		if result and result.retcode == mt5.TRADE_RETCODE_DONE:
			log_info(f"[{account_label}] pending order {order.ticket} cancelled")
			cancelled_any = True
		else:
			err_code = result.retcode if result else "No Result"
			log_error(f"[{account_label}] failed to cancel order {order.ticket}: {err_code}", detail=f"Result: {result}, Error: {mt5.last_error()}")

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
			log_info(f"[{account['label']}] already active monitor account; using current session")
			try:
				handler(account)
			except Exception as e:
				log_error(f"[{account['label']}] handler error in run_for_all_accounts: {e}", detail=str(e))
			continue

		if not login_account(account):
			log_info(f"[{account['label']}] skipped")
			continue
		try:
			handler(account)
		except Exception as e:
			log_error(f"[{account['label']}] handler error in run_for_all_accounts: {e}", detail=str(e))

	if monitor_account:
		return login_account(monitor_account)

	return False


async def monitor_retracement_async(signal_data, accounts, monitor_account):
	symbol = signal_data["symbol"]
	action = signal_data["action"]
	range_low = signal_data["range_low"]
	range_high = signal_data["range_high"]
	
	signal_tag = build_comment()
	log_info(f"[RETRACEMENT] Started monitoring retracement for {signal_tag} on {symbol} (5 minutes)")

	start_ts = time.time()
	while time.time() - start_ts <= 300: # 5 minutes
		try:
			# Log in to monitor account to read live price
			if not login_account(monitor_account):
				await asyncio.sleep(2)
				continue

			tick = mt5.symbol_info_tick(symbol)
			if tick is None:
				await asyncio.sleep(2)
				continue

			current_price = get_monitor_price(tick, action)
			in_zone = range_low <= current_price <= range_high

			if in_zone:
				log_info(f"[RETRACEMENT] {signal_tag}: Price retraced back to zone ({current_price}). Checking if orders already exist...")
				
				# Check if any limit order or position exists for this signal tag on any account
				exists = False
				for account in accounts:
					if not login_account(account):
						continue
					
					orders = mt5.orders_get(symbol=symbol) or []
					active_orders = [o for o in orders if str(getattr(o, "comment", "") or "").strip() == signal_tag]
					
					positions = mt5.positions_get(symbol=symbol) or []
					active_positions = [p for p in positions if str(getattr(p, "comment", "") or "").strip() == signal_tag]
					
					if len(active_orders) > 0 or len(active_positions) > 0:
						exists = True
						break
				
				if not exists:
					log_info(f"[RETRACEMENT] {signal_tag}: No existing orders found. Entering the trade now!")
					
					placed_state = {"placed": False}
					def place_entries(account):
						placed = place_signal_orders_for_account(account, signal_data, signal_tag, current_price)
						placed_state["placed"] = placed_state["placed"] or placed

					# Run for all accounts
					if run_for_all_accounts(accounts, monitor_account, place_entries):
						if placed_state["placed"]:
							# Start the async monitoring task for TP1/TP2/TP3
							asyncio.create_task(
								monitor_signal_async(accounts, monitor_account, signal_data, signal_tag)
							)
							log_info(f"[RETRACEMENT] {signal_tag}: Successfully entered trade and started async monitoring.")
							return
					else:
						log_error(f"[RETRACEMENT] {signal_tag}: Failed to enter trade on all accounts.")
				else:
					log_info(f"[RETRACEMENT] {signal_tag}: Orders/positions already exist. Skipping entry.")
					return

			await asyncio.sleep(2)

		except Exception as e:
			log_error(f"[RETRACEMENT] {signal_tag} error: {e}", detail=str(e))
			await asyncio.sleep(2)

	log_info(f"[RETRACEMENT] {signal_tag}: 5-minute timeout reached without price retracing to zone.")


def send_trade(signal_data, accounts, monitor_account):
	if not signal_data:
		return None

	symbol = signal_data["symbol"]
	action = signal_data["action"]
	range_low = signal_data["range_low"]
	range_high = signal_data["range_high"]
	sl = signal_data["sl"]
	tp1 = signal_data["tp1"]

	if not symbol.startswith("XAUUSD"):
		log_info(f"Trade skipped: only XAUUSD signals are supported, got {symbol}.")
		return None

	if action not in {"BUY", "SELL"}:
		log_info(f"Trade skipped: unsupported action {action}.")
		return None

	# Ensure we are connected/logged into the monitor account first to prevent No IPC Connection error
	if not login_account(monitor_account):
		log_error(f"Failed to connect/login to monitor account for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
		return None

	if not mt5.symbol_select(symbol, True):
		log_error(f"Failed to select {symbol} on monitor account: {mt5.last_error()}", detail=str(mt5.last_error()))
		return None

	tick = mt5.symbol_info_tick(symbol)
	if tick is None:
		log_error(f"Failed to read current price for {symbol}: {mt5.last_error()}", detail=str(mt5.last_error()))
		return None

	current_price = get_monitor_price(tick, action)

	# Check for price spike too fast:
	is_spike = False
	if action == "BUY" and current_price > range_high:
		is_spike = True
	elif action == "SELL" and current_price < range_low:
		is_spike = True

	if is_spike:
		log_info(f"Trade skipped: price spiked too fast ({current_price} is outside zone {range_low}-{range_high}). Will monitor for 5 minutes for retracement.")
		asyncio.create_task(monitor_retracement_async(signal_data, accounts, monitor_account))
		return None

	if action == "BUY" and current_price <= sl:
		log_info(f"Trade skipped: current price {current_price} is already at/below SL {sl}.")
		return None
	if action == "SELL" and current_price >= sl:
		log_info(f"Trade skipped: current price {current_price} is already at/above SL {sl}.")
		return None

	signal_tag = build_comment()
	log_info(f"Processing signal {signal_tag}: {symbol} {action} range={range_low}-{range_high} SL={sl} TP1={tp1}")

	placed_state = {"placed": False}

	def place_entries(account):
		placed = place_signal_orders_for_account(account, signal_data, signal_tag, current_price)
		placed_state["placed"] = placed_state["placed"] or placed

	if not run_for_all_accounts(accounts, monitor_account, place_entries):
		log_error("Trade flow stopped: monitor account could not be restored after entry placement.")
		return None

	if not placed_state["placed"]:
		log_info(f"No orders were placed for {signal_tag}.")
		return None

	# Return signal_tag to be used for async monitoring
	log_info(f"[INFO] Orders placed for {signal_tag}; monitoring will continue asynchronously.")
	return signal_tag


async def monitor_signals_from_notifications(accounts, monitor_account):
	target_group = os.getenv("TARGET_WA_GROUP", "").strip()

	if target_group:
		log_info(f"Monitoring WhatsApp group: '{target_group}'")
	else:
		log_info("TARGET_WA_GROUP is empty; all WhatsApp notifications will be considered.")

	# Track active monitoring tasks
	active_tasks = set()

	async def cleanup_task(task):
		"""Remove completed tasks from tracking."""
		try:
			await task
		except Exception as e:
			log_error(f"[CLEANUP] Task error: {e}", detail=str(e))
		finally:
			active_tasks.discard(task)

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
				log_info("\nNEW SIGNAL RECEIVED (WhatsApp notification):")
				log_info(f"Group: {group_name}")

				signal_json = parse_signal(body)
				log_info(f"Parsed Signal: {signal_json}")

				# Place orders immediately (non-blocking), returns signal_tag if successful
				signal_tag = send_trade(signal_json, accounts, monitor_account)

				# If orders were placed (signal_tag is not None), spawn async monitoring task
				if signal_tag:
					# Create monitoring task that runs concurrently
					task = asyncio.create_task(
						monitor_signal_async(accounts, monitor_account, signal_json, signal_tag)
					)
					active_tasks.add(task)
					# Schedule cleanup when task completes
					asyncio.create_task(cleanup_task(task))

					# Log active monitoring count
					log_info(f"[INFO] Currently monitoring {len(active_tasks)} concurrent signal(s)")

		except Exception as e:
			log_error(f"Notification read error: {e}", detail=str(e))
			log_info("Retrying in 3 seconds...")
			await asyncio.sleep(3)
			if monitor_account:
				login_account(monitor_account)


def main():
	accounts = load_accounts_from_json()
	if not accounts:
		return

	monitor_account = None
	for account in accounts:
		if login_account(account):
			monitor_account = account
			break

	if monitor_account is None:
		log_error("No account could be logged in for monitoring.")
		return

	try:
		asyncio.run(monitor_signals_from_notifications(accounts, monitor_account))
	except KeyboardInterrupt:
		log_info("Stopped by user")
	finally:
		mt5.shutdown()


if __name__ == "__main__":
	main()