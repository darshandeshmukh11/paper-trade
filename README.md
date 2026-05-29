# Paper trading (Indian equities)

Web app and Excel workbook for virtual NSE/BSE paper trading with approximate Indian brokerage charges.

**Symbols:** Full NSE list (~2,100+ symbols) plus **Nifty 50** and **Nifty 100** filters on the New trade tab. Search by partial name (e.g. `TATAST` → TATASTEEL).

## Run the web app

```bash
cd /Users/admin/Desktop/Codebase/ri/test/paper-trade
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run paper_trading_app.py
```

Open http://localhost:8501 — trades persist in `data/paper_trades.db`.

Optional password: copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` and set `PAPER_PASSWORD`.

## Excel workbook

```bash
python build_paper_trading_sheet.py
# → paper_trading_india.xlsx
```

## Deploy online

See [DEPLOY_PAPER_TRADING.md](DEPLOY_PAPER_TRADING.md). Streamlit Cloud **main file path**: `test/paper-trade/paper_trading_app.py`.

## Layout

| Path | Purpose |
|------|---------|
| `paper_trading_app.py` | Streamlit UI |
| `paper_trading/` | Charges, SQLite store, portfolio math |
| `build_paper_trading_sheet.py` | Generate Excel template |
| `data/paper_trades.db` | Local trade database |
