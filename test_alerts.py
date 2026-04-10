from alerts.engine import AlertEngine
import pandas as pd

engine = AlertEngine()

market_data = pd.DataFrame([
    {"Symbol": "VWCE", "Drawdown from ATH %": -25},
    {"Symbol": "VIX", "Price": 30}
])

portfolio_value = 10000

print("FIRST RUN")
alerts = engine.evaluate(market_data, portfolio_value)
for alert in alerts:
    print(alert.message)

print("\nSECOND RUN")
alerts = engine.evaluate(market_data, portfolio_value)
for alert in alerts:
    print(alert.message)

print("\nRECOVERY")
market_data = pd.DataFrame([
    {"Symbol": "VWCE", "Drawdown from ATH %": -12},
    {"Symbol": "VIX", "Price": 30}
])

engine.evaluate(market_data, portfolio_value)

print("\nDROP AGAIN")
market_data = pd.DataFrame([
    {"Symbol": "VWCE", "Drawdown from ATH %": -25},
    {"Symbol": "VIX", "Price": 30}
])

alerts = engine.evaluate(market_data, portfolio_value)
for alert in alerts:
    print(alert.message)