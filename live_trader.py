import os
import sys
import pandas as pd
from dotenv import load_dotenv

# Suppress OpenBLAS memory allocation overhead on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

load_dotenv()

from lumibot.brokers import Alpaca
from lumibot.strategies import Strategy
from lumibot.traders import Trader
from news_sentiment_engine import (
    fetch_and_analyze_news,
    get_sector_sentiment_summary,
    LONG_SHORT_PAIRS,
    BEAR_TO_BULL
)

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")


def get_active_pairs():
    """Load pairs dynamically from watchlist.json (synced with Web UI)."""
    if os.path.exists(WATCHLIST_FILE):
        try:
            import json
            with open(WATCHLIST_FILE, "r") as f:
                data = json.load(f)
                if data:
                    return data
        except Exception:
            pass
    return [
        {
            "name": "Semiconductors 3X",
            "benchmark": "SOXL",
            "bull": "SOXL",
            "bear": "SOXS",
            "sma_period": 20,
            "buffer_pct": 0.005,
            "allocation": 0.90,
        }
    ]


class MultiPairLongShortLive(Strategy):
    """
    Institutional Champion Momentum & Profit-Locking Engine.
    
    1. Scans the full watchlist (36+ high-beta leveraged ETFs & stocks).
    2. Filters for tickers trading above their 20-day SMA (confirmed bull trend).
    3. Ranks candidates by 10-day Rate of Change (ROC) momentum.
    4. Concentrates capital into the TOP 3 strongest champions (28% allocation each).
    5. Locks in gains with a 4.0% Trailing Stop-Loss from peak price.
    6. Protects capital with a 3.5% Hard Stop-Loss against adverse moves.
    7. Exits to cash if price breaks below the 20-day SMA.
    """
    BEAR_SYMBOLS = {
        "SOXS", "MSTZ", "BTCZ", "CONI", "NVD", "MUZ", "SQQQ",
        "TSLQ", "PLTD", "DRIP", "SCO", "KOLD", "GDXD", "RKLZ"
    }

    parameters = {
        "max_positions": 3,          # Hold top 3 strongest leaders at any time
        "per_trade_alloc": 0.28,      # 28% per trade (84% total deployed, 16% cash reserve)
        "sma_period": 20,            # 20-day trend confirmation
        "momentum_lookback": 10,     # 10-day Rate of Change ranking
        "trailing_stop_pct": 0.04,   # 4% winning trailing stop to lock in profits near peak
        "hard_stop_pct": 0.035,      # 3.5% hard stop-loss to quickly cut adverse trades
        "buffer_pct": 0.005,         # 0.5% buffer against false breakdown
    }

    def initialize(self):
        # Poll market data every 1 minute during regular trading hours
        self.sleeptime = "1M"
        # Track peak prices for active positions to manage trailing stop-losses
        self.peak_prices = {}
        self.entry_prices = {}
        self.log_message("[System] MultiPairLongShortLive initialized. Champion Momentum & Trailing Stop Engine Active.")

    def on_trading_iteration(self):
        portfolio_value = self.get_portfolio_value()
        if portfolio_value is None or portfolio_value <= 0:
            return

        active_items = get_active_pairs()
        if not active_items:
            return

        # --------------------------------------------------------------
        # 0. MACRO CASH SHIELD: Dodge Broad Market Corrections
        # --------------------------------------------------------------
        is_bear_correction = False
        try:
            spy_bars = self.get_historical_prices("SPY", 50, "day")
            spy_price = self.get_last_price("SPY")
            if spy_bars is not None and len(spy_bars.df) >= 50 and spy_price is not None:
                spy_sma50 = float(spy_bars.df["close"].mean())
                if spy_price < spy_sma50:
                    is_bear_correction = True
        except Exception:
            is_bear_correction = False

        if is_bear_correction:
            held_positions = self.get_positions()
            if held_positions:
                self.log_message("🛡️ [MACRO CASH SHIELD] SPY below 50-day SMA! Moving to 100% Cash to dodge correction.")
                for pos in held_positions:
                    self.close_position(pos.symbol)
                self.peak_prices.clear()
                self.entry_prices.clear()
            return  # Sit safely in 100% cash during broad market corrections

        # --------------------------------------------------------------
        # 1. NEWS SENTIMENT ANALYSIS: Real-time News Polling & Stance
        # --------------------------------------------------------------
        try:
            fetch_and_analyze_news(limit=15)
            sector_sentiment = get_sector_sentiment_summary()
        except Exception:
            sector_sentiment = {}

        # --------------------------------------------------------------
        # 2. RISK MANAGEMENT: Dynamic Trailing Stops & Breaking News Exits
        # --------------------------------------------------------------
        self._manage_open_positions(portfolio_value, sector_sentiment)

        # --------------------------------------------------------------
        # 3. SCAN & RANK: Identify Top Momentum Champions above 20-Day SMA
        # --------------------------------------------------------------
        champions = self._scan_and_rank_champions(active_items, sector_sentiment)

        # --------------------------------------------------------------
        # 4. EXECUTE ROTATION: Align Portfolio with Top Champions
        # --------------------------------------------------------------
        self._rebalance_champions(champions, portfolio_value)

    def _manage_open_positions(self, portfolio_value: float, sector_sentiment: dict):
        """Monitor held positions for trailing stop profit-locks, hard stops, and breaking news signals."""
        held_positions = self.get_positions()
        if not held_positions:
            self.peak_prices.clear()
            self.entry_prices.clear()
            return

        trailing_stop_pct = self.parameters["trailing_stop_pct"]
        hard_stop_pct = self.parameters["hard_stop_pct"]
        sma_period = self.parameters["sma_period"]
        buffer_pct = self.parameters["buffer_pct"]

        for pos in held_positions:
            sym = pos.symbol
            current_price = self.get_last_price(sym)
            if not current_price or current_price <= 0:
                continue

            # Record entry price
            if sym not in self.entry_prices:
                self.entry_prices[sym] = float(pos.avg_price) if hasattr(pos, 'avg_price') and pos.avg_price else current_price

            # Update peak price (high-water mark)
            if sym not in self.peak_prices:
                self.peak_prices[sym] = current_price
            else:
                self.peak_prices[sym] = max(self.peak_prices[sym], current_price)

            peak = self.peak_prices[sym]
            entry = self.entry_prices[sym]
            gain_pct = (current_price - entry) / entry if entry > 0 else 0

            # Profit-Tier Ratchet: Tighten stop distance as gains expand
            dynamic_stop_pct = self._calculate_dynamic_atr_stop(sym, current_price)
            if gain_pct >= 0.35:
                active_stop_pct = min(dynamic_stop_pct, 0.020)  # 2.0% parabolic lock
                tier_name = "PARABOLIC 2% LOCK"
            elif gain_pct >= 0.15:
                active_stop_pct = min(dynamic_stop_pct, 0.030)  # 3.0% profit ratchet
                tier_name = "PROFIT RATCHET 3%"
            else:
                active_stop_pct = dynamic_stop_pct
                tier_name = "BASE ATR"

            # A. Trailing Stop Check (Lock in profits)
            drawdown_from_peak = (current_price - peak) / peak
            if drawdown_from_peak <= -active_stop_pct:
                pnl_dollars = (current_price - entry) * float(pos.quantity)
                self.close_position(sym)
                self.log_message(
                    f"🎯 [{tier_name}] EXITED {sym} @ ${current_price:.2f} | "
                    f"Peak was ${peak:.2f} (-{abs(drawdown_from_peak)*100:.1f}% vs {active_stop_pct*100:.1f}%) | "
                    f"Total Gain: +{gain_pct*100:.1f}% | Est PnL: ${pnl_dollars:.2f}"
                )
                self.peak_prices.pop(sym, None)
                self.entry_prices.pop(sym, None)
                continue

            # B. Hard Stop-Loss Check (Cut fast losses)
            loss_from_entry = (current_price - entry) / entry
            if loss_from_entry <= -hard_stop_pct:
                self.close_position(sym)
                self.log_message(
                    f"🛑 [HARD STOP-LOSS] CUT LOSS on {sym} @ ${current_price:.2f} | "
                    f"Loss: {loss_from_entry*100:.1f}% (limit: -{hard_stop_pct*100:.1f}%)"
                )
                self.peak_prices.pop(sym, None)
                self.entry_prices.pop(sym, None)
                continue

            # C. Trend Breakdown Check (Price dropped below 20-day SMA)
            bars = self.get_historical_prices(sym, sma_period, "day")
            if bars is not None and len(bars.df) >= sma_period:
                sma = float(bars.df["close"].mean())
                if current_price < (sma * (1 - buffer_pct)):
                    self.close_position(sym)
                    self.log_message(
                        f"📉 [TREND BREAKDOWN] EXITED {sym} @ ${current_price:.2f} | "
                        f"Price dropped below 20-day SMA (${sma:.2f})"
                    )
                    self.peak_prices.pop(sym, None)
                    self.entry_prices.pop(sym, None)
                    continue

            # D. Real-Time News Sentiment Exit & Inverse Pair Switch
            if sym in LONG_SHORT_PAIRS:
                pair_data = sector_sentiment.get(sym, {})
                if pair_data.get("stance") == "BEARISH" and pair_data.get("sentiment_score", 0) <= -0.18:
                    self.close_position(sym)
                    self.log_message(
                        f"🚨 [NEWS REVERSAL EXIT] EXITED {sym} @ ${current_price:.2f} | "
                        f"Breaking negative sentiment detected ({pair_data.get('sentiment_score')})"
                    )
                    bear_sym = pair_data.get("bear_etf")
                    if bear_sym:
                        bear_price = self.get_last_price(bear_sym)
                        if bear_price and bear_price > 0:
                            budget = portfolio_value * self.parameters["per_trade_alloc"]
                            qty = int(budget // bear_price)
                            if qty > 0:
                                order = self.create_order(bear_sym, qty, "buy")
                                self.submit_order(order)
                                self.entry_prices[bear_sym] = bear_price
                                self.peak_prices[bear_sym] = bear_price
                                self.log_message(f"🐻 [INVERSE SHORT ENTRY] BOUGHT {qty} {bear_sym} @ ${bear_price:.2f} to profit on the downside!")
                    self.peak_prices.pop(sym, None)
                    self.entry_prices.pop(sym, None)
                    continue

            # If holding a Bear ETF and sector turns Bullish again
            if sym in BEAR_TO_BULL:
                bull_sym = BEAR_TO_BULL[sym]
                pair_data = sector_sentiment.get(bull_sym, {})
                if pair_data.get("stance") == "BULLISH" and pair_data.get("sentiment_score", 0) >= 0.15:
                    self.close_position(sym)
                    self.log_message(
                        f"🟢 [NEWS REVERSAL] Liquidated Bear Hedge {sym} @ ${current_price:.2f} as {bull_sym} sentiment turned Bullish (+{pair_data.get('sentiment_score')})"
                    )
                    self.peak_prices.pop(sym, None)
                    self.entry_prices.pop(sym, None)
                    continue

    def _calculate_dynamic_atr_stop(self, sym: str, current_price: float) -> float:
        """
        Calculate dynamic trailing stop percentage based on 14-day Average True Range (ATR).
        - Multiplier: 1.8x daily ATR
        - Clamped between 3.5% (tight minimum) and 6.0% (volatility ceiling).
        """
        base_stop = self.parameters.get("trailing_stop_pct", 0.04)
        try:
            bars = self.get_historical_prices(sym, 16, "day")
            if bars is None or len(bars.df) < 15:
                return base_stop

            df = bars.df
            if not {"high", "low", "close"}.issubset(df.columns):
                return base_stop

            prev_close = df["close"].shift(1)
            tr1 = df["high"] - df["low"]
            tr2 = (df["high"] - prev_close).abs()
            tr3 = (df["low"] - prev_close).abs()
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_14 = float(true_range.iloc[-14:].mean())

            if atr_14 <= 0 or current_price <= 0:
                return base_stop

            atr_pct = (1.8 * atr_14) / current_price
            return max(0.035, min(0.060, atr_pct))
        except Exception:
            return base_stop

    def _scan_and_rank_champions(self, active_items: list, sector_sentiment: dict = None) -> list:
        """Scan all watchlist candidates and rank them by 10-day Rate of Change."""
        candidates = []
        sma_period = self.parameters["sma_period"]
        mom_days = self.parameters["momentum_lookback"]
        req_bars = max(sma_period, mom_days + 1)

        # Macro Regime Check (SPY 50-day SMA)
        is_bear_market = False
        try:
            spy_bars = self.get_historical_prices("SPY", 50, "day")
            spy_price = self.get_last_price("SPY")
            if spy_bars is not None and len(spy_bars.df) >= 50 and spy_price is not None:
                spy_sma50 = float(spy_bars.df["close"].mean())
                is_bear_market = spy_price < spy_sma50
        except Exception:
            is_bear_market = False

        for item in active_items:
            sym = (item.get("symbol") or item.get("benchmark") or item.get("bull", "")).upper()
            if not sym:
                continue

            # Filter out assets with negative breaking news sentiment
            if sector_sentiment and sym in LONG_SHORT_PAIRS:
                p_data = sector_sentiment.get(sym, {})
                if p_data.get("stance") == "BEARISH" and p_data.get("sentiment_score", 0) <= -0.15:
                    continue  # Skip buying long when sector news is actively negative!

            # Protect against inverse ETF decay in healthy bull regimes
            if sym in self.BEAR_SYMBOLS and not is_bear_market:
                continue

            bars = self.get_historical_prices(sym, req_bars, "day")
            current_price = self.get_last_price(sym)

            if bars is None or len(bars.df) < req_bars or current_price is None or current_price <= 0:
                continue

            # Check 20-day SMA filter (must be in uptrend)
            sma_20 = float(bars.df["close"].iloc[-sma_period:].mean())
            if current_price <= sma_20:
                continue  # Skip any asset trading below its 20-day SMA

            # Calculate 10-day Rate of Change (ROC)
            past_price = float(bars.df["close"].iloc[-mom_days])
            if past_price <= 0:
                continue

            roc = (current_price - past_price) / past_price
            if roc > 0:  # Only positive momentum
                candidates.append({
                    "symbol": sym,
                    "price": current_price,
                    "sma": sma_20,
                    "roc": roc,
                })

        # Sort highest momentum first
        candidates.sort(key=lambda x: x["roc"], reverse=True)
        max_positions = self.parameters["max_positions"]
        return candidates[:max_positions]

    def _rebalance_champions(self, champions: list, portfolio_value: float):
        """Allocate capital into the top momentum champions without over-allocating."""
        if not champions:
            return

        held_positions = self.get_positions()
        held_symbols = [p.symbol for p in held_positions] if held_positions else []
        max_positions = self.parameters["max_positions"]
        alloc_per_stock = self.parameters["per_trade_alloc"]

        for champ in champions:
            if len(held_symbols) >= max_positions:
                break  # Reached maximum concurrent champion holdings

            sym = champ["symbol"]
            price = champ["price"]

            if sym not in held_symbols:
                budget = portfolio_value * alloc_per_stock
                qty = int(budget // price)
                if qty > 0:
                    order = self.create_order(sym, qty, "buy")
                    self.submit_order(order)
                    self.entry_prices[sym] = price
                    self.peak_prices[sym] = price
                    held_symbols.append(sym)
                    self.log_message(
                        f"🚀 [CHAMPION ENTRY] BOUGHT {qty} {sym} @ ${price:.2f} | "
                        f"10-Day ROC: +{champ['roc']*100:.1f}% | 20 SMA: ${champ['sma']:.2f}"
                    )


# =====================================================================
# RUNNER
# =====================================================================
if __name__ == "__main__":
    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_API_SECRET")
    is_paper = os.getenv("ALPACA_IS_PAPER", "true").lower() == "true"

    if not api_key or not api_secret:
        print("Error: Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env file.")
        sys.exit(1)

    alpaca_config = {
        "API_KEY": api_key,
        "API_SECRET": api_secret,
        "PAPER": is_paper,
    }

    broker = Alpaca(alpaca_config)

    # Instantiate single composite strategy
    strategy = MultiPairLongShortLive(broker=broker)

    trader = Trader()
    trader.add_strategy(strategy)

    print("=" * 65)
    print(" AUTONOMOUS MULTI-PAIR LONG/SHORT TRADER (LIVE PAPER MODE)")
    print(" Active Pair   : [SOXL (3x Bull)] <---> [SOXS (3x Bear)]")
    # Launch live Web UI dashboard in background daemon thread
    try:
        from dashboard import DashboardRequestHandler
        from http.server import HTTPServer
        import threading

        def _launch_dashboard():
            try:
                server = HTTPServer(("", 5000), DashboardRequestHandler)
                server.serve_forever()
            except Exception:
                pass

        dashboard_thread = threading.Thread(target=_launch_dashboard, daemon=True)
        dashboard_thread.start()
        print(" Live UI Dashboard : http://localhost:5000 (Open in your browser)")
    except Exception as e:
        print(f" [Notice] Dashboard background server skipped: {e}")

    print(" Polling Clock : 1 Minute evaluation cycle")
    print("=" * 65)

    trader.run_all()