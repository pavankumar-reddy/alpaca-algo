import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()

client = TradingClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_API_SECRET"),
    paper=True,
)

# 1. Place a $50 market buy order for BTC
order_request = MarketOrderRequest(
    symbol="BTC/USD",
    notional=50.00,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.GTC,
)

order = client.submit_order(order_request)
print(f"Submitted Order ID: {order.id} | Status: {order.status} | Symbol: {order.symbol}")

# 2. Check open positions
positions = client.get_all_positions()
print(f"Active Positions: {[p.symbol for p in positions]}")