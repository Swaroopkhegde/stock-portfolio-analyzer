# Stock Portfolio Analyzer

A Streamlit web application for analyzing your investment portfolio. Upload your transaction CSV file to get detailed insights into your stock holdings and options positions.

## Features

- **CSV File Upload**: Upload your transaction history
- **Stock Holdings Analysis**: 
  - Total shares held per stock
  - Average cost basis
  - Total cost invested
  - Current market prices (fetched from Yahoo Finance)
  - Current portfolio value
  - Unrealized gains/losses
  - Transaction history
- **Options Positions**: Separate tracking of all options trades
- **Real-time Pricing**: Automatic price updates from Yahoo Finance

## Project Setup

This project uses **Uv** for Python package management.

### Prerequisites

- Python 3.9+
- Uv package manager

### Installation

1. Install dependencies:
```bash
uv sync
```

### Running the App

1. Start the Streamlit application:
```bash
streamlit run app.py
```

2. Open your browser and navigate to `http://localhost:8501`

3. Upload your transaction CSV file to analyze your portfolio

## CSV Format

The app expects a CSV file with the following columns:
- Activity Date
- Process Date
- Settle Date
- Instrument (stock symbol)
- Description
- Trans Code (Buy, Sell, STO, BTC, etc.)
- Quantity
- Price
- Amount

## Project Structure

```
StockAnalyzer/
├── app.py                 # Main Streamlit application
├── pyproject.toml         # Uv project configuration
└── utils/
    ├── __init__.py
    ├── csv_parser.py      # CSV parsing and data processing
    └── portfolio_analyzer.py  # Portfolio metrics calculation
```

## Dependencies

- **streamlit**: Web application framework
- **pandas**: Data manipulation and analysis
- **yfinance**: Yahoo Finance data fetching
- **numpy**: Numerical computations

## Future Enhancements

- Additional tabs for performance analysis, risk metrics, etc.
- Portfolio rebalancing suggestions
- Tax loss harvesting reports
- Portfolio comparison and benchmarking

## License

MIT
