import os, sys, json, time
from datetime import datetime, date

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SwingPortfolio')]

os.environ['APP_ENV'] = 'prod'
os.environ['PYTHONUNBUFFERED'] = '1'

print("Importing...")
from core.registry import get_strategy, get_strategy_universe
from core.backtest_engine import run_backtest, _months_from_range
print("Imports done.")

now = datetime.now()
END = date(now.year, now.month, 1)
START = date(2025, 9, 1)
print(f"Range: {START} to {END}")
months = _months_from_range(START, END)
print(f"Months: {len(months)}")

print("Loading strategy...")
s = get_strategy('donchian_adx')
print(f"Strategy: {s.name}")

print("Loading universe...")
univ = get_strategy_universe('donchian_adx')
print(f"Universe: {len(univ)} symbols")

print("Running BT...")
t0 = time.time()
trades, day_log = run_backtest(s, months=months, universe=univ,
                               capital=1500000, max_pos=3,
                               position_sizing='lot', option_strike='ATM')
t1 = time.time()
print(f"Done in {t1-t0:.1f}s, {len(trades)} trades")

if trades:
    pnl = sum(t.get('pnl',0) for t in trades)
    wins = [t for t in trades if t.get('pnl',0) > 0]
    print(f"P&L: {pnl:,.0f}, WinRate: {len(wins)/len(trades)*100:.1f}%, Trades: {len(trades)}")
