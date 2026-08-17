import csv

import pandas as pd


def _parse_numeric_column(series: pd.Series, *, currency: bool = False) -> pd.Series:
    """Convert exported numeric text to numbers without silently losing commas."""
    cleaned = series.astype(str).str.strip()
    if currency:
        cleaned = cleaned.str.replace('$', '', regex=False)
    cleaned = cleaned.str.replace(',', '', regex=False)
    cleaned = cleaned.str.replace(r'^\((.*)\)$', r'-\1', regex=True)
    # Robinhood marks shares removed by a corporate action with an "S"
    # suffix (for example, 200S).
    cleaned = cleaned.str.replace(r'^(\d+(?:\.\d+)?)S$', r'-\1', regex=True)
    cleaned = cleaned.replace({'': '0', 'nan': '0'})
    return pd.to_numeric(cleaned, errors='coerce').fillna(0)


def parse_csv(file_path: str, include_dividends: bool = False):
    """
    Parse the transaction CSV file and separate stocks from options.
    Handles multiline quoted fields and skips incomplete rows.
    
    Args:
        file_path: Path to the CSV file
        
    Returns:
        Tuple of (stocks_df, options_df). When ``include_dividends`` is True,
        returns (stocks_df, options_df, dividends_df).
    """
    # Read CSV with Python's csv module to handle multiline fields properly
    rows = []
    with open(file_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.reader(f)
        header = next(reader)
        num_fields = len(header)
        
        for row_num, row in enumerate(reader, start=2):
            # Only include rows with correct number of fields
            if len(row) == num_fields:
                rows.append(row)
            else:
                # Skip malformed rows (incomplete or extra fields)
                print(f"Skipping malformed row {row_num}: expected {num_fields} fields, got {len(row)}")
    
    df = pd.DataFrame(rows, columns=header)
    
    # Clean column names
    df.columns = df.columns.str.strip()
    
    # Brokerage exports quote currency values and use commas as thousands
    # separators. Strip those separators before numeric conversion.
    df['Amount'] = _parse_numeric_column(df['Amount'], currency=True)
    df['Price'] = _parse_numeric_column(df['Price'], currency=True)
    df['Quantity'] = _parse_numeric_column(df['Quantity'])
    
    # Separate stocks from options
    # Options contain "Put" or "Call" in the Description or have STO/BTC/BTO/STC in Trans Code
    option_trans_codes = ['STO', 'BTC', 'BTO', 'STC']
    
    options_mask = (
        df['Description'].str.contains('Put|Call', case=False, na=False) |
        df['Trans Code'].isin(option_trans_codes)
    )
    
    options_df = df[options_mask].copy()
    stocks_df = df[~options_mask & (df['Instrument'].notna()) & (df['Instrument'] != '')].copy()
    dividends_df = df[df['Trans Code'].isin(['CDIV', 'MDIV', 'DTAX'])].copy()
    
    # Include share-changing corporate actions as well as trades. Cash-only
    # activity (dividends, interest, deposits, etc.) does not affect holdings.
    share_codes = ['Buy', 'Sell', 'BCXL', 'SPL', 'SPR', 'MRGS', 'REC']
    stocks_df = stocks_df[stocks_df['Trans Code'].isin(share_codes)].copy()
    
    if include_dividends:
        return stocks_df, options_df, dividends_df
    return stocks_df, options_df


def get_holding_summary(stocks_df: pd.DataFrame) -> dict:
    """
    Calculate holdings summary by instrument.
    
    Args:
        stocks_df: DataFrame with stock transactions
        
    Returns:
        Dictionary with holdings summary
    """
    holdings = {}
    
    for instrument in stocks_df['Instrument'].unique():
        instrument_txns = stocks_df[stocks_df['Instrument'] == instrument].copy()
        instrument_txns['_activity_date'] = pd.to_datetime(
            instrument_txns['Activity Date'], errors='coerce'
        )
        instrument_txns = instrument_txns.sort_values('_activity_date')
        
        total_shares = 0
        total_cost = 0
        transactions = []
        
        for _, row in instrument_txns.iterrows():
            if row['Trans Code'] == 'Buy':
                total_shares += row['Quantity']
                total_cost += abs(row['Amount'])
            elif row['Trans Code'] == 'Sell':
                shares_sold = row['Quantity']
                avg_cost = total_cost / total_shares if total_shares > 0 else 0
                total_cost -= avg_cost * shares_sold
                total_shares -= shares_sold
            elif row['Trans Code'] == 'BCXL':
                # A cancelled buy reverses both the shares and the exact cash
                # amount of the original order.
                total_shares -= row['Quantity']
                total_cost -= abs(row['Amount'])
            elif row['Trans Code'] in ('SPL', 'SPR', 'MRGS', 'REC'):
                total_shares += row['Quantity']

            if abs(total_shares) < 1e-9:
                total_shares = 0.0
                total_cost = 0.0
            
            transactions.append({
                'Date': row['Activity Date'],
                'Type': row['Trans Code'],
                'Quantity': row['Quantity'],
                'Price': row['Price'],
                'Amount': row['Amount']
            })
        
        avg_cost_per_share = total_cost / total_shares if total_shares != 0 else 0
        
        # A holdings view should contain open long positions only. Closed and
        # fully removed securities otherwise appear as misleading $0 rows.
        if total_shares > 1e-9:
            holdings[instrument] = {
                'total_shares': total_shares,
                'total_cost': total_cost,
                'avg_cost_per_share': avg_cost_per_share,
                'transactions': transactions
            }
    
    return holdings


def get_options_summary(options_df: pd.DataFrame) -> dict:
    """
    Calculate options summary by instrument and strike.
    
    Args:
        options_df: DataFrame with options transactions
        
    Returns:
        Dictionary with options summary
    """
    options = {}
    
    for _, row in options_df.iterrows():
        instrument = row['Instrument']
        description = row['Description']
        
        key = f"{instrument} - {description}"
        
        if key not in options:
            options[key] = {
                'instrument': instrument,
                'description': description,
                'transactions': []
            }
        
        options[key]['transactions'].append({
            'Date': row['Activity Date'],
            'Type': row['Trans Code'],
            'Quantity': row['Quantity'],
            'Price': row['Price'],
            'Amount': row['Amount']
        })
    
    return options
