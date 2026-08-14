from datetime import datetime

def format_currency(value: float, currency: str = 'EUR') -> str:
    symbol = '€' if currency == 'EUR' else '$' if currency == 'USD' else currency
    return f"{symbol}{value:,.2f}"

def format_percent(value: float) -> str:
    return f"{value:+.2f}%"

def calculate_pnl(current_price: float, avg_price: float, quantity: int) -> dict:
    if current_price is None or avg_price is None or quantity is None:
        return {"pnl_absolute": 0.0, "pnl_percent": 0.0}
        
    invested = avg_price * quantity
    current_value = current_price * quantity
    pnl_absolute = current_value - invested
    
    pnl_percent = 0.0
    if invested > 0:
        pnl_percent = (pnl_absolute / invested) * 100
        
    return {
        "pnl_absolute": round(pnl_absolute, 2),
        "pnl_percent": round(pnl_percent, 2)
    }

def is_trading_day(date=None) -> bool:
    if date is None:
        date = datetime.now()
    return date.weekday() < 5

def get_market_for_ticker(ticker: str) -> str:
    if ticker.endswith('.MI'):
        return 'IT'
    elif ticker.endswith('.AS'):
        return 'EU_NL'
    elif ticker.endswith('.DE'):
        return 'EU_DE'
    elif ticker.endswith('.PA'):
        return 'EU_FR'
    else:
        return 'US'
