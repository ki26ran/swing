"""
Systematic grid search for all 3 swing strategies.
Runs parameter combos, identifies best config per strategy by profit + winrate.
"""
import os, sys, json, itertools, time, csv
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

RESULTS = {}


def score(pnl, winrate, sharpe, pf, total_trades):
    w_pnl = min(pnl / 100000, 5) if pnl > 0 else pnl / 100000
    w_wr = winrate / 100.0
    w_sharpe = min(sharpe / 3.0, 2) if sharpe is not None else 0
    w_pf = min(pf / 3.0, 2) if pf is not None else 0
    w_trades = min(total_trades / 30, 1.5)
    return w_pnl * 3 + w_wr * 3 + w_sharpe * 2 + w_pf * 1.5 + w_trades * 0.5


def run_bt(strategy_id, params):
    """Run backtest with given params and return metrics."""
    s = get_strategy(strategy_id)
    for k, v in params.items():
        setattr(s, k, v)
    universe = get_strategy_universe(strategy_id)
    try:
        trades, _ = run_backtest(s, months=months, universe=universe,
                                 capital=1500000, max_pos=3,
                                 position_sizing='lot', option_strike='ATM')
    except Exception as e:
        return None

    if not trades:
        return None

    df = trades if isinstance(trades, list) else trades.to_dict('records')
    n = len(df)
    if n == 0:
        return None

    wins = [t for t in df if t.get('pnl', 0) > 0]
    losses = [t for t in df if t.get('pnl', 0) <= 0]
    nw, nl = len(wins), len(losses)
    total_pnl = sum(t.get('pnl', 0) for t in df)
    winrate = (nw / n * 100) if n > 0 else 0
    avg_win = sum(t.get('pnl', 0) for t in wins) / nw if nw > 0 else 0
    avg_loss = abs(sum(t.get('pnl', 0) for t in losses)) / nl if nl > 0 else 0
    pf = (sum(t.get('pnl', 0) for t in wins) / abs(sum(t.get('pnl', 0) for t in losses))) if losses and sum(t.get('pnl',0) for t in losses) != 0 else None

    day_pnl = defaultdict(float)
    for t in df:
        d = str(t.get('exit_dt', ''))[:10]
        day_pnl[d] += t.get('pnl', 0)
    daily_returns = list(day_pnl.values())
    if len(daily_returns) > 1:
        import numpy as np
        mean_r = np.mean(daily_returns)
        std_r = np.std(daily_returns, ddof=1)
        sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else None
    else:
        sharpe = None

    maxdd = 0
    cum = 0
    peak = 0
    for t in sorted(df, key=lambda x: x.get('exit_dt', '')):
        cum += t.get('pnl', 0)
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > maxdd:
            maxdd = dd

    return {
        'total_trades': n,
        'total_pnl': round(total_pnl, 0),
        'winrate': round(winrate, 1),
        'profit_factor': round(pf, 2) if pf else None,
        'sharpe': round(sharpe, 2) if sharpe else None,
        'max_dd': round(maxdd, 0),
        'avg_win': round(avg_win, 0),
        'avg_loss': round(avg_loss, 0),
        'score': round(score(total_pnl, winrate, sharpe, pf, n), 2),
    }


def grid_search(strategy_id, param_grid, base_params=None):
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    results = []

    total = 1
    for v in values:
        total *= len(v)

    print(f"\n{'='*60}")
    print(f"Strategy: {strategy_id}")
    print(f"Grid size: {total} combinations")
    print(f"{'='*60}")

    if base_params:
        print("\n--- Baseline ---")
        baseline = run_bt(strategy_id, base_params)
        if baseline:
            print(f"  Trades: {baseline['total_trades']}, P&L: {baseline['total_pnl']:,.0f}, WR: {baseline['winrate']}%, PF: {baseline['profit_factor']}, Sharpe: {baseline['sharpe']}, Score: {baseline['score']}")
    else:
        baseline = None

    idx = 0
    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        if base_params:
            full_params = {**base_params, **params}
        else:
            full_params = params

        idx += 1
        if idx % 10 == 0 or idx == 1:
            print(f"  [{idx}/{total}] Testing: {params}")

        result = run_bt(strategy_id, full_params)
        if result:
            result['params'] = params
            results.append(result)

    results.sort(key=lambda r: r['score'], reverse=True)

    print(f"\n--- Top 10 Results for {strategy_id} ---")
    print(f"{'Rank':<5} {'Score':<8} {'P&L':<12} {'WR%':<8} {'PF':<8} {'Sharpe':<8} {'Trades':<8} {'MaxDD':<10} Params")
    print("-"*120)
    for i, r in enumerate(results[:10]):
        print(f"{i+1:<5} {r['score']:<8} {r['total_pnl']:<12,.0f} {r['winrate']:<8} {str(r['profit_factor']):<8} {str(r['sharpe']):<8} {r['total_trades']:<8} {r['max_dd']:<10,.0f} {r['params']}")

    RESULTS[strategy_id] = {
        'baseline': baseline,
        'top10': results[:10],
        'all': results
    }
    return results


# ============================================================
# 1. Donchian ADX
# ============================================================
print("\n\n### DONCHIAN ADX GRID SEARCH ###")

donchian_base = {'donchian_period': 20, 'atr_period': 14, 'adx_threshold': 25, 'atr_multiplier': 2.0}
donchian_grid = {
    'donchian_period': [15, 20, 25, 30],
    'atr_period': [10, 14, 20],
    'adx_threshold': [20, 25, 30, 35],
    'atr_multiplier': [1.5, 2.0, 2.5],
}
donchian_results = grid_search('donchian_adx', donchian_grid, donchian_base)

# ============================================================
# 2. Keltner RSI
# ============================================================
print("\n\n### KELTNER RSI GRID SEARCH ###")

keltner_base = {'keltner_period': 20, 'keltner_multiplier': 2.0, 'rsi_period': 14,
                'rsi_long_min': 55, 'rsi_short_max': 45, 'volume_multiplier': 2.0, 'adx_threshold': 25,
                'atr_period': 14, 'volume_ma_period': 20}
keltner_grid = {
    'keltner_period': [15, 20, 25],
    'keltner_multiplier': [1.5, 2.0, 2.5],
    'rsi_period': [10, 14, 20],
    'rsi_long_min': [50, 55, 60],
    'rsi_short_max': [35, 40, 45],
    'volume_multiplier': [1.5, 2.0, 3.0],
    'adx_threshold': [20, 25, 30],
}
keltner_results = grid_search('keltner_rsi', keltner_grid, keltner_base)

# ============================================================
# 3. Supertrend Volume
# ============================================================
print("\n\n### SUPERTREND VOLUME GRID SEARCH ###")

st_base = {'period': 10, 'multiplier': 3.0, 'volume_ma_period': 20, 'volume_filter': 1.0,
           'sl_atr_multiplier': 2.0, 'rr_ratio': 2.0, 'max_hold_days': 10, 'trail_pct': 0.02}
st_grid = {
    'period': [7, 10, 12, 15],
    'multiplier': [2.0, 2.5, 3.0, 3.5],
    'volume_filter': [1.0, 1.5, 2.0],
    'sl_atr_multiplier': [1.5, 2.0, 2.5, 3.0],
    'rr_ratio': [1.5, 2.0, 2.5, 3.0],
    'max_hold_days': [7, 10, 14],
    'trail_pct': [0.01, 0.02, 0.03],
}
st_results = grid_search('supertrend_volume', st_grid, st_base)

# ============================================================
# Summary
# ============================================================
print("\n\n")
print("="*80)
print("FINAL SUMMARY - BEST CONFIGS")
print("="*80)

for sid in ['donchian_adx', 'keltner_rsi', 'supertrend_volume']:
    top = RESULTS[sid]['top10'][0] if RESULTS[sid]['top10'] else None
    base = RESULTS[sid]['baseline']
    print(f"\n{sid}:")
    if base:
        print(f"  Baseline: P&L={base['total_pnl']:,.0f} WR={base['winrate']}% PF={base['profit_factor']} Score={base['score']}")
    if top:
        print(f"  Best:     P&L={top['total_pnl']:,.0f} WR={top['winrate']}% PF={top['profit_factor']} Score={top['score']}")
        print(f"  Params:   {top['params']}")

# Save results
out = {}
for sid in RESULTS:
    out[sid] = {
        'baseline': RESULTS[sid]['baseline'],
        'top10': RESULTS[sid]['top10']
    }
os.makedirs('SwingPortfolio/data/optim_results', exist_ok=True)
with open('SwingPortfolio/data/optim_results/grid_search_results.json', 'w') as f:
    json.dump(out, f, indent=2, default=str)
print("\n\nResults saved to SwingPortfolio/data/optim_results/grid_search_results.json")
