import os
import datetime as dt
import pytz
from dotenv import load_dotenv

# Prevent OpenBLAS memory allocation overhead on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

load_dotenv()

from lumibot.strategies.strategy import Strategy
from lumibot.backtesting import AlpacaBacktesting

class SOXLLongShortRotation(Strategy):
    parameters = {
        "bull_symbol": "SOXL",  # 3x Bullish Semiconductor ETF
        "bear_symbol": "SOXS",  # 3x Bearish/Inverse Semiconductor ETF
        "sma_period": 20,
        "buffer_pct": 0.005,    # 0.5% hysteresis buffer
        "allocation": 0.90,     # 90% allocation cap (10% cash reserve)
    }

    def initialize(self):
        self.sleeptime = "1D"

    def on_trading_iteration(self):
        bull = self.parameters["bull_symbol"]
        bear = self.parameters["bear_symbol"]
        sma_period = self.parameters["sma_period"]
        buffer_pct = self.parameters.get("buffer_pct", 0.005)

        bars = self.get_historical_prices(bull, sma_period, "day")
        if bars is None or len(bars.df) < sma_period:
            return

        current_price = self.get_last_price(bull)
        sma = float(bars.df["close"].mean())

        # Render indicator lines in backtest reports
        self.add_line("SOXL_Close", current_price)
        self.add_line("SMA_20", sma)

        bull_pos = self.get_position(bull)
        bear_pos = self.get_position(bear)

        # Bull Regime: Price > SMA -> Rotate into SOXL
        if current_price > sma:
            if bear_pos is not None:
                self.sell_all(bear)
            if bull_pos is None:
                qty = int((self.get_portfolio_value() * self.parameters["allocation"]) // current_price)
                if qty > 0:
                    self.submit_order(self.create_order(bull, qty, "buy"))
                    self.log_message(f"ROTATION -> BUY BULL {qty} {bull} @ ${current_price:.2f}")

        # Bear Regime: Price drops 0.5% below SMA -> Rotate into SOXS
        elif current_price < (sma * (1 - buffer_pct)):
            if bull_pos is not None:
                self.sell_all(bull)
            if bear_pos is None:
                bear_price = self.get_last_price(bear)
                if bear_price and bear_price > 0:
                    qty = int((self.get_portfolio_value() * self.parameters["allocation"]) // bear_price)
                    if qty > 0:
                        self.submit_order(self.create_order(bear, qty, "buy"))
                        self.log_message(f"ROTATION -> BUY BEAR {qty} {bear} @ ${bear_price:.2f}")


if __name__ == "__main__":
    alpaca_config = {
        "API_KEY": os.getenv("ALPACA_API_KEY"),
        "API_SECRET": os.getenv("ALPACA_API_SECRET"),
        "PAPER": True,
    }

    tzinfo = pytz.timezone("America/New_York")
    backtesting_start = tzinfo.localize(dt.datetime(2025, 1, 1))
    backtesting_end = tzinfo.localize(dt.datetime(2025, 12, 31))

    results, strategy = SOXLLongShortRotation.run_backtest(
        datasource_class=AlpacaBacktesting,
        backtesting_start=backtesting_start,
        backtesting_end=backtesting_end,
        benchmark_asset="SOXL",
        analyze_backtest=True,
        show_progress_bar=True,
        timestep="day",
        market="NASDAQ",
        config=alpaca_config,
    )

    print(results)