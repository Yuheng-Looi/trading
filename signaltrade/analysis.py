import MetaTrader5 as mt5
import os

from dotenv import load_dotenv
from datetime import datetime, timedelta
from collections import defaultdict

load_dotenv()


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


def _format_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _deal_time(deal):
    return datetime.fromtimestamp(deal.time)


def _realized_pnl(deal):
    fee = getattr(deal, "fee", 0.0)
    return deal.profit + deal.swap + deal.commission + fee


def _entry_name(entry):
    mapping = {
        mt5.DEAL_ENTRY_IN: "IN",
        mt5.DEAL_ENTRY_OUT: "OUT",
        mt5.DEAL_ENTRY_INOUT: "INOUT",
        mt5.DEAL_ENTRY_OUT_BY: "OUT_BY",
    }
    return mapping.get(entry, str(entry))


def _side_name(deal_type):
    if deal_type == mt5.DEAL_TYPE_BUY:
        return "BUY"
    if deal_type == mt5.DEAL_TYPE_SELL:
        return "SELL"
    return str(deal_type)


def _open_side_from_closing_deal_type(deal_type):
    if deal_type == mt5.DEAL_TYPE_SELL:
        return "BUY"
    if deal_type == mt5.DEAL_TYPE_BUY:
        return "SELL"
    return "N/A"


def _strategy_key(deal):
    if deal.magic and deal.magic != 0:
        return f"magic:{deal.magic}"
    comment = (deal.comment or "").strip()
    if comment:
        return f"comment:{comment}"
    return "comment:(empty)"


def _group_label(comment, magic):
    cleaned_comment = (comment or "").strip()
    magic_number = int(magic or 0)
    if not cleaned_comment and magic_number == 0:
        return "manual"
    if not cleaned_comment:
        cleaned_comment = "no_comment"
    return f"{cleaned_comment}({magic_number})"


def _get_server_time(symbol="XAUUSD-P"):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not getattr(tick, "time", 0):
        return None
    return datetime.fromtimestamp(tick.time)


def _get_report_offset_hours():
    return float(os.getenv("REPORT_TZ_OFFSET_HOURS", "-8"))


def _position_direction(position_deals):
    ordered = sorted(position_deals, key=lambda x: x.time)
    for deal in ordered:
        if deal.entry == mt5.DEAL_ENTRY_IN:
            return _side_name(deal.type)
    for deal in ordered:
        if deal.entry in {mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY}:
            return _open_side_from_closing_deal_type(deal.type)
    return "N/A"


def analyse():
    symbol_for_time = os.getenv("ANALYSIS_TIME_SYMBOL", "XAUUSD-P")
    server_now = _get_server_time(symbol_for_time)
    if server_now is None:
        server_now = datetime.now()
        print(f"Warning: cannot get server tick time from {symbol_for_time}, fallback to local now")

    report_offset_hours = _get_report_offset_hours()
    report_now = server_now + timedelta(hours=report_offset_hours)
    report_start = report_now.replace(hour=0, minute=0, second=0, microsecond=0)
    server_start = report_start - timedelta(hours=report_offset_hours)

    deals = mt5.history_deals_get(server_start, server_now)

    print("\n=== Today Window (Server Tick Time) ===")
    print(f"Time symbol: {symbol_for_time}")
    print(f"REPORT_TZ_OFFSET_HOURS={report_offset_hours}")
    print(f"Local now: {_format_dt(datetime.now())}")
    print(f"Server now: {_format_dt(server_now)}")
    print(f"Report now: {_format_dt(report_now)}")
    print(f"Report day start: {_format_dt(report_start)}")
    print(f"Query window (server time): {_format_dt(server_start)} -> {_format_dt(server_now)}")

    if deals is None or len(deals) == 0:
        print("No trades found today")
        return

    trade_types = {mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL}
    all_trade_deals = [deal for deal in deals if deal.type in trade_types]

    if not all_trade_deals:
        print("No BUY/SELL trade deals found today")
        return

    closing_entries = {mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY}
    deals_by_position = defaultdict(list)
    for deal in all_trade_deals:
        if deal.position_id:
            deals_by_position[deal.position_id].append(deal)

    completed_trades = []
    for position_id, position_deals in deals_by_position.items():
        closing_deals = [d for d in position_deals if d.entry in closing_entries]
        if not closing_deals:
            continue

        open_time_utc = min(_deal_time(d) for d in position_deals)
        close_time_utc = max(_deal_time(d) for d in closing_deals)
        pnl = sum(_realized_pnl(d) for d in closing_deals)
        sample = sorted(position_deals, key=lambda x: x.time)[0]
        comment = (sample.comment or "").strip()

        completed_trades.append(
            {
                "position_id": position_id,
                "symbol": sample.symbol,
                "magic": sample.magic,
                "comment": comment,
                "direction": _position_direction(position_deals),
                "open_time": open_time_utc + timedelta(hours=report_offset_hours),
                "close_time": close_time_utc + timedelta(hours=report_offset_hours),
                "pnl": pnl,
            }
        )

    completed_trades.sort(key=lambda x: x["close_time"])

    print("\n=== Today Completed Trades (One Row Per Trade) ===")
    print("open_time            close_time           position      symbol      dir   magic      pnl")
    print("-" * 104)
    for trade in completed_trades:
        print(
            f"{_format_dt(trade['open_time']):19} "
            f"{_format_dt(trade['close_time']):19} "
            f"{trade['position_id']:<13} "
            f"{trade['symbol']:<11} "
            f"{trade['direction']:<5} "
            f"{trade['magic']:<10} "
            f"{trade['pnl']:>8.2f}"
        )

    first_order_time = min(t["open_time"] for t in completed_trades)
    last_order_time = max(t["open_time"] for t in completed_trades)
    print(f"\nFirst order time (report): {_format_dt(first_order_time)}")
    print(f"Last order time (report): {_format_dt(last_order_time)}")

    if not completed_trades:
        print("No closing trade deals found today")
        return

    total_wins = 0
    total_losses = 0
    total_pnl = 0.0
    strategy_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "trades": 0, "pnl": 0.0})

    for trade in completed_trades:
        pnl = trade["pnl"]
        key = _group_label(trade["comment"], trade["magic"])

        strategy_stats[key]["trades"] += 1
        strategy_stats[key]["pnl"] += pnl
        total_pnl += pnl

        if pnl > 0:
            total_wins += 1
            strategy_stats[key]["wins"] += 1
        else:
            total_losses += 1
            strategy_stats[key]["losses"] += 1

    total_trades = total_wins + total_losses
    win_rate = (total_wins / total_trades * 100) if total_trades else 0.0

    print("\n=== Overall Summary (Today, Closed Trades) ===")
    print(f"Total Trades: {total_trades}")
    print(f"Wins/Losses: {total_wins}/{total_losses}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total PnL: {total_pnl:.2f}")

    print("\n=== Per Comment(Magic) (Closed Trades) ===")
    print("comment(magic)                 trades  wins  losses  winrate   pnl")
    print("-" * 72)
    for key, stats in sorted(strategy_stats.items()):
        trades = stats["trades"]
        wr = (stats["wins"] / trades * 100) if trades else 0.0
        print(
            f"{key:<30} "
            f"{trades:>6} "
            f"{stats['wins']:>5} "
            f"{stats['losses']:>7} "
            f"{wr:>7.2f}% "
            f"{stats['pnl']:>8.2f}"
        )


if login_demo():
    try:
        analyse()
    finally:
        mt5.shutdown()