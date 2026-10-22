import os
import sys
import json
from datetime import datetime, date, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse
import pandas as pd
from dotenv import load_dotenv

# Suppress OpenBLAS thread overhead on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from news_sentiment_engine import (
    fetch_and_analyze_news,
    get_sector_sentiment_summary,
    load_cached_sentiment,
    LONG_SHORT_PAIRS
)

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_API_SECRET")
IS_PAPER = os.getenv("ALPACA_IS_PAPER", "true").lower() == "true"
PORT = 5000
WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")

if not API_KEY or not API_SECRET:
    print("[Error] Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env")
    sys.exit(1)

trading_client = TradingClient(api_key=API_KEY, secret_key=API_SECRET, paper=IS_PAPER)
data_client = StockHistoricalDataClient(api_key=API_KEY, secret_key=API_SECRET)

DEFAULT_WATCHLIST = [
    {
        "symbol": "SOXL",
        "name": "SOXL (Semiconductor Bull 3X)",
        "sma_period": 20,
        "buffer_pct": 0.005,
    },
    {
        "symbol": "SOXS",
        "name": "SOXS (Semiconductor Bear 3X)",
        "sma_period": 20,
        "buffer_pct": 0.005,
    }
]


def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r") as f:
                data = json.load(f)
                if data and isinstance(data, list):
                    # Normalize items to have 'symbol'
                    normalized = []
                    for item in data:
                        sym = item.get("symbol") or item.get("benchmark") or item.get("bull")
                        if sym:
                            normalized.append({
                                "symbol": sym.upper(),
                                "name": item.get("name", sym.upper()),
                                "sma_period": item.get("sma_period", 20),
                                "buffer_pct": item.get("buffer_pct", 0.005),
                                "bull": item.get("bull"),
                                "bear": item.get("bear"),
                            })
                    return normalized if normalized else DEFAULT_WATCHLIST
        except Exception:
            return DEFAULT_WATCHLIST
    save_watchlist(DEFAULT_WATCHLIST)
    return DEFAULT_WATCHLIST


def save_watchlist(items):
    try:
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(items, f, indent=4)
    except Exception as e:
        print(f"[Error] Failed saving watchlist: {e}")


def fetch_dashboard_data():
    """Fetch complete live account, positions, trades, technical signals, and chart history."""
    now_utc = datetime.now(timezone.utc)
    
    # 1. Account Details
    account = trading_client.get_account()
    portfolio_val = float(account.portfolio_value)
    cash = float(account.cash)
    buying_power = float(account.buying_power)
    last_equity = float(account.last_equity)
    daily_pnl = portfolio_val - last_equity
    daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity > 0 else 0.0

    # 2. Open Positions
    raw_positions = trading_client.get_all_positions()
    positions = []
    for p in raw_positions:
        qty = float(p.qty)
        avg_price = float(p.avg_entry_price)
        current_price = float(p.current_price)
        market_val = float(p.market_value)
        unrealized_pnl = float(p.unrealized_pl)
        unrealized_pnl_pct = float(p.unrealized_plpc) * 100
        positions.append({
            "symbol": p.symbol,
            "qty": qty,
            "avg_price": avg_price,
            "current_price": current_price,
            "market_val": market_val,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "side": p.side.value if hasattr(p.side, 'value') else str(p.side),
        })

    # 3. Today's Executed Trades
    today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
    orders_request = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        after=today_start,
        limit=50
    )
    raw_orders = trading_client.get_orders(orders_request)
    trades = []
    for o in raw_orders:
        created_at = o.created_at.strftime("%H:%M:%S") if o.created_at else "--:--:--"
        filled_at = o.filled_at.strftime("%H:%M:%S") if o.filled_at else "--:--:--"
        filled_qty = float(o.filled_qty) if o.filled_qty else 0.0
        filled_price = float(o.filled_avg_price) if o.filled_avg_price else 0.0
        total_dollars = filled_qty * filled_price
        trades.append({
            "id": str(o.id)[:8],
            "symbol": o.symbol,
            "side": o.side.value.upper() if hasattr(o.side, 'value') else str(o.side).upper(),
            "status": o.status.value.upper() if hasattr(o.status, 'value') else str(o.status).upper(),
            "qty": float(o.qty) if o.qty else filled_qty,
            "filled_qty": filled_qty,
            "filled_price": filled_price,
            "total_dollars": total_dollars,
            "created_at": created_at,
            "filled_at": filled_at,
            "order_type": o.order_type.value if hasattr(o.order_type, 'value') else str(o.order_type)
        })

    # 4. Watchlist & Regime Signals (Batch Query for Instant Loading)
    watchlist_items = load_watchlist()
    watchlist_status = []
    count = max(len(watchlist_items), 1)
    equal_allocation_pct = round(90 / count, 1)

    symbols = [item["symbol"] for item in watchlist_items]
    bars_by_symbol = {}
    try:
        start_date = datetime.now(timezone.utc) - timedelta(days=90)
        bars_req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start_date
        )
        multi_bars_df = data_client.get_stock_bars(bars_req).df
        if not multi_bars_df.empty:
            if isinstance(multi_bars_df.index, pd.MultiIndex):
                for sym, group in multi_bars_df.groupby(level=0):
                    bars_by_symbol[sym] = group
            else:
                bars_by_symbol[symbols[0]] = multi_bars_df
    except Exception:
        pass

    for item in watchlist_items:
        sym = item["symbol"]
        sma_period = item.get("sma_period", 20)
        buffer_pct = item.get("buffer_pct", 0.005)
        
        current_price = None
        sma_val = None
        regime = "CALCULATING"
        distance_pct = 0.0
        
        if sym in bars_by_symbol:
            closes = bars_by_symbol[sym]['close'].values[-sma_period:]
            if len(closes) >= sma_period:
                sma_val = float(closes.mean())
                current_price = float(closes[-1])
                distance_pct = ((current_price - sma_val) / sma_val) * 100
                
                if current_price > sma_val:
                    regime = "BULLISH (BUY / HOLD)"
                elif current_price < (sma_val * (1 - buffer_pct)):
                    regime = "BEARISH (EXIT / CASH)"
                else:
                    regime = "NEUTRAL (HOLD / BUFFER)"
        else:
            regime = "DATA SYNCING"

        watchlist_status.append({
            "symbol": sym,
            "name": item.get("name", sym),
            "current_price": current_price,
            "sma_20": sma_val,
            "regime": regime,
            "distance_pct": distance_pct,
            "allocation": equal_allocation_pct,
            "action": "BUY / HOLD" if "BULLISH" in regime else ("SELL / CASH" if "BEARISH" in regime else "WAIT")
        })

    # 5. Market Open Status
    clock = trading_client.get_clock()
    is_market_open = clock.is_open
    next_open = clock.next_open.strftime("%a %b %d, %I:%M %p %Z") if clock.next_open else "N/A"
    next_close = clock.next_close.strftime("%a %b %d, %I:%M %p %Z") if clock.next_close else "N/A"

    # 6. Portfolio History for Chart
    chart_labels = []
    chart_values = []
    try:
        history = trading_client.get_portfolio_history()
        if history and hasattr(history, 'equity') and history.equity:
            eqs = history.equity[-30:]
            ts = history.timestamp[-30:]
            for t, val in zip(ts, eqs):
                if val is not None:
                    chart_labels.append(datetime.fromtimestamp(t).strftime("%b %d"))
                    chart_values.append(round(float(val), 2))
    except Exception:
        pass

    if not chart_values:
        chart_labels = ["Session Start", "Current"]
        chart_values = [last_equity if last_equity > 0 else portfolio_val, portfolio_val]

    # 7. Real-time News & Sentiment Summary
    news_items = load_cached_sentiment()
    if not news_items:
        try:
            news_items = fetch_and_analyze_news(limit=10)
        except Exception:
            news_items = []
    sector_summary = get_sector_sentiment_summary()

    return {
        "timestamp": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "account": {
            "portfolio_value": portfolio_val,
            "cash": cash,
            "buying_power": buying_power,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "is_paper": IS_PAPER,
            "status": account.status.value if hasattr(account.status, 'value') else str(account.status),
        },
        "market": {
            "is_open": is_market_open,
            "next_open": next_open,
            "next_close": next_close,
        },
        "positions": positions,
        "trades": trades,
        "watchlist": watchlist_status,
        "news": news_items[:8],
        "sector_sentiment": sector_summary,
        "chart": {
            "labels": chart_labels,
            "values": chart_values,
        }
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Alpaca AlgoBot Terminal | Multi-Stock Autonomous Cockpit</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg-dark: #07090e;
      --bg-surface: #0e131d;
      --bg-card: #131926;
      --border: rgba(255, 255, 255, 0.08);
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --green: #10b981;
      --green-glow: rgba(16, 185, 129, 0.2);
      --red: #ef4444;
      --red-glow: rgba(239, 68, 68, 0.2);
      --blue: #3b82f6;
      --amber: #f59e0b;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-dark);
      color: var(--text-main);
      font-family: 'Plus Jakarta Sans', sans-serif;
      min-height: 100vh;
      padding: 24px;
      line-height: 1.5;
    }

    .container {
      max-width: 1420px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 22px;
    }

    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 20px 24px;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      flex-wrap: wrap;
      gap: 16px;
    }
    .brand { display: flex; align-items: center; gap: 16px; }
    .logo-badge {
      width: 48px;
      height: 48px;
      border-radius: 12px;
      background: linear-gradient(135deg, #2563eb, #7c3aed);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 24px;
      box-shadow: 0 0 20px rgba(124, 58, 237, 0.4);
    }
    h1 { font-size: 22px; font-weight: 800; letter-spacing: -0.5px; }
    .subtitle { font-size: 13px; color: var(--text-muted); }
    
    .actions-group {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 14px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.03);
    }
    .pulse { width: 8px; height: 8px; border-radius: 50%; }
    .pill.green .pulse { background: var(--green); box-shadow: 0 0 10px var(--green); }
    .pill.amber .pulse { background: var(--amber); box-shadow: 0 0 10px var(--amber); }

    .btn {
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s ease;
      border: 1px solid transparent;
      outline: none;
    }
    .btn-panic {
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border-color: rgba(239, 68, 68, 0.3);
    }
    .btn-panic:hover { background: #ef4444; color: white; box-shadow: 0 0 16px rgba(239, 68, 68, 0.5); }
    .btn-add {
      background: rgba(59, 130, 246, 0.15);
      color: #60a5fa;
      border-color: rgba(59, 130, 246, 0.3);
    }
    .btn-add:hover { background: #3b82f6; color: white; }
    .btn-refresh { background: #1e293b; color: var(--text-main); border-color: var(--border); }
    .btn-refresh:hover { background: #334155; }

    /* Stats Grid */
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
    }
    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .card-title {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-muted);
    }
    .card-val {
      font-size: 28px;
      font-weight: 800;
      font-family: 'JetBrains Mono', monospace;
    }
    .card-meta { font-size: 12px; font-weight: 500; }
    .positive { color: var(--green); }
    .negative { color: var(--red); }

    /* Chart */
    .chart-container {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px 24px;
    }
    .chart-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }
    .chart-title { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .chart-wrapper { height: 230px; position: relative; width: 100%; }

    /* Panels */
    .panel {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      overflow: hidden;
    }
    .panel-header {
      padding: 18px 24px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .panel-title { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 10px; }

    /* Watchlist Grid */
    .watchlist-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      padding: 20px;
    }
    .watch-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      position: relative;
    }
    .watch-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-right: 20px;
    }
    .watch-symbol {
      font-size: 18px;
      font-weight: 800;
      color: #38bdf8;
      font-family: 'JetBrains Mono', monospace;
    }
    .regime-badge {
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }
    .regime-badge.bull { background: var(--green-glow); color: var(--green); border: 1px solid rgba(16,185,129,0.3); }
    .regime-badge.bear { background: var(--red-glow); color: var(--red); border: 1px solid rgba(239,68,68,0.3); }
    .regime-badge.neutral { background: rgba(245,158,11,0.15); color: var(--amber); border: 1px solid rgba(245,158,11,0.3); }

    .gauge-wrapper { display: flex; flex-direction: column; gap: 6px; }
    .gauge-bar-bg {
      height: 8px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 999px;
      overflow: hidden;
    }
    .gauge-bar-fill { height: 100%; border-radius: 999px; transition: width 0.4s ease; }
    .gauge-bar-fill.bull { background: linear-gradient(90deg, #10b981, #34d399); }
    .gauge-bar-fill.bear { background: linear-gradient(90deg, #ef4444, #f87171); }

    .watch-stats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
    }
    .stat-item {
      background: rgba(0, 0, 0, 0.25);
      padding: 8px 12px;
      border-radius: 8px;
    }
    .stat-label {
      font-size: 10px;
      color: var(--text-muted);
      text-transform: uppercase;
      font-family: 'Plus Jakarta Sans', sans-serif;
    }
    .remove-btn {
      position: absolute;
      top: 14px;
      right: 14px;
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 16px;
      padding: 4px;
    }
    .remove-btn:hover { color: var(--red); }

    /* Tables */
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th {
      text-align: left;
      padding: 14px 20px;
      background: rgba(255, 255, 255, 0.02);
      color: var(--text-muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      border-bottom: 1px solid var(--border);
    }
    td {
      padding: 14px 20px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      font-family: 'JetBrains Mono', monospace;
    }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }
    .tag-buy { color: var(--green); background: var(--green-glow); padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
    .tag-sell { color: var(--red); background: var(--red-glow); padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
    .empty-state { padding: 36px; text-align: center; color: var(--text-muted); font-size: 14px; }

    /* News & Sentiment Pulse */
    .news-card {
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 16px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      transition: all 0.2s ease;
    }
    .news-card:hover { background: rgba(255, 255, 255, 0.04); border-color: rgba(255, 255, 255, 0.15); }
    .news-meta-row { display: flex; justify-content: space-between; align-items: center; font-size: 11px; }
    .news-title { font-size: 13px; font-weight: 600; color: #f8fafc; line-height: 1.4; text-decoration: none; }
    .news-title:hover { color: var(--blue); }
    .sentiment-pill {
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }
    .sentiment-bull { background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }
    .sentiment-bear { background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }
    .sentiment-neut { background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3); }
    .sector-stance-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    /* Simplified Single-Field Modal */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.8);
      backdrop-filter: blur(8px);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }
    .modal {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 28px;
      max-width: 440px;
      width: 100%;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .modal-title { font-size: 18px; font-weight: 700; }
    .modal-desc { font-size: 13px; color: var(--text-muted); line-height: 1.4; }
    .form-group { display: flex; flex-direction: column; gap: 6px; }
    .form-label { font-size: 12px; color: var(--text-muted); font-weight: 600; }
    .form-input {
      background: var(--bg-card);
      border: 1px solid var(--border);
      padding: 12px 14px;
      border-radius: 8px;
      color: white;
      font-size: 16px;
      font-family: 'JetBrains Mono', monospace;
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .form-input:focus { outline: none; border-color: var(--blue); }
    .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px; }

    #toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #1e293b;
      color: white;
      padding: 14px 22px;
      border-radius: 10px;
      border: 1px solid var(--border);
      font-size: 14px;
      font-weight: 600;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      display: none;
      z-index: 2000;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="logo-badge">⚡</div>
        <div>
          <h1>Autonomous Algo-Trader Cockpit</h1>
          <div class="subtitle">Multi-Stock Trend Evaluation & Execution Engine</div>
        </div>
      </div>
      <div class="actions-group">
        <div id="market-pill" class="pill amber">
          <span class="pulse"></span>
          <span id="market-status-text">Market Loading...</span>
        </div>
        <div class="pill">
          <span>Mode:</span>
          <strong id="account-mode">PAPER</strong>
        </div>
        <button class="btn btn-add" onclick="openAddModal()">
          <span>+</span> Add Stock Ticker
        </button>
        <button class="btn btn-panic" onclick="triggerLiquidateAll()">
          <span>🚨</span> Liquidate to Cash
        </button>
        <button class="btn btn-refresh" onclick="fetchData()">
          <span>↻</span>
        </button>
      </div>
    </header>

    <!-- Top Account Cards -->
    <div class="stats-grid">
      <div class="card">
        <div class="card-title">Portfolio Equity</div>
        <div class="card-val" id="val-portfolio">$0.00</div>
        <div class="card-meta" id="val-daily-pnl">Today: $0.00 (0.00%)</div>
      </div>
      <div class="card">
        <div class="card-title">Buying Power</div>
        <div class="card-val" id="val-buying-power">$0.00</div>
        <div class="card-meta" style="color: var(--text-muted)">Alpaca Margin Capacity</div>
      </div>
      <div class="card">
        <div class="card-title">Liquid Cash Reserve</div>
        <div class="card-val" id="val-cash">$0.00</div>
        <div class="card-meta" style="color: var(--text-muted)">Safety Buffer (10%)</div>
      </div>
      <div class="card">
        <div class="card-title">Market Schedule</div>
        <div class="card-val" style="font-size: 16px; line-height: 1.4" id="val-next-market">Checking...</div>
        <div class="card-meta" id="val-timestamp">Synced: --:--:--</div>
      </div>
    </div>

    <!-- Live Equity Performance Chart -->
    <div class="chart-container">
      <div class="chart-header">
        <div class="chart-title">
          <span>📈</span> Portfolio Equity Curve
        </div>
        <div style="font-size: 12px; color: var(--text-muted)">Account Equity over time</div>
      </div>
      <div class="chart-wrapper">
        <canvas id="equityChart"></canvas>
      </div>
    </div>

    <!-- Live News Sentiment Pulse & Sector Signals -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">
          <span>📰</span> Live Breaking News & Sector Sentiment Signals (Long / Short Switcher)
        </div>
        <div style="display: flex; gap: 10px; align-items: center;">
          <span style="font-size: 12px; color: var(--text-muted)">Alpaca Live Stream + VADER Sentiment Engine</span>
          <button class="btn btn-refresh" style="padding: 4px 12px; font-size: 11px; height: auto;" onclick="refreshNewsData()">
            <span>↻ Refresh News</span>
          </button>
        </div>
      </div>
      
      <!-- Sector stance pills -->
      <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px;">Sector Trend Stances & Target ETFs</div>
      <div id="sector-signals-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-bottom: 20px;">
        <!-- Dynamically rendered -->
      </div>

      <!-- Breaking Headlines Feed -->
      <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px;">Breaking Market Headlines & AI Signals</div>
      <div id="news-feed-list" style="display: flex; flex-direction: column; gap: 8px;">
        <!-- Dynamically rendered -->
      </div>
    </div>

    <!-- Active Watchlist -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">
          <span>🎯</span> Active Stocks Watched by Bot (Checking Prices to Buy / Sell)
        </div>
        <div style="font-size: 12px; color: var(--text-muted)">Automatic 20-Day SMA Trend Detection</div>
      </div>
      <div class="watchlist-grid" id="watchlist-cards">
        <!-- Dynamically rendered -->
      </div>
    </div>

    <!-- Current Positions -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">
          <span>💼</span> Currently Held Positions
        </div>
        <div id="positions-count" style="font-size: 12px; color: var(--text-muted)">0 Active Positions</div>
      </div>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Shares</th>
            <th>Avg Entry Price</th>
            <th>Current Price</th>
            <th>Market Value</th>
            <th>Unrealized PnL</th>
          </tr>
        </thead>
        <tbody id="positions-table-body">
          <tr><td colspan="7" class="empty-state">No open positions. Capital preserved in cash.</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Today's Executed Trades -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">
          <span>📜</span> Today's Executed Trades (Bought & Sold Today)
        </div>
        <div id="trades-count" style="font-size: 12px; color: var(--text-muted)">0 Orders Today</div>
      </div>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>Action</th>
            <th>Shares Filled</th>
            <th>Exec Price</th>
            <th>Total Dollars</th>
            <th>Order Status</th>
          </tr>
        </thead>
        <tbody id="trades-table-body">
          <tr><td colspan="7" class="empty-state">No trades executed today yet.</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Simplified Single-Field Modal -->
  <div class="modal-overlay" id="add-modal">
    <div class="modal">
      <div class="modal-title">+ Add Stock / ETF Ticker</div>
      <div class="modal-desc">
        Enter any US stock or ETF ticker symbol. The bot will automatically track its 20-day SMA, buy when trending up, and sell to cash when trending down.
      </div>
      <div class="form-group">
        <label class="form-label">Stock Ticker Symbol</label>
        <input class="form-input" id="input-ticker" placeholder="e.g. SOXL, NVDA, AAPL, QQQ, TSLA" autofocus />
      </div>
      <div class="modal-actions">
        <button class="btn btn-refresh" onclick="closeAddModal()">Cancel</button>
        <button class="btn btn-add" onclick="submitNewTicker()">+ Start Tracking</button>
      </div>
    </div>
  </div>

  <div id="toast"></div>

  <script>
    let equityChart = null;

    function showToast(msg) {
      const toast = document.getElementById('toast');
      toast.textContent = msg;
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 4000);
    }

    function fmtDollar(num) {
      if (num === null || num === undefined) return "$0.00";
      return "$" + Number(num).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function initChart(labels, values) {
      const ctx = document.getElementById('equityChart').getContext('2d');
      const gradient = ctx.createLinearGradient(0, 0, 0, 230);
      gradient.addColorStop(0, 'rgba(59, 130, 246, 0.35)');
      gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');

      equityChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Portfolio Equity ($)',
            data: values,
            borderColor: '#3b82f6',
            borderWidth: 2.5,
            fill: true,
            backgroundColor: gradient,
            tension: 0.35,
            pointRadius: 3,
            pointBackgroundColor: '#3b82f6'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#0e131d',
              borderColor: 'rgba(255,255,255,0.1)',
              borderWidth: 1,
              titleColor: '#94a3b8',
              bodyColor: '#f1f5f9',
              callbacks: {
                label: (ctx) => ' Equity: ' + fmtDollar(ctx.parsed.y)
              }
            }
          },
          scales: {
            x: {
              grid: { color: 'rgba(255, 255, 255, 0.03)' },
              ticks: { color: '#64748b', font: { family: 'Plus Jakarta Sans', size: 11 } }
            },
            y: {
              grid: { color: 'rgba(255, 255, 255, 0.04)' },
              ticks: {
                color: '#64748b',
                font: { family: 'JetBrains Mono', size: 11 },
                callback: (val) => fmtDollar(val)
              }
            }
          }
        }
      });
    }

    async function fetchData() {
      try {
        const res = await fetch('/api/data');
        const d = await res.json();

        // Account stats
        document.getElementById('val-portfolio').textContent = fmtDollar(d.account.portfolio_value);
        document.getElementById('val-cash').textContent = fmtDollar(d.account.cash);
        document.getElementById('val-buying-power').textContent = fmtDollar(d.account.buying_power);
        document.getElementById('account-mode').textContent = d.account.is_paper ? 'PAPER' : 'LIVE';

        const pnlEl = document.getElementById('val-daily-pnl');
        const pnlSign = d.account.daily_pnl >= 0 ? '+' : '';
        pnlEl.textContent = `Today: ${pnlSign}${fmtDollar(d.account.daily_pnl)} (${pnlSign}${d.account.daily_pnl_pct.toFixed(2)}%)`;
        pnlEl.className = 'card-meta ' + (d.account.daily_pnl >= 0 ? 'positive' : 'negative');

        // Market Schedule
        const marketPill = document.getElementById('market-pill');
        const marketText = document.getElementById('market-status-text');
        const nextMarket = document.getElementById('val-next-market');
        document.getElementById('val-timestamp').textContent = `Synced: ${d.timestamp}`;

        if (d.market.is_open) {
          marketPill.className = 'pill green';
          marketText.textContent = 'Market OPEN (Trading Active)';
          nextMarket.textContent = `Closes: ${d.market.next_close}`;
        } else {
          marketPill.className = 'pill amber';
          marketText.textContent = 'Market CLOSED (Bot Sleeping)';
          nextMarket.textContent = `Next Open: ${d.market.next_open}`;
        }

        // Chart
        if (d.chart && d.chart.values.length > 0) {
          if (!equityChart) {
            initChart(d.chart.labels, d.chart.values);
          } else {
            equityChart.data.labels = d.chart.labels;
            equityChart.data.datasets[0].data = d.chart.values;
            equityChart.update('none');
          }
        }

        // Watchlist Cards
        const watchContainer = document.getElementById('watchlist-cards');
        watchContainer.innerHTML = '';
        d.watchlist.forEach((w) => {
          let badgeClass = 'neutral';
          let gaugeClass = 'bull';
          let fillPct = Math.min(Math.max((w.distance_pct + 5) * 10, 5), 100);

          if (w.regime.includes('BULLISH')) {
            badgeClass = 'bull';
            gaugeClass = 'bull';
          } else if (w.regime.includes('BEARISH')) {
            badgeClass = 'bear';
            gaugeClass = 'bear';
            fillPct = Math.min(Math.max(Math.abs(w.distance_pct) * 10, 5), 100);
          }

          const card = document.createElement('div');
          card.className = 'watch-card';
          card.innerHTML = `
            <button class="remove-btn" title="Remove ticker" onclick="removeTicker('${w.symbol}')">✕</button>
            <div class="watch-top">
              <div class="watch-symbol">${w.symbol}</div>
              <span class="regime-badge ${badgeClass}">${w.action}</span>
            </div>
            
            <div class="gauge-wrapper">
              <div style="display:flex; justify-content:space-between; font-size:11px; color:#94a3b8">
                <span>Trend vs 20-Day SMA</span>
                <span class="${w.distance_pct >= 0 ? 'positive' : 'negative'}">
                  ${w.distance_pct >= 0 ? '+' : ''}${w.distance_pct.toFixed(2)}%
                </span>
              </div>
              <div class="gauge-bar-bg">
                <div class="gauge-bar-fill ${gaugeClass}" style="width: ${fillPct}%"></div>
              </div>
            </div>

            <div class="watch-stats">
              <div class="stat-item">
                <div class="stat-label">Current Price</div>
                <div>${fmtDollar(w.current_price)}</div>
              </div>
              <div class="stat-item">
                <div class="stat-label">20-Day SMA</div>
                <div>${fmtDollar(w.sma_20)}</div>
              </div>
              <div class="stat-item">
                <div class="stat-label">Bot Regime</div>
                <div style="font-size:11px; font-weight:700; color:#38bdf8">${w.regime}</div>
              </div>
              <div class="stat-item">
                <div class="stat-label">Target Allocation</div>
                <div>${w.allocation}%</div>
              </div>
            </div>
          `;
          watchContainer.appendChild(card);
        });

        // Positions Table
        const posBody = document.getElementById('positions-table-body');
        document.getElementById('positions-count').textContent = `${d.positions.length} Active Position(s)`;
        if (d.positions.length === 0) {
          posBody.innerHTML = `<tr><td colspan="7" class="empty-state">No open positions currently. Capital preserved in cash.</td></tr>`;
        } else {
          posBody.innerHTML = d.positions.map(p => {
            const pnlClass = p.unrealized_pnl >= 0 ? 'positive' : 'negative';
            const pnlSign = p.unrealized_pnl >= 0 ? '+' : '';
            return `
              <tr>
                <td><strong style="color: #38bdf8">${p.symbol}</strong></td>
                <td><span style="text-transform: uppercase">${p.side}</span></td>
                <td>${p.qty.toFixed(2)}</td>
                <td>${fmtDollar(p.avg_price)}</td>
                <td>${fmtDollar(p.current_price)}</td>
                <td>${fmtDollar(p.market_val)}</td>
                <td class="${pnlClass}">${pnlSign}${fmtDollar(p.unrealized_pnl)} (${pnlSign}${p.unrealized_pnl_pct.toFixed(2)}%)</td>
              </tr>
            `;
          }).join('');
        }

        // Trades Table
        const tradesBody = document.getElementById('trades-table-body');
        document.getElementById('trades-count').textContent = `${d.trades.length} Order(s) Executed Today`;
        if (d.trades.length === 0) {
          tradesBody.innerHTML = `<tr><td colspan="7" class="empty-state">No trades executed today yet.</td></tr>`;
        } else {
          tradesBody.innerHTML = d.trades.map(t => {
            const tagClass = t.side === 'BUY' ? 'tag-buy' : 'tag-sell';
            return `
              <tr>
                <td>${t.filled_at !== '--:--:--' ? t.filled_at : t.created_at}</td>
                <td><strong>${t.symbol}</strong></td>
                <td><span class="${tagClass}">${t.side}</span></td>
                <td>${t.filled_qty > 0 ? t.filled_qty.toFixed(2) : t.qty.toFixed(2)}</td>
                <td>${t.filled_price > 0 ? fmtDollar(t.filled_price) : 'Pending'}</td>
                <td>${t.total_dollars > 0 ? fmtDollar(t.total_dollars) : '--'}</td>
                <td><span style="font-size: 11px; opacity: 0.8">${t.status}</span></td>
              </tr>
            `;
          }).join('');
        }

        // Sector Sentiment Signals
        const sectorGrid = document.getElementById('sector-signals-grid');
        if (d.sector_sentiment && Object.keys(d.sector_sentiment).length > 0) {
          sectorGrid.innerHTML = Object.entries(d.sector_sentiment).map(([sym, sec]) => {
            let pillClass = 'sentiment-neut';
            let icon = '⚪';
            if (sec.stance === 'BULLISH') {
              pillClass = 'sentiment-bull';
              icon = '🟢';
            } else if (sec.stance === 'BEARISH') {
              pillClass = 'sentiment-bear';
              icon = '🔴';
            }
            return `
              <div class="sector-stance-card">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <strong style="font-size:12px; color:#f8fafc">${sec.sector}</strong>
                  <span class="sentiment-pill ${pillClass}">${icon} ${sec.stance}</span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:11px; color:#94a3b8; font-family:'JetBrains Mono',monospace;">
                  <span>Pair: <strong>${sec.bull_etf}</strong> / <strong>${sec.bear_etf}</strong></span>
                  <span>Score: ${sec.sentiment_score >= 0 ? '+' : ''}${sec.sentiment_score.toFixed(3)}</span>
                </div>
                <div style="font-size:11px; margin-top:2px; font-weight:700; color:${sec.stance === 'BEARISH' ? '#ef4444' : '#10b981'}">
                  🎯 Target: ${sec.target_etf} (${sec.stance === 'BEARISH' ? 'SHORT / HEDGE' : 'LONG'})
                </div>
              </div>
            `;
          }).join('');
        }

        // Breaking News Feed
        const newsList = document.getElementById('news-feed-list');
        if (d.news && d.news.length > 0) {
          newsList.innerHTML = d.news.map(n => {
            const sent = n.sentiment || {};
            let pillClass = 'sentiment-neut';
            let icon = '⚪';
            if (sent.label === 'BULLISH') {
              pillClass = 'sentiment-bull';
              icon = '🟢';
            } else if (sent.label === 'BEARISH') {
              pillClass = 'sentiment-bear';
              icon = '🔴';
            }
            const symbolsPills = (n.symbols || []).slice(0, 4).map(s => `<span style="background:rgba(255,255,255,0.06); padding:1px 6px; border-radius:4px; font-size:10px; font-weight:700; color:#38bdf8">${s}</span>`).join(' ');
            const rec = n.pair_recommendation ? `<span style="font-size:11px; font-weight:700; color:${sent.label === 'BEARISH' ? '#ef4444' : '#10b981'}">⚡ Recommends: ${sent.label === 'BEARISH' ? 'BUY SHORT ' + n.pair_recommendation.bear : 'BUY LONG ' + n.pair_recommendation.bull}</span>` : '';
            return `
              <div class="news-card">
                <div class="news-meta-row">
                  <div style="display:flex; gap:6px; align-items:center;">
                    <span style="color:#94a3b8; font-size:11px">${n.created_at || 'Just Now'}</span>
                    <span style="color:#64748b">•</span>
                    <span style="color:#64748b; font-size:11px">${n.source || 'Alpaca Wire'}</span>
                    ${symbolsPills}
                  </div>
                  <span class="sentiment-pill ${pillClass}">${icon} ${sent.label} (${sent.compound >= 0 ? '+' : ''}${sent.compound || 0.0})</span>
                </div>
                <a href="${n.url || '#'}" target="_blank" class="news-title">${n.headline}</a>
                <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px;">
                  <span style="color:#94a3b8">${n.summary ? n.summary.slice(0, 160) + '...' : ''}</span>
                  ${rec}
                </div>
              </div>
            `;
          }).join('');
        }

      } catch (err) {
        console.error("Dashboard Sync Error:", err);
      }
    }

    // Refresh News Data
    async function refreshNewsData() {
      showToast("Fetching breaking market news & analyzing sentiment...");
      try {
        const res = await fetch('/api/refresh_news', { method: 'POST' });
        const data = await res.json();
        showToast("News sentiment pulse updated!");
        fetchData();
      } catch (e) {
        showToast("Error refreshing news: " + e);
      }
    }

    // Emergency Liquidate All
    async function triggerLiquidateAll() {
      if (!confirm("⚠️ EMERGENCY LIQUIDATION: Are you sure you want to close ALL positions and cancel all open orders immediately?")) {
        return;
      }
      try {
        const res = await fetch('/api/liquidate_all', { method: 'POST' });
        const data = await res.json();
        showToast(data.message || "Positions closed!");
        fetchData();
      } catch (e) {
        showToast("Error triggering liquidation: " + e);
      }
    }

    // Modal controls
    function openAddModal() {
      document.getElementById('add-modal').style.display = 'flex';
      document.getElementById('input-ticker').focus();
    }
    function closeAddModal() {
      document.getElementById('add-modal').style.display = 'none';
      document.getElementById('input-ticker').value = '';
    }

    async function submitNewTicker() {
      const ticker = document.getElementById('input-ticker').value.trim().toUpperCase();
      if (!ticker) {
        alert("Please enter a ticker symbol.");
        return;
      }

      try {
        const res = await fetch('/api/add_ticker', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol: ticker })
        });
        const data = await res.json();
        closeAddModal();
        showToast(data.message || `Started tracking ${ticker}!`);
        fetchData();
      } catch (e) {
        showToast("Failed adding ticker: " + e);
      }
    }

    async function removeTicker(sym) {
      if (!confirm(`Stop tracking and remove "${sym}"?`)) return;
      try {
        const res = await fetch('/api/remove_ticker', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol: sym })
        });
        const data = await res.json();
        showToast(data.message || `Removed ${sym}!`);
        fetchData();
      } catch (e) {
        showToast("Failed removing ticker: " + e);
      }
    }

    fetchData();
    setInterval(fetchData, 5000);
  </script>
</body>
</html>
"""


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif parsed.path == "/api/data":
            try:
                data = fetch_dashboard_data()
                self._send_json(200, data)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        elif parsed.path == "/api/news":
            try:
                items = load_cached_sentiment()
                if not items:
                    items = fetch_and_analyze_news(limit=12)
                summary = get_sector_sentiment_summary()
                self._send_json(200, {"news": items, "sectors": summary})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        if parsed.path == "/api/refresh_news":
            try:
                items = fetch_and_analyze_news(limit=15)
                summary = get_sector_sentiment_summary()
                self._send_json(200, {"success": True, "news": items, "sectors": summary})
            except Exception as e:
                self._send_json(500, {"success": False, "error": str(e)})

        elif parsed.path == "/api/liquidate_all":
            try:
                trading_client.close_all_positions(cancel_orders=True)
                resp = {"success": True, "message": "All positions liquidated & open orders canceled."}
                self._send_json(200, resp)
            except Exception as e:
                self._send_json(500, {"success": False, "message": str(e)})

        elif parsed.path in ("/api/add_ticker", "/api/add_pair"):
            try:
                sym = payload.get("symbol") or payload.get("bull") or payload.get("benchmark")
                if not sym:
                    self._send_json(400, {"success": False, "message": "Missing ticker symbol."})
                    return
                sym = sym.strip().upper()

                items = load_watchlist()
                if any(i.get("symbol") == sym for i in items):
                    self._send_json(400, {"success": False, "message": f"{sym} is already on your watchlist."})
                    return

                items.append({
                    "symbol": sym,
                    "name": sym,
                    "sma_period": 20,
                    "buffer_pct": 0.005,
                })
                save_watchlist(items)
                self._send_json(200, {"success": True, "message": f"Added {sym} to bot watchlist!"})
            except Exception as e:
                self._send_json(500, {"success": False, "message": str(e)})

        elif parsed.path in ("/api/remove_ticker", "/api/remove_pair"):
            try:
                sym = (payload.get("symbol") or payload.get("name") or "").strip().upper()
                items = load_watchlist()
                new_items = [i for i in items if i.get("symbol") != sym and i.get("name") != sym]
                save_watchlist(new_items)
                self._send_json(200, {"success": True, "message": f"Removed {sym} from watchlist."})
            except Exception as e:
                self._send_json(500, {"success": False, "message": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, status, obj):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))

    def log_message(self, format, *args):
        return


def start_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, DashboardRequestHandler)
    print("=" * 65)
    print(f" ALGO-BOT COCKPIT ACTIVE ON: http://localhost:{PORT}")
    print(f" Open http://localhost:{PORT} in your browser to view & trade.")
    print("=" * 65)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dashboard] Shutting down...")
        httpd.server_close()


if __name__ == "__main__":
    start_server()
