import os
from datetime import datetime
from dotenv import load_dotenv

# Prevent OpenBLAS memory allocation failures on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

# Import the correct Alpaca backtesting class name
from lumibot.backtesting import AlpacaBacktesting
from lumibot.strategies import Strategy

load_dotenv()


class SOXLBreakout(Strategy):
    parameters = {
        "symbol": "SOXL",
        "sma_period": 20,
        "allocation": 0.90,
        "buffer_pct": 0.005,  # 0.5% hysteresis buffer
    }

    def initialize(self):
        self.sleeptime = "1D"

    def on_trading_iteration(self):
        symbol = self.parameters["symbol"]
        price = self.get_last_price(symbol)
        bars = self.get_historical_prices(symbol, self.parameters["sma_period"], "day")

        if bars is None or len(bars.df) < self.parameters["sma_period"]:
            return

        sma = float(bars.df["close"].mean())

        # Render indicator lines to logs/*_indicators.html
        self.add_line("SOXL_Close", price)
        self.add_line("SMA_20", sma)

        position = self.get_position(symbol)

        # Entry condition
        if position is None and price > sma:
            portfolio_val = self.get_portfolio_value()
            target_dollars = portfolio_val * self.parameters["allocation"]
            qty = int(target_dollars // price)

            if qty > 0:
                order = self.create_order(symbol, qty, "buy")
                self.submit_order(order)
                self.log_message(f"BUY {qty} {symbol} @ ${price:.2f} | SMA: ${sma:.2f}")

        # Exit condition with 0.5% buffer
        elif position is not None and price < (sma * (1 - self.parameters["buffer_pct"])):
            self.log_message(
                f"EXIT {symbol} @ ${price:.2f} (0.5% below SMA ${sma:.2f})"
            )
            self.sell_all()


if __name__ == "__main__":
    alpaca_backtest_config = {
        "API_KEY": os.getenv("ALPACA_API_KEY"),
        "API_SECRET": os.getenv("ALPACA_API_SECRET"),
        "PAPER": True,
    }

    SOXLBreakout.backtest(
        AlpacaBacktesting,
        datetime(2025, 1, 1),
        datetime(2025, 12, 31),
        config=alpaca_backtest_config,
        benchmark_asset="SOXL",
    )