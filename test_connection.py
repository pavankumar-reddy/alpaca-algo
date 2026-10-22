import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv()

api_key = os.getenv("ALPACA_API_KEY")
api_secret = os.getenv("ALPACA_API_SECRET")
is_paper = os.getenv("ALPACA_IS_PAPER", "true").lower() == "true"

try:
    client = TradingClient(api_key=api_key, secret_key=api_secret, paper=is_paper)
    account = client.get_account()
    
    print(f"Status: {account.status}")
    print(f"Buying Power: ${float(account.buying_power):,.2f}")
    print(f"Portfolio Value: ${float(account.portfolio_value):,.2f}")
    print("Connection successful.")
except Exception as e:
    print(f"Connection failed: {e}")