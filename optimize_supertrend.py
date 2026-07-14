import os, sys, json
from datetime import datetime, date
from collections import defaultdict
sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SwingPortfolio')]
from core.registry import get_strategy, get_strategy_universe
from core.backtest_engine import run_backtest, _months_from_range
os.environ['APP_ENV'] = 'prod'
now = datetime.now()
END = date(now.year, now.month, 1)
START = date(2025, 9, 1)
months = _months_from_range(START, END)

def score(pnl, winrate, sharpe, pf, n):
    w_pnl = min(pnl/100000,5) if pnl>0 else pnl/100000
    w_wr = winrate/100.0
    w_sharpe = min(sharpe/3.0,2) if sharpe else 0
    w_pf = min(pf/3.0,2) if pf else 0
    w_trades = min(n/30, 1.5)
    return round(w_pnl*3 + w_wr*3 + w_sharpe*2 + w_pf*1.5 + w_trades*0.5, 2)

def run_bt(overrides):
    s = get_strategy('supertrend_volume')
    for k,v in overrides.items():
        setattr(s, k, v)
    try:
        trades, _ = run_backtest(s, months=months,
                                 universe=get_strategy_universe('supertrend_volume'),
                                 entry_mode="15_15", capital=1500000, max_pos=3,
                                 position_sizing='lot', option_strike='ATM')
    except Exception as e:
        print(f'  ERROR: {e}', flush=True); return None
    if not trades: return None
    df = trades if isinstance(trades, list) else trades.to_dict('records')
    n = len(df)
    if n==0: return None
    wins = [t for t in df if t.get('pnl',0)>0]
    losses = [t for t in df if t.get('pnl',0)<=0]
    nw,nl = len(wins),len(losses)
    total_pnl = sum(t.get('pnl',0) for t in df)
    wr = nw/n*100
    pf = (sum(t.get('pnl',0) for t in wins)/abs(sum(t.get('pnl',0) for t in losses))) if losses and sum(t.get('pnl',0) for t in losses)!=0 else None
    day_pnl = defaultdict(float)
    for t in df:
        day_pnl[str(t.get('exit_dt',''))[:10]] += t.get('pnl',0)
    dr = list(day_pnl.values())
    if len(dr)>1:
        import numpy as np
        sharpe = (np.mean(dr)/np.std(dr,ddof=1)*(252**0.5)) if np.std(dr,ddof=1)>0 else None
    else:
        sharpe = None
    cum,peak,maxdd = 0,0,0
    for t in sorted(df, key=lambda x: x.get('exit_dt','')):
        cum+=t.get('pnl',0)
        if cum>peak: peak=cum
        dd=peak-cum
        if dd>maxdd: maxdd=dd
    return {'total_trades':n,'total_pnl':round(total_pnl,0),'winrate':round(wr,1),
            'profit_factor':round(pf,2) if pf else None,'sharpe':round(sharpe,2) if sharpe else None,
            'max_dd':round(maxdd,0),'score':score(total_pnl,wr,sharpe,pf,n),'overrides':overrides}

print("=== SUPERTREND VOLUME OPTIMIZATION ===\n", flush=True)
base_r = run_bt({})
if base_r: print(f"--- Baseline (strategies.json defaults) ---", flush=True); print(f"  P&L={base_r['total_pnl']:,.0f} WR={base_r['winrate']}% PF={base_r['profit_factor']} Trades={base_r['total_trades']} Score={base_r['score']}", flush=True)

print("\n--- Sweep: period ---", flush=True)
for v in [7,10,12,15]:
    r=run_bt({'period':v})
    if r: print(f"  period={v:>2}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: multiplier ---", flush=True)
for v in [2.0,2.5,3.0,3.5]:
    r=run_bt({'multiplier':v})
    if r: print(f"  mult={v:.1f}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: volume_filter ---", flush=True)
for v in [1.0,1.5,2.0]:
    r=run_bt({'volume_filter':v})
    if r: print(f"  vol_flt={v:.1f}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: sl_atr_multiplier ---", flush=True)
for v in [1.5,2.0,2.5,3.0]:
    r=run_bt({'sl_atr_multiplier':v})
    if r: print(f"  sl_atr={v:.1f}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: rr_ratio ---", flush=True)
for v in [1.5,2.0,2.5,3.0]:
    r=run_bt({'rr_ratio':v})
    if r: print(f"  rr={v:.1f}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: max_hold_days ---", flush=True)
for v in [7,10,14]:
    r=run_bt({'max_hold_days':v})
    if r: print(f"  hold={v:>2}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: trail_pct ---", flush=True)
for v in [0.01,0.02,0.03,0.04]:
    r=run_bt({'trail_pct':v})
    if r: print(f"  trail={v:.2f}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Top combos ---", flush=True)
combos = [
    {'period':7,'multiplier':3.0,'rr_ratio':2.5},
    {'period':7,'multiplier':2.5,'rr_ratio':3.0},
    {'period':7,'multiplier':3.5,'sl_atr_multiplier':1.5},
    {'sl_atr_multiplier':2.5,'rr_ratio':2.5,'trail_pct':0.03},
    {'period':12,'multiplier':2.5,'rr_ratio':2.5},
    {'rr_ratio':2.5,'max_hold_days':7,'trail_pct':0.01},
    {'period':7,'multiplier':2.5,'rr_ratio':3.0,'trail_pct':0.01},
]
for p in combos:
    r=run_bt(p)
    if r: print(f"  {p}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)
