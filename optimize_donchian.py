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
    s = get_strategy('donchian_adx')
    for k,v in overrides.items():
        setattr(s, k, v)
    try:
        trades, _ = run_backtest(s, months=months,
                                 universe=get_strategy_universe('donchian_adx'),
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

print("=== DONCHIAN ADX OPTIMIZATION ===\n", flush=True)
base_r = run_bt({})
if base_r: print(f"--- Baseline (strategies.json defaults) ---", flush=True); print(f"  P&L={base_r['total_pnl']:,.0f} WR={base_r['winrate']}% PF={base_r['profit_factor']} Trades={base_r['total_trades']} Score={base_r['score']}", flush=True)

print("\n--- Sweep: donchian_period ---", flush=True)
for v in [15,20,25,30]:
    r=run_bt({'donchian_period':v})
    if r: print(f"  period={v:>2}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: atr_period ---", flush=True)
for v in [10,14,20]:
    r=run_bt({'atr_period':v})
    if r: print(f"  atr={v:>2}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: adx_threshold ---", flush=True)
for v in [20,25,30,35]:
    r=run_bt({'adx_threshold':v})
    if r: print(f"  adx={v:>2}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Sweep: atr_multiplier ---", flush=True)
for v in [1.5,2.0,2.5,3.0]:
    r=run_bt({'atr_multiplier':v})
    if r: print(f"  mult={v:.1f}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)

print("\n--- Top combos (multiple params) ---", flush=True)
combos = [
    {'donchian_period':25,'atr_multiplier':1.5},
    {'donchian_period':25,'adx_threshold':30},
    {'donchian_period':30,'atr_multiplier':1.5},
    {'adx_threshold':30,'atr_multiplier':1.5},
    {'donchian_period':25,'adx_threshold':20},
    {'donchian_period':30,'adx_threshold':30,'atr_multiplier':1.5},
]
for p in combos:
    r=run_bt(p)
    if r: print(f"  {p}: P&L={r['total_pnl']:>8,.0f} WR={r['winrate']:>5}% PF={r['profit_factor']} Trades={r['total_trades']:>2} Score={r['score']}", flush=True)
