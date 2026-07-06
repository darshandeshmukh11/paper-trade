# Paper trading (Indian equities)

Web app and Excel workbook for virtual NSE/BSE paper trading with Zerodha brokerage calculator charges.

**Symbols:** Full NSE list (~2,100+ symbols) plus **Nifty 50** and **Nifty 100** filters on the New trade tab.

## Run the web app

```bash
cd test/paper-trade
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run paper_trading_app.py
```

Open http://localhost:8501

| Storage | When |
|---------|------|
| **Supabase** | `SUPABASE_DB_URL` in Streamlit secrets or env — see [DEPLOY_SUPABASE.md](DEPLOY_SUPABASE.md) |
| **paper_trades.db** | Local SQLite in this folder (no Supabase URL) |

## Excel workbook (no hosting)

```bash
python build_paper_trading_sheet.py
python build_paper_trading_sheet.py --import ~/Downloads/paper_trading_backup.json
python build_paper_trading_sheet.py --refresh
```

Output: `paper_trading_india.xlsx` in this folder.

## Files (flat layout)

| File | Purpose |
|------|---------|
| `paper_trading_app.py` | Streamlit UI |
| `store.py`, `db.py` | SQLite or Supabase persistence |
| `charges.py`, `portfolio.py` | Brokerage math, P&L |
| `nse_symbols.py`, `nifty_indices.py`, `live_price.py` | Symbols and LTP |
| `build_paper_trading_sheet.py`, `excel_workbook.py` | Excel export |
| `schema.sql` | Run once in Supabase SQL editor |
| `nse_equity_symbols.json` | Cached NSE symbol list |
| `secrets.toml.example` | Local / reference secrets |
