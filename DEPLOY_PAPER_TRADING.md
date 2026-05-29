# Host paper trading on Streamlit (access anytime)

## Option A — Streamlit Community Cloud (free public URL)

1. Push this repo to **GitHub**. Include:
   - `test/paper-trade/paper_trading_app.py`
   - `test/paper-trade/paper_trading/`
   - `test/paper-trade/requirements.txt`
   - `test/paper-trade/.streamlit/config.toml`

2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.

3. Set:
   - **Main file path**: `test/paper-trade/paper_trading_app.py`

4. **Secrets**:

```toml
PAPER_PASSWORD = "your-secret-password"
```

5. Deploy → URL like `https://your-app.streamlit.app`

**Note:** Cloud storage is ephemeral — use **Settings → Download backup** regularly.

---

## Option B — Run on your Mac (persistent DB)

```bash
cd /Users/admin/Desktop/Codebase/ri/test/paper-trade
source .venv/bin/activate
pip install -r requirements.txt
streamlit run paper_trading_app.py
```

Database: `data/paper_trades.db`

### Background (macOS)

```bash
cd /Users/admin/Desktop/Codebase/ri/test/paper-trade
nohup .venv/bin/streamlit run paper_trading_app.py --server.port 8502 >> /tmp/paper_trading.log 2>&1 &
```

---

## Option C — Private access (Tailscale / ngrok)

Run locally (Option B) and expose only to yourself.
