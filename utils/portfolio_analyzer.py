import math
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd
import yfinance as yf


# yfinance stores timezone and Yahoo cookies in SQLite. The default user-profile
# cache can be read-only in hosted/sandboxed runs, which makes every download
# fail with ``OperationalError: unable to open database file``. Keep the cache
# beside the application instead, where the process is guaranteed write access.
YFINANCE_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "yfinance"
YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))


YAHOO_SYMBOL_OVERRIDES = {
    "BRK.B": "BRK-B",
}


def _valid_price(value) -> bool:
    """Return True only for a finite, positive market price."""
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _latest_close(data: pd.DataFrame, yahoo_symbol: str) -> Optional[float]:
    """Extract the newest non-null Close from a yfinance download result."""
    if data is None or data.empty:
        return None

    try:
        if isinstance(data.columns, pd.MultiIndex):
            if yahoo_symbol not in data.columns.get_level_values(0):
                return None
            closes = data[yahoo_symbol]["Close"]
        else:
            closes = data["Close"]

        closes = closes.dropna()
        if not closes.empty and _valid_price(closes.iloc[-1]):
            return float(closes.iloc[-1])
    except (KeyError, TypeError, ValueError):
        pass

    return None


def _download_prices(
    yahoo_symbols: Iterable[str], *, period: str, interval: str
) -> Dict[str, float]:
    """Fetch a batch of latest closes from Yahoo Finance."""
    symbols = list(dict.fromkeys(yahoo_symbols))
    if not symbols:
        return {}

    data = yf.download(
        tickers=symbols,
        period=period,
        interval=interval,
        group_by="ticker",
        auto_adjust=False,
        prepost=False,
        threads=True,
        progress=False,
        timeout=15,
    )
    return {
        symbol: price
        for symbol in symbols
        if (price := _latest_close(data, symbol)) is not None
    }


def get_current_prices(instruments: list) -> Dict[str, Optional[float]]:
    """
    Fetch the latest available market price for each instrument from yfinance.

    Minute-level prices are requested in one batch. Any missing symbols are
    retried with recent daily data and then with yfinance's quote metadata.
    A failed lookup remains ``None`` so the UI can report it as unavailable;
    it is never presented as a real $0.00 quote.
    """
    instruments = list(dict.fromkeys(instruments))
    prices: Dict[str, Optional[float]] = {symbol: None for symbol in instruments}
    yahoo_symbols = {
        symbol: YAHOO_SYMBOL_OVERRIDES.get(symbol, symbol) for symbol in instruments
    }

    try:
        intraday = _download_prices(
            yahoo_symbols.values(), period="1d", interval="1m"
        )
    except Exception as exc:
        print(f"Yahoo Finance intraday batch failed: {exc}")
        intraday = {}

    for instrument, yahoo_symbol in yahoo_symbols.items():
        if yahoo_symbol in intraday:
            prices[instrument] = intraday[yahoo_symbol]

    missing = [symbol for symbol, price in prices.items() if not _valid_price(price)]
    if missing:
        try:
            recent = _download_prices(
                (yahoo_symbols[symbol] for symbol in missing),
                period="5d",
                interval="1d",
            )
        except Exception as exc:
            print(f"Yahoo Finance daily batch failed: {exc}")
            recent = {}

        for instrument in missing:
            yahoo_symbol = yahoo_symbols[instrument]
            if yahoo_symbol in recent:
                prices[instrument] = recent[yahoo_symbol]

    missing = [symbol for symbol, price in prices.items() if not _valid_price(price)]
    for instrument in missing:
        yahoo_symbol = yahoo_symbols[instrument]
        try:
            ticker = yf.Ticker(yahoo_symbol)
            quote = ticker.info
            price = quote.get("currentPrice") or quote.get("regularMarketPrice")
            if _valid_price(price):
                prices[instrument] = float(price)
            else:
                print(f"Yahoo Finance returned no valid price for {instrument}")
        except Exception as exc:
            print(f"Yahoo Finance lookup failed for {instrument}: {exc}")

    return prices


def calculate_portfolio_metrics(holdings: Dict, current_prices: Dict) -> Dict:
    """Calculate current value and unrealized gain/loss for each holding."""
    portfolio_metrics = {}

    for instrument, holding in holdings.items():
        current_price = current_prices.get(instrument)
        total_shares = holding["total_shares"]
        total_cost = holding["total_cost"]
        avg_cost = holding["avg_cost_per_share"]

        if _valid_price(current_price) and total_shares > 0:
            current_value = total_shares * float(current_price)
            unrealized_gain_loss = current_value - total_cost
            unrealized_gain_loss_pct = (
                unrealized_gain_loss / total_cost * 100 if total_cost != 0 else 0
            )
        else:
            current_value = None
            unrealized_gain_loss = None
            unrealized_gain_loss_pct = None

        portfolio_metrics[instrument] = {
            "total_shares": total_shares,
            "avg_cost_per_share": avg_cost,
            "total_cost": total_cost,
            "current_price": current_price,
            "current_value": current_value,
            "unrealized_gain_loss": unrealized_gain_loss,
            "unrealized_gain_loss_pct": unrealized_gain_loss_pct,
            "transactions": holding["transactions"],
        }

    return portfolio_metrics


def format_currency(value):
    """Format a currency value, keeping unavailable data distinct from zero."""
    if value is None or pd.isna(value):
        return "N/A"
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def format_percentage(value):
    """Format a percentage value, keeping unavailable data distinct from zero."""
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:.2f}%"
