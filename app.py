import os

import pandas as pd
import streamlit as st

from utils.csv_parser import get_holding_summary, parse_csv
from utils.dividend_analyzer import calculate_dividend_summary
from utils.options_analyzer import (
    calculate_options_summary,
    get_current_option_quotes,
)
from utils.portfolio_analyzer import (
    calculate_portfolio_metrics,
    format_currency,
    format_percentage,
    get_current_prices,
)


def render_options_tab(options_df: pd.DataFrame) -> None:
    """Render option activity and live open-position valuation."""
    if options_df.empty:
        st.info("No option transactions found in the uploaded CSV file.")
        return

    st.subheader("Options Positions Summary")
    options_summary = calculate_options_summary(options_df)
    open_options = options_summary['open_positions']

    with st.spinner("Fetching current option prices from Yahoo Finance..."):
        option_quotes = get_current_option_quotes(open_options)

    missing_option_quotes = [
        position['contract']
        for position in open_options
        if option_quotes[position['contract_id']]['current_price'] is None
    ]
    if missing_option_quotes:
        st.error(
            "Yahoo Finance did not return a current option price for: "
            + ", ".join(missing_option_quotes)
            + ". Current option totals are unavailable until all quotes load."
        )
    elif open_options:
        st.success(
            f"Loaded current Yahoo Finance prices for all "
            f"{len(open_options)} open option positions."
        )

    option_position_rows = []
    for position in open_options:
        quote = option_quotes[position['contract_id']]
        current_price = quote['current_price']
        direction = 1 if position['position'] == 'Long' else -1
        current_value = (
            direction * position['contracts'] * 100 * current_price
            if current_price is not None
            else None
        )
        unrealized_gain_loss = (
            current_value + position['open_premium_cash_flow']
            if current_value is not None
            else None
        )
        option_position_rows.append({
            'Contract': position['contract'],
            'Instrument': position['instrument'],
            'Expiration': position['expiration'],
            'Type': position['option_type'],
            'Strike': position['strike'],
            'Position': position['position'],
            'Contracts': position['contracts'],
            'Premium Paid': max(-position['open_premium_cash_flow'], 0),
            'Premium Received': max(position['open_premium_cash_flow'], 0),
            'Current Option Price': current_price,
            'Current Position Value': current_value,
            'Unrealized Gain/Loss': unrealized_gain_loss,
            'Quote Source': quote['quote_source'],
        })

    all_option_quotes_loaded = not missing_option_quotes
    net_current_option_value = (
        sum(row['Current Position Value'] for row in option_position_rows)
        if all_option_quotes_loaded
        else None
    )
    overall_option_pnl = (
        options_summary['net_cash_flow'] + net_current_option_value
        if net_current_option_value is not None
        else None
    )

    st.markdown("### Options Overview")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Open Positions", f"{len(open_options)} positions")
    with col2:
        st.metric(
            "Long Contracts",
            f"{int(options_summary['open_long_contracts'])}",
        )
    with col3:
        st.metric(
            "Short Contracts",
            f"{int(options_summary['open_short_contracts'])}",
        )
    with col4:
        st.metric(
            "Net Current Options Value",
            format_currency(net_current_option_value),
        )

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        st.metric(
            "Open Premiums Paid",
            format_currency(options_summary['open_premium_paid']),
        )
    with col6:
        st.metric(
            "Open Premiums Received",
            format_currency(options_summary['open_premium_received']),
        )
    with col7:
        st.metric(
            "Net Option Cash Flow",
            format_currency(options_summary['net_cash_flow']),
        )
    with col8:
        st.metric("Overall Options P/L", format_currency(overall_option_pnl))

    st.caption(
        "Current option values use the latest Yahoo Finance bid/ask midpoint. "
        "Long values are positive; short option liabilities are negative. "
        "The premium totals above reflect only the open positions still on the "
        "book. BTC closing costs and STC closing proceeds are shown separately "
        "in the activity table and remain included in Net Option Cash Flow. "
        "Assignment values are excluded."
    )

    st.markdown("---")
    st.markdown("### Open Option Positions")
    if option_position_rows:
        option_positions_df = pd.DataFrame(option_position_rows)
        # Missing Yahoo quotes produce ``None`` position values. Coerce them
        # to NaN before taking the absolute value so unavailable quotes sort
        # last instead of raising ``bad operand type for abs(): NoneType``.
        option_positions_df['_Exposure'] = pd.to_numeric(
            option_positions_df['Current Position Value'], errors='coerce'
        ).abs()
        option_positions_df = option_positions_df.sort_values(
            '_Exposure', ascending=False, na_position='last'
        ).drop(columns=['_Exposure'])
        st.dataframe(
            option_positions_df,
            width="stretch",
            hide_index=True,
            column_config={
                'Expiration': st.column_config.DateColumn(format="MM/DD/YYYY"),
                'Strike': st.column_config.NumberColumn(format="dollar"),
                'Contracts': st.column_config.NumberColumn(format="%.0f"),
                'Premium Paid': st.column_config.NumberColumn(format="dollar"),
                'Premium Received': st.column_config.NumberColumn(format="dollar"),
                'Current Option Price': st.column_config.NumberColumn(
                    format="dollar"
                ),
                'Current Position Value': st.column_config.NumberColumn(
                    format="dollar"
                ),
                'Unrealized Gain/Loss': st.column_config.NumberColumn(
                    format="dollar"
                ),
            },
        )
    else:
        st.info("No open option positions found in the CSV file.")

    st.markdown("---")
    st.markdown("### Premium Summary by Instrument")
    instrument_summary_df = pd.DataFrame(options_summary["instrument_rows"])
    st.caption(
        "This table groups the open option book by instrument and shows the "
        "premium currently tied to each symbol. Positive values are premiums "
        "received on open short positions; negative values are premiums paid "
        "on open long positions."
    )
    st.dataframe(
        instrument_summary_df,
        width="stretch",
        hide_index=True,
        column_config={
            'Open Contracts': st.column_config.NumberColumn(format="%.0f"),
            'Premium Paid': st.column_config.NumberColumn(format="dollar"),
            'Premium Received': st.column_config.NumberColumn(format="dollar"),
            'Net Premium': st.column_config.NumberColumn(format="dollar"),
        },
    )

    st.markdown("---")
    st.markdown("### Lifetime Premium Summary by Instrument")
    lifetime_instrument_summary_df = pd.DataFrame(
        options_summary["lifetime_instrument_rows"]
    )
    st.caption(
        "This table includes every option trade in the CSV for each instrument. "
        "Premium Paid includes BTO and BTC activity; Premium Received includes "
        "STO and STC activity. Assignment and expiration rows are excluded "
        "because they do not carry a premium amount."
    )
    lifetime_selection = st.dataframe(
        lifetime_instrument_summary_df,
        width="stretch",
        hide_index=True,
        key="lifetime_instrument_summary",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            'Transactions': st.column_config.NumberColumn(format="%.0f"),
            'Premium Paid': st.column_config.NumberColumn(format="dollar"),
            'Premium Received': st.column_config.NumberColumn(format="dollar"),
            'Net Premium': st.column_config.NumberColumn(format="dollar"),
        },
    )

    if lifetime_instrument_summary_df.empty:
        st.info("No lifetime option summary data is available.")
    else:
        selected_rows = []
        if isinstance(lifetime_selection, dict):
            selected_rows = lifetime_selection.get("selection", {}).get("rows", [])
        else:
            selected_rows = getattr(
                getattr(lifetime_selection, "selection", {}),
                "rows",
                [],
            )

        if selected_rows:
            selected_index = selected_rows[0]
            if 0 <= selected_index < len(lifetime_instrument_summary_df):
                selected_instrument = lifetime_instrument_summary_df.iloc[
                    selected_index
                ]["Instrument"]

                st.markdown(f"### Transactions for {selected_instrument}")
                instrument_txn_df = pd.DataFrame(
                    [
                        row
                        for row in options_summary["transaction_rows"]
                        if row["Instrument"] == selected_instrument
                    ]
                )
                st.dataframe(
                    instrument_txn_df,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        'Quantity': st.column_config.NumberColumn(format="%.0f"),
                        'Price': st.column_config.NumberColumn(format="dollar"),
                        'Amount': st.column_config.NumberColumn(format="dollar"),
                    },
                )
        else:
            st.info("Click an instrument row above to show its transaction history.")

    st.markdown("---")
    st.markdown("### Purchased and Sold Options Activity")
    activity_df = pd.DataFrame(options_summary['activity_rows'])
    st.dataframe(
        activity_df,
        width="stretch",
        hide_index=True,
        column_config={
            'Contracts': st.column_config.NumberColumn(format="%.0f"),
            'Cash Paid': st.column_config.NumberColumn(format="dollar"),
            'Cash Received': st.column_config.NumberColumn(format="dollar"),
        },
    )

    with st.expander("Option Transaction History"):
        transaction_df = pd.DataFrame(options_summary['transaction_rows'])
        st.dataframe(
            transaction_df,
            width="stretch",
            hide_index=True,
            column_config={
                'Quantity': st.column_config.NumberColumn(format="%.0f"),
                'Price': st.column_config.NumberColumn(format="dollar"),
                'Amount': st.column_config.NumberColumn(format="dollar"),
            },
        )


def render_dividends_tab(dividends_df: pd.DataFrame) -> None:
    """Render lifetime and per-instrument dividend summaries."""
    if dividends_df.empty:
        st.info("No dividend payments found in the uploaded CSV file.")
        return

    summary = calculate_dividend_summary(dividends_df)
    st.subheader("Dividend Income Summary")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Gross Dividends", format_currency(summary['gross_total']))
    with col2:
        st.metric(
            "Foreign Tax Withheld",
            format_currency(summary['tax_withheld']),
        )
    with col3:
        st.metric(
            "Total Dividends Received (Net)",
            format_currency(summary['net_total']),
        )
    with col4:
        st.metric("Dividend Payments", f"{summary['payment_count']}")

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        st.metric("Cash Dividends", format_currency(summary['cash_total']))
    with col6:
        st.metric(
            "Manufactured Dividends",
            format_currency(summary['manufactured_total']),
        )
    with col7:
        st.metric("Instruments Paying Dividends", f"{summary['instrument_count']}")
    with col8:
        coverage = (
            f"{summary['first_payment']:%m/%d/%Y} – "
            f"{summary['last_payment']:%m/%d/%Y}"
            if summary['first_payment'] and summary['last_payment']
            else "N/A"
        )
        st.metric("Dividend History", coverage)

    st.caption(
        "Gross dividends include regular cash dividends and manufactured "
        "dividends. Net dividends subtract foreign tax withholding recorded "
        "in the CSV."
    )

    st.markdown("---")
    st.markdown("### Dividends by Instrument")
    instrument_df = pd.DataFrame(summary['instrument_rows'])
    st.caption(
        "Sorted by Net Dividends Received (highest first). Click any column "
        "header to sort ascending or descending."
    )
    st.dataframe(
        instrument_df,
        width="stretch",
        hide_index=True,
        column_config={
            'Payments': st.column_config.NumberColumn(format="%d"),
            'Cash Dividends': st.column_config.NumberColumn(format="dollar"),
            'Manufactured Dividends': st.column_config.NumberColumn(
                format="dollar"
            ),
            'Gross Dividends': st.column_config.NumberColumn(format="dollar"),
            'Foreign Tax Withheld': st.column_config.NumberColumn(
                format="dollar"
            ),
            'Net Dividends Received': st.column_config.NumberColumn(
                format="dollar"
            ),
            'First Payment': st.column_config.DateColumn(format="MM/DD/YYYY"),
            'Last Payment': st.column_config.DateColumn(format="MM/DD/YYYY"),
        },
    )

    st.markdown("---")
    st.markdown("### Dividend Payment Details")
    transactions_df = pd.DataFrame(summary['transaction_rows'])
    st.dataframe(
        transactions_df,
        width="stretch",
        hide_index=True,
        column_config={
            'Activity Date': st.column_config.DateColumn(format="MM/DD/YYYY"),
            'Record Date': st.column_config.DateColumn(format="MM/DD/YYYY"),
            'Payment Date': st.column_config.DateColumn(format="MM/DD/YYYY"),
            'Eligible Shares': st.column_config.NumberColumn(format="%.4f"),
            'Rate Per Share': st.column_config.NumberColumn(format="dollar"),
            'Gross Dividend': st.column_config.NumberColumn(format="dollar"),
            'Tax Withheld': st.column_config.NumberColumn(format="dollar"),
            'Net Amount': st.column_config.NumberColumn(format="dollar"),
        },
    )


def main():
    st.set_page_config(
        page_title="Stock Portfolio Analyzer",
        page_icon="📈",
        layout="wide"
    )
    
    st.title("📈 Stock Portfolio Analyzer")
    st.markdown("---")
    
    # File upload section
    st.subheader("Upload Your Transaction CSV")
    uploaded_file = st.file_uploader("Choose a CSV file", type="csv")
    
    if uploaded_file is not None:
        # Save uploaded file temporarily
        temp_path = "temp_transactions.csv"
        with open(temp_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        try:
            # Parse the CSV
            stocks_df, options_df, dividends_df = parse_csv(
                temp_path, include_dividends=True
            )
            
            # Get holdings summary
            holdings = get_holding_summary(stocks_df)
            
            # Create tabs
            tab1, tab2, tab3 = st.tabs([
                "📊 Stock Holdings",
                "📋 Options Positions",
                "💵 Dividends",
            ])
            
            # TAB 1: Stock Holdings
            with tab1:
                if holdings:
                    st.subheader("Stock Holdings Summary")
                    
                    # Get current prices
                    with st.spinner("Fetching current prices from Yahoo Finance..."):
                        instruments = list(holdings.keys())
                        current_prices = get_current_prices(instruments)

                    missing_prices = [
                        symbol
                        for symbol, price in current_prices.items()
                        if price is None
                    ]
                    if missing_prices:
                        st.error(
                            "Yahoo Finance did not return a current price for: "
                            + ", ".join(missing_prices)
                            + ". Current portfolio totals are unavailable until "
                            "all prices load. Check the internet connection and rerun."
                        )
                    else:
                        st.success(
                            f"Loaded current Yahoo Finance prices for all "
                            f"{len(current_prices)} holdings."
                        )
                    
                    # Calculate metrics
                    portfolio_metrics = calculate_portfolio_metrics(holdings, current_prices)
                    
                    # Display portfolio overview
                    st.markdown("### Portfolio Overview")
                    col1, col2, col3, col4 = st.columns(4)
                    
                    total_shares_all = sum(m['total_shares'] for m in portfolio_metrics.values())
                    total_cost_all = sum(m['total_cost'] for m in portfolio_metrics.values())
                    all_prices_loaded = not missing_prices
                    total_value_all = (
                        sum(m['current_value'] for m in portfolio_metrics.values())
                        if all_prices_loaded
                        else None
                    )
                    total_gain_loss = (
                        total_value_all - total_cost_all
                        if total_value_all is not None
                        else None
                    )
                    
                    with col1:
                        st.metric("Total Holdings", f"{int(total_shares_all)} shares")
                    with col2:
                        st.metric("Total Cost Basis", format_currency(total_cost_all))
                    with col3:
                        st.metric("Current Value", format_currency(total_value_all))
                    with col4:
                        gain_loss_pct = (
                            total_gain_loss / total_cost_all * 100
                            if total_cost_all and total_gain_loss is not None
                            else None
                        )
                        st.metric("Unrealized Gain/Loss", format_currency(total_gain_loss), format_percentage(gain_loss_pct))
                    
                    st.markdown("---")
                    
                    # Display individual holdings
                    st.markdown("### Individual Holdings")
                    
                    # Create a summary table
                    summary_data = []
                    for instrument, metrics in portfolio_metrics.items():
                        summary_data.append({
                            'Instrument': instrument,
                            'Shares': metrics['total_shares'],
                            'Avg Cost/Share': metrics['avg_cost_per_share'],
                            'Total Cost': metrics['total_cost'],
                            'Current Price': metrics['current_price'],
                            'Total Equity Value': metrics['current_value'],
                            'Unrealized Gain/Loss': metrics['unrealized_gain_loss'],
                            'Gain/Loss %': metrics['unrealized_gain_loss_pct']
                        })
                     
                    summary_df = pd.DataFrame(summary_data).sort_values(
                        'Total Equity Value', ascending=False, na_position='last'
                    )
                    st.caption(
                        "Sorted by Total Equity Value (highest first). "
                        "Click any column header to sort ascending or descending."
                    )
                    st.dataframe(
                        summary_df,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            'Shares': st.column_config.NumberColumn(format="%.4f"),
                            'Avg Cost/Share': st.column_config.NumberColumn(format="dollar"),
                            'Total Cost': st.column_config.NumberColumn(format="dollar"),
                            'Current Price': st.column_config.NumberColumn(format="dollar"),
                            'Total Equity Value': st.column_config.NumberColumn(format="dollar"),
                            'Unrealized Gain/Loss': st.column_config.NumberColumn(format="dollar"),
                            'Gain/Loss %': st.column_config.NumberColumn(format="%.2f%%"),
                        },
                    )
                    
                    st.markdown("---")
                    
                    # Expandable transaction details
                    st.markdown("### Transaction Details")
                    for instrument, metrics in portfolio_metrics.items():
                        with st.expander(f"📌 {instrument} Transactions"):
                            txn_df = pd.DataFrame(metrics['transactions'])
                            st.dataframe(txn_df, width="stretch", hide_index=True)
                else:
                    st.info("No stock holdings found in the uploaded CSV file.")
            
            # TAB 2: Options Positions
            with tab2:
                render_options_tab(options_df)

            # TAB 3: Dividends
            with tab3:
                render_dividends_tab(dividends_df)
        
        except Exception as e:
            st.error(f"Error processing file: {str(e)}")
        
        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    else:
        st.info("👆 Upload a CSV file to get started with your portfolio analysis.")


if __name__ == "__main__":
    main()
