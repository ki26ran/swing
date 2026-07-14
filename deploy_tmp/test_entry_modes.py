import os, sys, time
sys.path[:0] = ["/opt/swing", "/opt/swing/SwingPortfolio"]
os.environ["APP_ENV"] = "prod"

from datetime import datetime, date
from collections import defaultdict
from core.registry import get_strategy, get_strategy_universe
from core.backtest_engine import run_backtest, _months_from_range

END = date(2026, 7, 1)
START = date(2025, 9, 1)
months = _months_from_range(START, END)

for entry_mode in ["15_15", "zscore"]:
    print(f"\n{'='*60}")
    print(f"Entry mode: {entry_mode}")
    print(f"{'='*60}")
    for sid in ["donchian_adx", "keltner_rsi", "supertrend_volume"]:
        s = get_strategy(sid)
        univ = get_strategy_universe(sid)
        t0 = time.time()
        trades, _ = run_backtest(s, months=months, universe=univ, entry_mode=entry_mode,
                                 capital=1500000, max_pos=3, position_sizing="lot", option_strike="ATM")
        t1 = time.time()
        if trades:
            pnl = sum(t.get("pnl",0) for t in trades)
            wins = [t for t in trades if t.get("pnl",0)>0]
            losses = [t for t in trades if t.get("pnl",0)<=0]
            wr = len(wins)/len(trades)*100
            nw = len(wins)
            nl = len(losses)
            pf_val = (sum(t.get("pnl",0) for t in wins)/abs(sum(t.get("pnl",0) for t in losses))) if losses and sum(t.get("pnl",0) for t in losses)!=0 else None
            avg_win = sum(t.get("pnl",0) for t in wins)/nw if nw>0 else 0
            avg_loss = abs(sum(t.get("pnl",0) for t in losses))/nl if nl>0 else 0
            print(f"  {sid:20s}: {len(trades):3d} trades, P&L={pnl:>8,.0f}, WR={wr:5.1f}%, PF={str(pf_val):>8}")
            if nw>0 and nl>0:
                print(f"  {'':20s}  AvgWin={avg_win:>7,.0f}, AvgLoss={avg_loss:>7,.0f}, Payoff={avg_win/avg_loss:.2f}")
        else:
            print(f"  {sid}: No trades")
