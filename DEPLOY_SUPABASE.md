# Streamlit Cloud + Supabase

## Step 1 — Create tables (required)

Supabase → **SQL Editor** → paste all of [`schema.sql`](schema.sql) → **Run**.

## Step 2 — Streamlit secrets (copy exactly)

```toml
PAPER_PASSWORD = "your-app-password"

SUPABASE_PROJECT_REF = "jrtqpdjrsxnmsdxguqlg"
SUPABASE_REGION = "ap-southeast-1"
SUPABASE_PASSWORD = "paste-database-password-here"
```

Get the password: **Project Settings → Database → Database password** (click **Reset** if unsure).

**Do not** use `[YOUR-PASSWORD]` or your Supabase login password.

Remove `SUPABASE_DB_URL` if you use the three secrets above.

## Step 3 — Redeploy

Sidebar should show **Storage: Supabase**.

## If it still fails — paste exact host from Supabase

1. Supabase project home → **Connect** → **ORMs** / **URI**
2. Copy **Session pooler** (port **5432**) host and user
3. Add to secrets:

```toml
SUPABASE_HOST = "aws-0-ap-southeast-1.pooler.supabase.com"
SUPABASE_PORT = "5432"
SUPABASE_USER = "postgres.jrtqpdjrsxnmsdxguqlg"
SUPABASE_PASSWORD = "your-database-password"
SUPABASE_DB = "postgres"
```

The app tries session pooler (5432) before transaction pooler (6543).

## Import old trades

**Settings → Import backup JSON → Replace all data**
