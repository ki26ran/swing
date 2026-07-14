import streamlit as st
from core.registry import get_all_strategies

CSS = """
<style>
    .strat-card { border-radius: 8px; padding: 10px 14px; margin: 8px 0; border-left: 4px solid; background: #1a1a2e; }
    .tag-long { color: #00e676; font-weight: 600; }
    .tag-short { color: #ff5252; font-weight: 600; }
    .pnl-pos { color: #00e676; font-weight: 700; }
    .pnl-neg { color: #ff5252; font-weight: 700; }
    table { font-size: 0.85rem; }
    th, td { padding: 4px 8px; }
</style>
"""

def show():
    st.title("SwingPortfolio")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("Unified multi-strategy swing trading system for NSE F&O stocks.")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Overview", "Architecture", "Configuration", "Strategies",
        "Backtest Results", "Deployment"
    ])

    with tab1:
        st.subheader("Overview")
        st.markdown("""
        **Active Strategies (as of June 2026):**
        - **Donchian ADX** — 20-day breakout + ADX > 35 + DI confirmation (Enabled)
        - **Keltner RSI + Volume** — Keltner bands + RSI 55/45 + Volume 2.0x avg + ADX > 25 + DI confirmation (Enabled)
        - **Ichimoku Cloud** — TK Cross + Kumo + ADX > 25 (Disabled)

        **Capital & Position Sizing:**
        - Total capital: **Rs. 1,500,000**
        - Margin per position: ~Rs. 200,000 (lot size x price x 15%) + Rs. 50,000 buffer
        - **Max positions per strategy: 2** (total max: 6 across all 3)
        - Position sizing: **3 modes** — Lot (F&O), Capital (cash equity), Options (delta model)
        - Options: ATM=0.5 delta, ITM1=0.75 delta, auto-switch to ATM if <10 days to expiry
        - Universe: **Margin < 200K** (dynamically filtered from live yfinance prices)

        **Risk Parameters:**
        - Stop-loss: **3x daily ATR** (wider swing stop)
        - Trailing SL: **1.5%** from best price (tightened from 3% for higher WR)
        - Hard SL: **Rs. 50,000** per position (tightened from 75K for lower DD)
        - Risk-reward ratio: **1:1.5**
        - No EOD close — positions carry forward multi-day

        **Run Schedule:**
        | Time | Task | Description |
        |------|------|-------------|
        | **08:30** | Stock Selection | Scans yesterday's close for signals across all enabled strategies |
        | **09:15** | Market opens | --- |
        | **09:42** | Live Trader Entry Phase | Reads cached selections, attempts zscore entry on 1-min data |
        | **09:42-10:42** | Z-Score Retry Window | Retries unfilled z-score entries every 5 min |
        | **~10:42-15:15** | Monitor Phase | Every 60 min: checks trailing SL, target hits |
        | **15:15** | End of Session | OPEN positions saved to file (carry forward to next day) |
        | **15:30** | Market closes | --- |

        **Entry Methods:**
        | Mode | Description |
        |------|-------------|
        | zscore (default) | Enter when z-score exceeds 2s (LONG) or -2s (SHORT) relative to first 15 candles. Retries every 5 min. |
        | walk | Enter when price breaks outside the high/low range of first 15 candles |
        | 15m_945 | Enter at exactly 09:45 using the 15-min candle open |

        **Why zscore?** Catches explosive breakout moves, avoids first 15 min noise, self-adapts to each stock's volatility.
        """)

    with tab2:
        st.subheader("Architecture")
        st.markdown("""
        ```
        SwingPortfolio/
        |
        +-- cfg/                      # Configuration
        |   +-- settings.py           #   Global params (capital, margin, risk, SL/trail)
        |   +-- universes.py          #   Symbol lists, lot sizes, margin filter
        |   +-- strategies.json       #   Strategy registry
        |
        +-- core/                     # Shared engine
        |   +-- strategy_base.py      #   Abstract base class for all strategies
        |   +-- registry.py           #   Auto-discovers strategies from strategies.json
        |   +-- data_loader.py        #   yfinance daily downloader with pickle cache
        |   +-- backtest_engine.py    #   Backtest + simulate_today() for live matching
        |   +-- atomic.py             #   Atomic JSON/CSV file I/O
        |   +-- notifications.py      #   Telegram alert sender
        |
        +-- strategies/               # Strategy implementations
        |   +-- donchian_adx.py       #   Donchian 20d + ADX>35 + DI confirmation
        |   +-- keltner_rsi.py        #   Keltner 20/2 + RSI 55/45 + Volume 2.0x + ADX>25 + DI confirmation
        |   +-- supertrend_volume.py  #   ATR Supertrend (p=10,m=3) + volume filter | 78% WR, Sharpe 9.87
        |
        +-- agents/                   # Live agents (Windows Scheduled Tasks)
        |   +-- stock_selection.py    #   08:30 scan
        |   +-- live_trader.py        #   09:42 entry + 60s monitor loop
        |
        +-- reports/                  # Streamlit dashboard pages
        |   +-- dashboard.py          #   Strategy-grouped positions, color-coded P&L
        |   +-- backtest.py           #   Run backtests, Simulate Today button
        |   +-- stock_selection.py    #   Latest scan results
        |   +-- admin.py              #   Risk settings, Telegram, Sheet config
        |   +-- scheduler.py          #   Windows Task Scheduler
        |   +-- about.py              #   This page
        |
        +-- data/                     # Runtime data
        |   +-- selections.json       #   Pre-market scan results
        |   +-- live_positions.json   #   Current open/closed positions
        |   +-- positions_history.json #  All-time closed positions
        |   +-- signals.csv           #   Signal log
        |   +-- telegram_config.json  #   Bot token + chat ID
        |
        +-- cache/                    # Pickled daily OHLCV data
        |   +-- daily_YYYYMM.pkl      #   One file per month
        |
        +-- dashboard.py              # Streamlit entry point
        ```
        """)

    with tab3:
        st.subheader("Configuration")

        st.markdown("""
        ### cfg/settings.py - Global Parameters

        | Parameter | Current | Description |
        |-----------|:-------:|-------------|
        | CAPITAL_TOTAL | 1,500,000 | Total capital across all strategies |
        | MAX_POSITIONS_PER_STRATEGY | 3 | Max concurrent positions per strategy |
        | MAX_POSITIONS_TOTAL | 6 | Max concurrent positions across both strategies |
        | POSITION_SIZING | "lot" | Uses actual NSE F&O lot sizes |
        | MAX_MARGIN_PER_STOCK | 200,000 | Max futures margin per 1-lot position |
        | MARGIN_RATE | 0.15 | Approx margin as % of contract value |
        | TRAIL_PCT | 0.015 (1.5%) | Trailing SL: 1.5% from best price |
        | SL_ATR_MULTIPLIER | 3.0 | Stop-loss = ATR x 3 |
        | RR_RATIO | 1.5 | Risk-reward target ratio |
        | HARD_SL_AMOUNT | 50,000 | Max loss per position (safety net) |
        | HARD_SL_ENABLED | True | Hard SL active |
        | GAP_FILTER_ENABLED | True | Skips entry if gap > 3% |
        | ENTRY_TIME | "zscore" | Entry method |

        **Why these values?**
        - **SL_ATR_MULTIPLIER = 3**: 1.5x the daily range as a stop — wide enough to avoid intraday noise
        - **TRAIL_PCT = 1.5%**: Tightened from 3% — backtest showed +Rs.1.65M combined P&L gain (3%→1.5%) with higher WR (84% vs 71%) and lower DD
        - **HARD_SL = 50,000**: Tightened from 75K — caps max loss per position tighter; ATR-based SL remains primary exit

        ### cfg/strategies.json - Strategy Registry
        Each strategy is a JSON entry. Fields:
        - `id` � Unique identifier (used in logs, files, sheet sync)
        - `module` � Python file in strategies/
        - `class` � Class name (must extend BaseStrategy)
        - `enabled` � true = active, false = skipped
        - `universe` � Stock universe: Nifty 50, Nifty 100, Full F&O, Margin < 200K
        - `entry_time` � Entry method override
        - `params` � Strategy-specific parameters

        ### cfg/universes.py - Symbol Data & Margin Filter
        - **SYMBOLS_DATA**: 215+ NSE F&O symbols with lot sizes
        - **4 presets**: Nifty 50, Nifty 100, Full F&O, Margin < 200K
        - Margin filter excludes stocks where lot_size x price x 0.15 > 200,000
        - Cached in data/margin_universe.json (refreshed if >1 hour old)
        """)

    with tab4:
        st.subheader("Strategies")

        st.markdown("""
        ### 1. Donchian + ADX (Enabled)
        **Concept:** Breakout trading — enters when price breaks out of a 20-day range.

        **Entry LONG:** Close > 20-day high AND ADX > 35 AND +DI > -DI
        **Entry SHORT:** Close < 20-day low AND ADX > 35 AND -DI > +DI

        **Exit:** SL = ATR x 3 from reference, Trail = 1.5% from best, Target = 1:1.5 RR, Hard SL = Rs. 50K

        **Backtest (Nov 2025 - Jun 2026, max 2 positions):**
        | Metric | Value |
        |--------|:-----:|
        | Trades | 118 |
        | Win Rate | 86% |
        | Total P&L | +1,792,510 |
        | Avg Win | +18,485 |
        | Avg Loss | -5,808 |
        | Profit Factor | 20.29 |
        | Sharpe | 11.94 |
        | Max DD | Rs. 50,000 |
        | Avg Hold | 3-4 days |
        | LONG:SHORT | 76:42 |

        ### 2. Keltner + RSI + Volume + ADX (Enabled)
        **Concept:** Momentum entry — enters when price pushes beyond Keltner bands with volume and trend confirmation.

        **Entry LONG:** Close > upper band AND +DI > -DI AND ADX > 25 AND RSI > 55 AND Vol > 2.0x avg
        **Entry SHORT:** Close < lower band AND -DI > +DI AND ADX > 25 AND RSI < 45 AND Vol > 2.0x avg

        **Exit:** Same as Donchian.

        **Backtest (Nov 2025 - Jun 2026, max 2 positions):**
        | Metric | Value |
        |--------|:-----:|
        | Trades | 107 |
        | Win Rate | 80% |
        | Total P&L | +2,328,105 |
        | Avg Win | +30,069 |
        | Avg Loss | -12,279 |
        | Profit Factor | 10.03 |
        | Sharpe | 12.76 |
        | Max DD | Rs. 50,411 |
        | Avg Hold | 3-4 days |
        | LONG:SHORT | 61:46 |

        ### How they complement each other
        - Donchian catches **breakouts** (price breaks 20-day range with strong trend)
        - Keltner catches **momentum with volume** (band breakouts with volume confirmation)
        - Their signals rarely overlap, providing diversification
        - Both now use ADX + DI for trend confirmation and have balanced LONG/SHORT exposure

        **Combined (Nov 2025 - Jun 2026):** 225 trades, 84% WR, +Rs.4,120,615 P&L, Profit Factor 12.75, Sharpe 12.88
        """)

    with tab5:
        st.subheader("Backtest Results")

        st.markdown("""
        ### Donchian ADX - Month-wise (Nov 2025 - Jun 2026)

        | Month | Trades | Wins | Loss | WR% | P&L |
        |-------|:-----:|:---:|:---:|:---:|:---:|
        | Nov | 14 | 9 | 5 | 64% | +67,166 |
        | Dec | 20 | 18 | 2 | 90% | +354,222 |
        | Jan | 16 | 14 | 2 | 88% | +146,266 |
        | Feb | 17 | 16 | 1 | 94% | +238,754 |
        | Mar | 16 | 13 | 3 | 81% | +214,454 |
        | Apr | 13 | 12 | 1 | 92% | +343,528 |
        | May | 17 | 16 | 1 | 94% | +310,989 |
        | Jun | 5 | 4 | 1 | 80% | +117,130 |
        | **Total** | **118** | **102** | **16** | **86%** | **+1,792,510** |

        *Settings: ADX>35, Trail=1.5%, Hard SL=Rs.50K*

        ### Keltner RSI - Month-wise (Nov 2025 - Jun 2026)

        | Month | Trades | Wins | Loss | WR% | P&L |
        |-------|:-----:|:---:|:---:|:---:|:---:|
        | Nov | 11 | 9 | 2 | 82% | +163,937 |
        | Dec | 16 | 11 | 5 | 69% | +452,295 |
        | Jan | 17 | 12 | 5 | 71% | +194,051 |
        | Feb | 15 | 12 | 3 | 80% | +267,809 |
        | Mar | 14 | 12 | 2 | 86% | +209,826 |
        | Apr | 15 | 14 | 1 | 93% | +402,240 |
        | May | 17 | 15 | 2 | 88% | +593,607 |
        | Jun | 2 | 1 | 1 | 50% | +44,341 |
        | **Total** | **107** | **86** | **21** | **80%** | **+2,328,105** |

        *Settings: Vol>2.0x, ADX>25, DI confirmation, RSI fix*

        ### Combined Performance

        | Metric | Value |
        |--------|:-----:|
        | Total Trades | 225 |
        | Win Rate | 84% |
        | Total P&L | +Rs.4,120,615 |
        | Avg Win | +Rs.23,784 |
        | Avg Loss | -Rs.9,481 |
        | Profit Factor | 12.75 |
        | Sharpe Ratio | 12.88 |
        | Max Drawdown | Rs.50,411 |
        | LONG:SHORT | 137:88 |

        ### Key Improvements (June 2026)

        1. **Keltner RSI bug fix**: Off-by-sign error in RSI calculation (was always -inf) prevented LONG signals. Now produces balanced 61:46 LONG:SHORT.
        2. **Tighter trail (3% → 1.5%)**: Captures profits earlier, increased WR from 71% to 84%
        3. **Keltner volume filter (1.5x → 2.0x)**: Higher quality signals, WR 80%
        4. **Donchian ADX threshold (25 → 35)**: Stricter trend filter, fewer but better trades
        5. **Keltner ADX + DI confirmation**: Added for trend validation, matching Donchian logic
        6. **Hard SL cap (75K → 50K)**: Tighter risk control, lower drawdown

        **Result**: P&L increased from +Rs.1.68M to +Rs.4.12M (+145%), while reducing max drawdown.
        """)

    with tab6:
        st.subheader("Deployment")

        tab_a, tab_b, tab_c = st.tabs(["Setup", "Scheduled Tasks", "Google Sheets"])

        with tab_a:
            st.markdown("""
            ### First-time setup
            ```
            cd C:\\ngen26\\SwingPortfolio
            pip install streamlit pandas yfinance plotly requests numpy gspread oauth2client
            streamlit run dashboard.py
            ```

            ### Dashboard pages
            | Page | What it shows |
            |------|---------------|
            | Dashboard | Strategy cards, positions grouped by strategy, color-coded P&L |
            | Stock Selection | Latest 8:30 AM scan per strategy |
            | Backtest | Historical backtests + Simulate Today (1-min intraday) |
            | Admin | Risk, SL/trail, strategies, Telegram, Google Sheet |
            | Scheduler | Deploy/manage Windows Scheduled Tasks |

            ### First-time configuration
            1. Admin > Risk Settings: verify capital, limits, SL/trail
            2. Click "Refresh from yfinance" for margin-filtered universe
            3. Admin > Telegram: enter bot token + chat ID
            4. Admin > Google Sheet: paste Sheet ID, enable, Test Sync
            """)

        with tab_b:
            st.markdown("""
            ### Scheduled Tasks (Windows Task Scheduler)

            | Task | Trigger | Duration | Description |
            |------|---------|----------|-------------|
            | Stock Selection | 08:30 | Instant | Scan yesterday's close, save to selections.json |
            | Live Trader | 09:35 | to 15:15 | Read selections, zscore entry, 60s monitor loop |

            All tasks run Mon-Fri, auto-restart on failure (3 attempts).
            Path: C:\\ngen26\\SwingPortfolio with anaconda3 python.exe
            """)

        with tab_c:
            st.markdown("""
            ### Google Sheets Integration

            5 auto-created tabs:

            | Tab | Direction | Content |
            |-----|-----------|---------|
            | Control | Sheet to Python | Enable/disable strategies, close positions |
            | Positions | Python to Sheet | Open + today's closed (strategy-grouped) |
            | History | Python to Sheet | All-time closed positions |
            | Status | Python to Sheet | Last sync, open count, health |
            | Selections | Python to Sheet | Latest scan results |

            **Control commands:** donchian_adx = ENABLED/DISABLED, close_position = SYMBOL_STRATEGY

            **Setup:** Google Cloud Console > enable Sheets API > service account > share sheet with SA email.
            """)

