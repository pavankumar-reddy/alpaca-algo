import os
import sys
import json
import datetime as dt
import pytz
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Prevent OpenBLAS/OMP memory overhead on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

load_dotenv()

from lumibot.strategies.strategy import Strategy
from lumibot.backtesting import YahooDataBacktesting, AlpacaBacktesting

class ChampionMomentumBacktest(Strategy):
    """
    Backtesting implementation of the Champion Momentum & Profit-Locking Engine:
    1. Trend Filter: Price must be above 20-day SMA.
    2. Momentum Ranking: Top candidates ranked by 10-day Rate-of-Change (ROC).
    3. Position Sizing: Top N champions allocated equal budget.
    4. Profit-Locking Trailing Stop: Exits if price drops 4.0% below peak high-water mark.
    5. Hard Stop-Loss: Exits if price drops 3.5% below entry.
    6. Trend Breakdown: Exits if price drops below 20-day SMA.
    """
    # Load all 36+ ETFs dynamically from watchlist.json
    try:
        with open("watchlist.json", "r") as f:
            _wl_data = json.load(f)
        _all_etfs = [item["symbol"].upper() for item in _wl_data if "symbol" in item]
    except Exception:
        _all_etfs = ["SOXL", "TQQQ", "NVDX", "BITX", "CONL", "GUSH"]

    BEAR_SYMBOLS = {
        "SOXS", "MSTZ", "BTCZ", "CONI", "NVD", "MUZ", "SQQQ",
        "TSLQ", "PLTD", "DRIP", "SCO", "KOLD", "GDXD", "RKLZ"
    }

    parameters = {
        "symbols": _all_etfs,
        "sma_period": 20,
        "momentum_days": 10,
        "max_positions": 3,          # Concentrated into Top 3 Champions
        "allocation_per_stock": 0.28,# 28% each = 84% invested, 16% cash reserve
        "trailing_stop_pct": 0.04,   # 4.0% winning trailing stop to lock in profits near peak
        "hard_stop_pct": 0.035,      # 3.5% hard stop-loss from entry
        "buffer_pct": 0.005,         # 0.5% buffer for SMA exit
    }

    def initialize(self):
        self.sleeptime = "1D"
        self.peak_prices = {}
        self.entry_prices = {}

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

    def on_trading_iteration(self):
        portfolio_value = self.get_portfolio_value()
        if portfolio_value <= 0:
            return

        self.add_line("Portfolio_Value", portfolio_value)

        # -----------------------------------------------------------------
        # STEP 0: MACRO CASH SHIELD (Dodge Broad Market Corrections)
        # -----------------------------------------------------------------
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
                self.log_message("🛡️ [MACRO CASH SHIELD] SPY below 50-day SMA! Liquidating to 100% Cash.")
                for pos in held_positions:
                    self.sell_all(pos.symbol)
                self.peak_prices.clear()
                self.entry_prices.clear()
            return  # Sit safely in 100% cash during broad market corrections

        # -----------------------------------------------------------------
        # STEP 1: MANAGE EXISTING POSITIONS (Trailing Stop, Hard Stop, SMA)
        # -----------------------------------------------------------------
        held_positions = self.get_positions()
        trailing_stop_pct = self.parameters["trailing_stop_pct"]
        hard_stop_pct = self.parameters["hard_stop_pct"]
        sma_period = self.parameters["sma_period"]
        buffer_pct = self.parameters["buffer_pct"]

        for pos in held_positions:
            sym = pos.symbol
            price = self.get_last_price(sym)
            if not price or price <= 0:
                continue

            if sym not in self.entry_prices:
                self.entry_prices[sym] = float(pos.avg_price) if hasattr(pos, "avg_price") and pos.avg_price else price

            if sym not in self.peak_prices:
                self.peak_prices[sym] = price
            else:
                self.peak_prices[sym] = max(self.peak_prices[sym], price)

            peak = self.peak_prices[sym]
            entry = self.entry_prices[sym]
            gain_pct = (price - entry) / entry if entry > 0 else 0

            # Profit-Tier Ratchet: Tighten stop distance as gains expand
            dynamic_stop_pct = self._calculate_dynamic_atr_stop(sym, price)
            if gain_pct >= 0.35:
                active_stop_pct = min(dynamic_stop_pct, 0.020)  # 2.0% parabolic lock
                tier_name = "PARABOLIC 2% LOCK"
            elif gain_pct >= 0.15:
                active_stop_pct = min(dynamic_stop_pct, 0.030)  # 3.0% profit ratchet
                tier_name = "PROFIT RATCHET 3%"
            else:
                active_stop_pct = dynamic_stop_pct
                tier_name = "BASE ATR"

            # Trailing Stop: Lock profits if pulled back from peak
            drawdown = (price - peak) / peak
            if drawdown <= -active_stop_pct:
                self.close_position(sym)
                self.log_message(f"🎯 [{tier_name}] Exited {sym} @ ${price:.2f} (Peak ${peak:.2f}, -{abs(drawdown)*100:.1f}% vs {active_stop_pct*100:.1f}%) | Gain: +{gain_pct*100:.1f}%")
                self.peak_prices.pop(sym, None)
                self.entry_prices.pop(sym, None)
                continue

            # Hard Stop-Loss: Cut quick losses
            loss = (price - entry) / entry
            if loss <= -hard_stop_pct:
                self.close_position(sym)
                self.log_message(f"🛑 [HARD STOP-LOSS] Exited {sym} @ ${price:.2f} (Loss: {loss*100:.1f}%)")
                self.peak_prices.pop(sym, None)
                self.entry_prices.pop(sym, None)
                continue

            # Trend Breakdown: Price broke below 20-day SMA
            bars = self.get_historical_prices(sym, sma_period, "day")
            if bars is not None and len(bars.df) >= sma_period:
                sma = float(bars.df["close"].mean())
                if price < (sma * (1 - buffer_pct)):
                    self.close_position(sym)
                    self.log_message(f"📉 [TREND BREAKDOWN] Exited {sym} @ ${price:.2f} (Below 20 SMA ${sma:.2f})")
                    self.peak_prices.pop(sym, None)
                    self.entry_prices.pop(sym, None)

        # -----------------------------------------------------------------
        # STEP 2: SCAN UNIVERSE & RANK MOMENTUM CHAMPIONS
        # -----------------------------------------------------------------
        symbols = self.parameters["symbols"]
        mom_days = self.parameters["momentum_days"]
        req_bars = max(sma_period, mom_days + 1)
        candidates = []

        # Check overall market regime (SPY 50-day SMA)
        is_bear_market = False
        try:
            spy_bars = self.get_historical_prices("SPY", 50, "day")
            spy_price = self.get_last_price("SPY")
            if spy_bars is not None and len(spy_bars.df) >= 50 and spy_price is not None:
                spy_sma50 = float(spy_bars.df["close"].mean())
                is_bear_market = spy_price < spy_sma50
        except Exception:
            is_bear_market = False

        for sym in symbols:
            # Skip decaying inverse bear ETFs unless broad market is in a confirmed bear regime
            if sym in self.BEAR_SYMBOLS and not is_bear_market:
                continue

            try:
                bars = self.get_historical_prices(sym, req_bars, "day")
                price = self.get_last_price(sym)
                if bars is None or len(bars.df) < req_bars or price is None or price <= 0:
                    continue

                # Uptrend filter: Must be above 20-day SMA
                sma = float(bars.df["close"].iloc[-sma_period:].mean())
                if price <= sma:
                    continue

                # Momentum ROC: (current_price - price_10_days_ago) / price_10_days_ago
                past_price = float(bars.df["close"].iloc[-mom_days])
                if past_price > 0:
                    roc = (price - past_price) / past_price
                    if roc > 0:
                        candidates.append({"symbol": sym, "price": price, "roc": roc, "sma": sma})
            except Exception:
                continue

        # Sort highest momentum first
        candidates.sort(key=lambda x: x["roc"], reverse=True)
        champions = candidates[: self.parameters["max_positions"]]

        # -----------------------------------------------------------------
        # STEP 3: REBALANCE INTO TOP CHAMPIONS
        # -----------------------------------------------------------------
        active_positions = self.get_positions()
        held_symbols = [p.symbol for p in active_positions] if active_positions else []
        max_pos = self.parameters["max_positions"]
        alloc = self.parameters["allocation_per_stock"]

        for champ in champions:
            if len(held_symbols) >= max_pos:
                break
            sym = champ["symbol"]
            price = champ["price"]
            if sym not in held_symbols:
                budget = portfolio_value * alloc
                qty = int(budget // price)
                if qty > 0:
                    self.submit_order(self.create_order(sym, qty, "buy"))
                    self.entry_prices[sym] = price
                    self.peak_prices[sym] = price
                    held_symbols.append(sym)
                    self.log_message(f"[CHAMPION ENTRY] Bought {qty} {sym} @ ${price:.2f} | 10D-ROC: +{champ['roc']*100:.1f}%")


if __name__ == "__main__":
    tzinfo = pytz.timezone("America/New_York")
    
    # Backtest period: Previous 1 Year (October 18, 2025 through October 18, 2026)
    backtest_start = tzinfo.localize(dt.datetime(2025, 10, 18))
    backtest_end = tzinfo.localize(dt.datetime(2026, 10, 18))

    print("=" * 65)
    print(" >>> STARTING MULTI-ETF CHAMPION MOMENTUM BACKTEST <<<")
    print("=" * 65)
    print(f" Period: {backtest_start.strftime('%Y-%m-%d')} -> {backtest_end.strftime('%Y-%m-%d')}")
    print(f" Universe: All {len(ChampionMomentumBacktest.parameters['symbols'])} ETFs from watchlist.json")
    print(" Strategy: Top 3 Champions (20-SMA filter + 10D ROC + 4% Trailing Stop)")
    print(" Benchmark: SPY")
    print("=" * 65)

    # Use YahooDataBacktesting for seamless, rate-limit-free historical data
    results, strategy = ChampionMomentumBacktest.run_backtest(
        datasource_class=YahooDataBacktesting,
        backtesting_start=backtest_start,
        backtesting_end=backtest_end,
        benchmark_asset="SPY",
        analyze_backtest=True,
        show_progress_bar=True,
        timestep="day",
        market="NASDAQ",
    )

    print("\n" + "=" * 65)
    print(" >>> BACKTEST COMPLETED SUCCESSFULLY! <<<")
    print("=" * 65)
    if isinstance(results, dict):
        for k, v in results.items():
            if isinstance(v, (int, float)):
                print(f" {k:<25}: {v:.2f}")
            else:
                print(f" {k:<25}: {v}")
    else:
        print(results)
    print("=" * 65)
    print(" Interactive HTML Tear Sheet saved to the 'logs/' directory.")
    print(" Open logs/*_tearsheet.html in your browser to inspect interactive charts.")
    print("=" * 65)
