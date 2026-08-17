import re

import pandas as pd


DIVIDEND_TYPES = {
    "CDIV": "Cash Dividend",
    "MDIV": "Manufactured Dividend",
    "DTAX": "Foreign Tax Withheld",
}

DIVIDEND_DESCRIPTION_PATTERN = re.compile(
    r"R/D\s+(?P<record_date>\d{4}-\d{2}-\d{2})\s+"
    r"P/D\s+(?P<payment_date>\d{4}-\d{2}-\d{2})\s+-\s+"
    r"(?P<shares>[\d,.]+)\s+shares\s+at\s+"
    r"(?P<rate>[\d,.]+)",
    re.IGNORECASE,
)


def _parse_dividend_description(description: str) -> dict[str, object | None]:
    """Extract record date, payment date, shares, and per-share rate."""
    match = DIVIDEND_DESCRIPTION_PATTERN.search(str(description))
    if not match:
        return {
            "record_date": None,
            "payment_date": None,
            "shares": None,
            "rate": None,
        }

    return {
        "record_date": pd.to_datetime(match.group("record_date")).date(),
        "payment_date": pd.to_datetime(match.group("payment_date")).date(),
        "shares": float(match.group("shares").replace(",", "")),
        "rate": float(match.group("rate").replace(",", "")),
    }


def calculate_dividend_summary(dividends_df: pd.DataFrame) -> dict:
    """Calculate lifetime dividend totals and per-instrument summaries."""
    dividends = dividends_df[
        dividends_df["Trans Code"].isin(["CDIV", "MDIV"])
    ].copy()
    taxes = dividends_df[dividends_df["Trans Code"] == "DTAX"].copy()

    dividends["_activity_date"] = pd.to_datetime(
        dividends["Activity Date"], errors="coerce"
    )
    taxes["_activity_date"] = pd.to_datetime(
        taxes["Activity Date"], errors="coerce"
    )

    cash_total = float(
        dividends.loc[dividends["Trans Code"] == "CDIV", "Amount"].sum()
    )
    manufactured_total = float(
        dividends.loc[dividends["Trans Code"] == "MDIV", "Amount"].sum()
    )
    gross_total = cash_total + manufactured_total
    tax_withheld = abs(float(taxes["Amount"].sum()))
    net_total = gross_total - tax_withheld

    instruments = sorted(
        set(dividends["Instrument"].dropna())
        | set(taxes["Instrument"].dropna())
    )
    instrument_rows = []
    for instrument in instruments:
        instrument_dividends = dividends[
            dividends["Instrument"] == instrument
        ]
        instrument_taxes = taxes[taxes["Instrument"] == instrument]
        cash_dividends = float(
            instrument_dividends.loc[
                instrument_dividends["Trans Code"] == "CDIV", "Amount"
            ].sum()
        )
        manufactured_dividends = float(
            instrument_dividends.loc[
                instrument_dividends["Trans Code"] == "MDIV", "Amount"
            ].sum()
        )
        gross_dividends = cash_dividends + manufactured_dividends
        instrument_tax = abs(float(instrument_taxes["Amount"].sum()))
        payment_dates = instrument_dividends["_activity_date"].dropna()

        instrument_rows.append({
            "Instrument": instrument,
            "Payments": len(instrument_dividends),
            "Cash Dividends": cash_dividends,
            "Manufactured Dividends": manufactured_dividends,
            "Gross Dividends": gross_dividends,
            "Foreign Tax Withheld": instrument_tax,
            "Net Dividends Received": gross_dividends - instrument_tax,
            "First Payment": (
                payment_dates.min().date() if not payment_dates.empty else None
            ),
            "Last Payment": (
                payment_dates.max().date() if not payment_dates.empty else None
            ),
        })

    transaction_rows = []
    ordered = dividends_df.copy()
    ordered["_activity_date"] = pd.to_datetime(
        ordered["Activity Date"], errors="coerce"
    )
    ordered = ordered.sort_values("_activity_date", ascending=False)
    for _, row in ordered.iterrows():
        trans_code = str(row["Trans Code"])
        parsed = _parse_dividend_description(row["Description"])
        amount = float(row["Amount"])
        is_tax = trans_code == "DTAX"
        transaction_rows.append({
            "Activity Date": (
                row["_activity_date"].date()
                if not pd.isna(row["_activity_date"])
                else None
            ),
            "Instrument": row["Instrument"],
            "Payment Type": DIVIDEND_TYPES.get(trans_code, trans_code),
            "Record Date": parsed["record_date"],
            "Payment Date": parsed["payment_date"],
            "Eligible Shares": parsed["shares"],
            "Rate Per Share": parsed["rate"],
            "Gross Dividend": 0.0 if is_tax else amount,
            "Tax Withheld": abs(amount) if is_tax else 0.0,
            "Net Amount": amount,
        })

    payment_dates = dividends["_activity_date"].dropna()
    return {
        "cash_total": cash_total,
        "manufactured_total": manufactured_total,
        "gross_total": gross_total,
        "tax_withheld": tax_withheld,
        "net_total": net_total,
        "payment_count": len(dividends),
        "instrument_count": len(set(dividends["Instrument"].dropna())),
        "first_payment": (
            payment_dates.min().date() if not payment_dates.empty else None
        ),
        "last_payment": (
            payment_dates.max().date() if not payment_dates.empty else None
        ),
        "instrument_rows": sorted(
            instrument_rows,
            key=lambda row: row["Net Dividends Received"],
            reverse=True,
        ),
        "transaction_rows": transaction_rows,
    }
