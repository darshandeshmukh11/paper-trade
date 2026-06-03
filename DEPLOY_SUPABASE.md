# Paper trading on Streamlit Cloud + Supabase

Trades persist in **Supabase Postgres** instead of ephemeral container disk.

## 1. Create tables in Supabase

1. Open your project → **SQL Editor** → **New query**.
2. Paste the contents of [`schema.sql`](schema.sql) in this folder.
3. Click **Run**.

## 2. Get the database connection string

1. **Project Settings** → **Database**.
2. Under **Connection string**, choose **URI**.
3. Select **Transaction pooler** (port **6543**) — works best with Streamlit Cloud.
4. Replace `[YOUR-PASSWORD]` with your database password.
5. Copy the full URI (starts with `postgresql://`).

## 3. Streamlit Cloud secrets

App → **Settings** → **Secrets**:

```toml
PAPER_PASSWORD = "your-strong-password"

SUPABASE_DB_URL = "postgresql://postgres.xxxxx:YOUR_PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
```

Redeploy the app. Sidebar should show **Storage: Supabase**.

## 4. Import existing trades (one time)

1. Open the app → **Settings**.
2. **Import backup JSON** → **Import (replace all data)**.

## Local development

```bash
cd test/paper-trade
pip install -r requirements.txt
export SUPABASE_DB_URL="postgresql://..."
export PAPER_PASSWORD="..."
streamlit run paper_trading_app.py
```

Or copy `secrets.toml.example` → `secrets.toml` (same folder). Streamlit loads `secrets.toml` from the app directory when present.

Without `SUPABASE_DB_URL`, the app uses `paper_trades.db` in this folder (SQLite).

## Optional theme

`streamlit_config.toml` is a reference copy. Streamlit expects `.streamlit/config.toml` by default; the app also applies a dark theme in code.
