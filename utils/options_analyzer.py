import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf


OPTION_DESCRIPTION_PATTERN = re.compile(
    r"(?:Option Expiration for )?"
    r"(?P<instrument>[A-Z][A-Z0-9.]*)\s+"
    r"(?P<expiration>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<option_type>Put|Call)\s+"
    r"\$(?P<strike>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)

YAHOO_SYMBOL_OVERRIDES = {
    "BRK.B": "BRK-B",
}

TRADE_LABELS = {
    "BTO": "Bought to Open",
    "STO": "Sold to Open",
    "BTC": "Bought to Close",
    "STC": "Sold to Close",
}


def _is_valid_number(value, *, positive: bool = False) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and (number > 0 if positive else True)


def parse_option_description(description: str) -> Optional[Dict]:
    """Parse a Robinhood option description into a canonical contract."""
    match = OPTION_DESCRIPTION_PATTERN.search(str(description))
    if not match:
        return None

    expiration = datetime.strptime(match.group("expiration"), "%m/%d/%Y").date()
    instrument = match.group("instrument").upper()
    option_type = match.group("option_type").title()
    strike = float(match.group("strike").replace(",", ""))
    contract_id = f"{instrument}|{expiration.isoformat()}|{option_type}|{strike:.4f}"

    return {
        "contract_id": contract_id,
        "instrument": instrument,
        "expiration": expiration,
        "option_type": option_type,
        "strike": strike,
        "contract": (
            f"{instrument} {expiration.isoformat()} {option_type} "
            f"${strike:,.2f}"
        ),
    }


def _position_delta(trans_code: str, quantity: float) -> float:
    """Translate an option event into a signed position change."""
    quantity = float(quantity)
    if trans_code == "BTO":
        return abs(quantity)
    if trans_code == "STO":
        return -abs(quantity)
    if trans_code == "BTC":
        return abs(quantity)
    if trans_code == "STC":
        return -abs(quantity)
    if trans_code in ("OEXP", "OASGN", "OEXCS"):
        # Robinhood's quantity is signed for terminal events: a negative
        # quantity removes a long position and a positive one removes a short.
        return quantity
    return 0.0


def _apply_position_event(contract: Dict, delta: float, cash_flow: float) -> None:
    """Apply an option event and retain entry cash for the remaining position."""
    if abs(delta) < 1e-12:
        return

    position = contract["net_contracts"]
    open_cash = contract["open_premium_cash_flow"]

    if abs(position) < 1e-12 or position * delta > 0:
        contract["net_contracts"] = position + delta
        contract["open_premium_cash_flow"] = open_cash + cash_flow
        return

    closing_contracts = min(abs(position), abs(delta))
    entry_cash_closed = open_cash * (closing_contracts / abs(position))
    event_cash_closed = cash_flow * (closing_contracts / abs(delta))

    contract["realized_pnl"] += entry_cash_closed + event_cash_closed
    contract["open_premium_cash_flow"] -= entry_cash_closed
    contract["net_contracts"] += math.copysign(closing_contracts, delta)

    remaining_contracts = abs(delta) - closing_contracts
    if remaining_contracts > 1e-12:
        remaining_cash = cash_flow - event_cash_closed
        contract["net_contracts"] += math.copysign(remaining_contracts, delta)
        contract["open_premium_cash_flow"] += remaining_cash

    if abs(contract["net_contracts"]) < 1e-9:
        contract["net_contracts"] = 0.0
        contract["open_premium_cash_flow"] = 0.0


def calculate_options_summary(options_df: pd.DataFrame) -> Dict:
    """Calculate option activity, realized results, and current open positions."""
    contracts: Dict[str, Dict] = {}
    unparsed_descriptions: List[str] = []

    ordered = options_df.copy()
    ordered["_activity_date"] = pd.to_datetime(
        ordered["Activity Date"], errors="coerce"
    )
    ordered["_source_order"] = range(len(ordered))
    ordered = ordered.sort_values(
        ["_activity_date", "_source_order"],
        ascending=[True, False],
        kind="stable",
    )

    purchased_contracts = 0.0
    sold_contracts = 0.0
    premium_paid = 0.0
    premium_received = 0.0
    activity = defaultdict(lambda: {"contracts": 0.0, "cash_flow": 0.0})
    transaction_rows = []

    for _, row in ordered.iterrows():
        parsed = parse_option_description(row["Description"])
        if parsed is None:
            unparsed_descriptions.append(str(row["Description"]))
            continue

        trans_code = str(row["Trans Code"])
        quantity = float(row["Quantity"])
        amount = float(row["Amount"])

        # Opening premiums are deliberately kept separate from closing cash.
        # BTC is a cost to close an existing short position, not a premium
        # paid to purchase a new option. Assignment rows have no option cash
        # amount and are excluded from both opening-premium totals.
        if trans_code == "BTO":
            purchased_contracts += abs(quantity)
            premium_paid += abs(amount)
        elif trans_code == "STO":
            sold_contracts += abs(quantity)
            premium_received += abs(amount)

        if trans_code in TRADE_LABELS:
            activity[trans_code]["contracts"] += abs(quantity)
            activity[trans_code]["cash_flow"] += amount

        contract_id = parsed["contract_id"]
        if contract_id not in contracts:
            contracts[contract_id] = {
                **parsed,
                "net_contracts": 0.0,
                "open_premium_cash_flow": 0.0,
                "realized_pnl": 0.0,
                "total_cash_flow": 0.0,
                "transactions": [],
            }

        contract = contracts[contract_id]
        contract["total_cash_flow"] += amount
        contract["transactions"].append(
            {
                "Date": row["Activity Date"],
                "Instrument": parsed["instrument"],
                "Contract": parsed["contract"],
                "Type": trans_code,
                "Quantity": quantity,
                "Price": float(row["Price"]),
                "Amount": amount,
            }
        )
        _apply_position_event(
            contract,
            _position_delta(trans_code, quantity),
            amount,
        )

        transaction_rows.append(contract["transactions"][-1])

    open_positions = [
        contract
        for contract in contracts.values()
        if abs(contract["net_contracts"]) > 1e-9
    ]
    for contract in open_positions:
        contract["position"] = (
            "Long" if contract["net_contracts"] > 0 else "Short"
        )
        contract["contracts"] = abs(contract["net_contracts"])

    activity_rows = []
    for code in ("BTO", "STO", "BTC", "STC"):
        row = activity[code]
        activity_rows.append(
            {
                "Action": TRADE_LABELS[code],
                "Code": code,
                "Contracts": row["contracts"],
                "Cash Paid": abs(row["cash_flow"]) if row["cash_flow"] < 0 else 0.0,
                "Cash Received": row["cash_flow"] if row["cash_flow"] > 0 else 0.0,
            }
        )

    instrument_summary = defaultdict(
        lambda: {
            "Open Contracts": 0.0,
            "Premium Paid": 0.0,
            "Premium Received": 0.0,
            "Net Premium": 0.0,
        }
    )
    for position in open_positions:
        row = instrument_summary[position["instrument"]]
        row["Open Contracts"] += position["contracts"]
        if position["open_premium_cash_flow"] < 0:
            row["Premium Paid"] += -position["open_premium_cash_flow"]
        elif position["open_premium_cash_flow"] > 0:
            row["Premium Received"] += position["open_premium_cash_flow"]
        row["Net Premium"] += position["open_premium_cash_flow"]

    instrument_rows = []
    for instrument, row in instrument_summary.items():
        instrument_rows.append(
            {
                "Instrument": instrument,
                "Open Contracts": row["Open Contracts"],
                "Premium Paid": row["Premium Paid"],
                "Premium Received": row["Premium Received"],
                "Net Premium": row["Net Premium"],
            }
        )
    instrument_rows.sort(key=lambda item: abs(item["Net Premium"]), reverse=True)

    lifetime_instrument_summary = defaultdict(
        lambda: {
            "Transactions": 0.0,
            "Premium Paid": 0.0,
            "Premium Received": 0.0,
            "Net Premium": 0.0,
        }
    )
    for _, row in ordered.iterrows():
        parsed = parse_option_description(row["Description"])
        if parsed is None:
            continue
        trans_code = str(row["Trans Code"])
        if trans_code not in TRADE_LABELS:
            continue

        amount = float(row["Amount"])
        instrument_row = lifetime_instrument_summary[parsed["instrument"]]
        instrument_row["Transactions"] += abs(float(row["Quantity"]))
        if amount < 0:
            instrument_row["Premium Paid"] += abs(amount)
            instrument_row["Net Premium"] -= abs(amount)
        elif amount > 0:
            instrument_row["Premium Received"] += amount
            instrument_row["Net Premium"] += amount

    lifetime_instrument_rows = []
    for instrument, row in lifetime_instrument_summary.items():
        lifetime_instrument_rows.append(
            {
                "Instrument": instrument,
                "Transactions": row["Transactions"],
                "Premium Paid": row["Premium Paid"],
                "Premium Received": row["Premium Received"],
                "Net Premium": row["Net Premium"],
            }
        )
    lifetime_instrument_rows.sort(
        key=lambda item: abs(item["Net Premium"]), reverse=True
    )

    net_cash_flow = sum(
        activity[code]["cash_flow"] for code in ("BTO", "STO", "BTC", "STC")
    )
    closing_costs = abs(activity["BTC"]["cash_flow"])
    closing_proceeds = activity["STC"]["cash_flow"]
    realized_pnl = sum(contract["realized_pnl"] for contract in contracts.values())
    open_premium_paid = sum(
        -position["open_premium_cash_flow"]
        for position in open_positions
        if position["open_premium_cash_flow"] < 0
    )
    open_premium_received = sum(
        position["open_premium_cash_flow"]
        for position in open_positions
        if position["open_premium_cash_flow"] > 0
    )

    return {
        "purchased_contracts": purchased_contracts,
        "sold_contracts": sold_contracts,
        "premium_paid": premium_paid,
        "premium_received": premium_received,
        "open_premium_paid": open_premium_paid,
        "open_premium_received": open_premium_received,
        "closing_costs": closing_costs,
        "closing_proceeds": closing_proceeds,
        "net_cash_flow": net_cash_flow,
        "realized_pnl": realized_pnl,
        "open_positions": open_positions,
        "open_long_contracts": sum(
            position["contracts"]
            for position in open_positions
            if position["position"] == "Long"
        ),
        "open_short_contracts": sum(
            position["contracts"]
            for position in open_positions
            if position["position"] == "Short"
        ),
        "instrument_rows": instrument_rows,
        "lifetime_instrument_rows": lifetime_instrument_rows,
        "activity_rows": activity_rows,
        "transaction_rows": list(reversed(transaction_rows)),
        "contracts": contracts,
        "unparsed_descriptions": sorted(set(unparsed_descriptions)),
    }


def _option_mark(row: pd.Series) -> Tuple[Optional[float], Optional[str]]:
    """Choose a current mark, preferring the midpoint of a valid bid/ask."""
    bid = row.get("bid")
    ask = row.get("ask")
    last = row.get("lastPrice")

    if (
        _is_valid_number(bid, positive=True)
        and _is_valid_number(ask, positive=True)
        and float(ask) >= float(bid)
    ):
        return (float(bid) + float(ask)) / 2, "Bid/Ask midpoint"
    if _is_valid_number(last, positive=True):
        return float(last), "Last trade"
    return None, None


def get_current_option_quotes(open_positions: List[Dict]) -> Dict[str, Dict]:
    """Fetch current yfinance marks for canonical open option contracts."""
    quotes = {
        position["contract_id"]: {
            "current_price": None,
            "bid": None,
            "ask": None,
            "last_price": None,
            "quote_source": None,
        }
        for position in open_positions
    }
    chains: Dict[Tuple[str, str], object] = {}

    for position in open_positions:
        instrument = position["instrument"]
        yahoo_symbol = YAHOO_SYMBOL_OVERRIDES.get(instrument, instrument)
        expiration = position["expiration"].isoformat()
        chain_key = (yahoo_symbol, expiration)

        if chain_key not in chains:
            try:
                chains[chain_key] = yf.Ticker(yahoo_symbol).option_chain(expiration)
            except Exception as exc:
                print(
                    f"Yahoo Finance option chain failed for "
                    f"{instrument} {expiration}: {exc}"
                )
                chains[chain_key] = None

        chain = chains[chain_key]
        if chain is None:
            continue

        table = chain.calls if position["option_type"] == "Call" else chain.puts
        matches = table.loc[
            (table["strike"].astype(float) - position["strike"]).abs() < 1e-8
        ]
        if matches.empty:
            print(f"Yahoo Finance option contract not found: {position['contract']}")
            continue

        row = matches.iloc[0]
        mark, source = _option_mark(row)
        quote = quotes[position["contract_id"]]
        quote.update(
            {
                "current_price": mark,
                "bid": float(row["bid"]) if _is_valid_number(row.get("bid")) else None,
                "ask": float(row["ask"]) if _is_valid_number(row.get("ask")) else None,
                "last_price": (
                    float(row["lastPrice"])
                    if _is_valid_number(row.get("lastPrice"))
                    else None
                ),
                "quote_source": source,
            }
        )

    return quotes
