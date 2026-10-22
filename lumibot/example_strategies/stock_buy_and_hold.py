import os
import datetime as dt
import pytz
from dotenv import load_dotenv

# Prevent OpenBLAS memory allocation failures on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

load_dotenv()

from lumibot.strategies.strategy import Strategy
from lumibot.backtesting import AlpacaBacktesting


class BuyAndHold(Strategy):
    parameters = {
        "buy_symbol": "QQQ",
    }

    def initialize(self):
        self.sleeptime = "1D"

    def on_trading_iteration(self):
        current_dt = self.get_datetime()
        self.log_message(f"Current datetime: {current_dt}")

        buy_symbol = self.parameters["buy_symbol"]
        current_value = self.get_last_price(buy_symbol)
        self.log_message(f"The value of {buy_symbol} is {current_value}")

        self.add_line(f"{buy_symbol} Value", current_value)

        all_positions = self.get_positions()

        # Buy once on the first trading iteration, then hold
        if len(all_positions) == 0 and current_value is not None:
            quantity = int(self.get_portfolio_value() // current_value)
            if quantity > 0:
                purchase_order = self.create_order(buy_symbol, quantity, "buy")
                self.submit_order(purchase_order)


if __name__ == "__main__":
    alpaca_config = {
        "API_KEY": os.getenv("ALPACA_API_KEY"),
        "API_SECRET": os.getenv("ALPACA_API_SECRET"),
        "PAPER": True,
    }

    tzinfo = pytz.timezone("America/New_York")
    backtesting_start = tzinfo.localize(dt.datetime(2025, 1, 1))
    backtesting_end = tzinfo.localize(dt.datetime(2025, 12, 31))

    results, strategy = BuyAndHold.run_backtest(
        datasource_class=AlpacaBacktesting,
        backtesting_start=backtesting_start,
        backtesting_end=backtesting_end,
        benchmark_asset="QQQ",
        analyze_backtest=True,
        parameters={
            "buy_symbol": "QQQ",
        },
        show_progress_bar=True,
        timestep="day",
        market="NASDAQ",
        config=alpaca_config,
    )

    print(results)