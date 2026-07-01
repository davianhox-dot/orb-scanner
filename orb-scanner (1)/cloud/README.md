# ORB Scanner — Hosted Setup (No Command Line Required)

This is the "everything hosted, I just open a link" version of the scanner. It's
made of three free services that each do one job:

| Service | Job | Cost |
|---|---|---|
| **GitHub Actions** | Runs the scan automatically at 08:00/08:30/09:00/09:20/09:28 ET | Free |
| **Supabase** (or Neon) | Stores the scan results | Free |
| **Streamlit Community Cloud** | The dashboard you actually look at | Free |

You'll set each of these up once, through their normal websites — no terminal, no
`git` commands. Takes about 15 minutes. Total ongoing cost: $0, unless you choose to
upgrade your Polygon.io plan for live data (see [About live data](#about-live-data-read-this-part)
below — this is the one part worth reading carefully before you decide what to expect).

---

## Step 1 — Create your database (Supabase)

1. Go to **[supabase.com](https://supabase.com)** and sign up (free, no credit card).
2. Click **New Project**. Give it any name, e.g. `orb-scanner`. Set a database
   password — write it down somewhere, you'll need it in a moment.
3. Wait ~2 minutes for the project to finish setting up.
4. In the left sidebar, click the **Connect** button (top of the page) → find
   **Connection string** → choose the **URI** tab → copy it. It looks like:
   ```
   postgresql://postgres.xxxxxxxx:[YOUR-PASSWORD]@aws-0-xxxx.pooler.supabase.com:5432/postgres
   ```
5. Replace `[YOUR-PASSWORD]` in that string with the database password from step 2.
   Save this whole string somewhere — this is your `DATABASE_URL`, and you'll paste
   it into two places in the next steps.

*(Neon.tech works identically if you'd rather use that — same idea, same kind of
connection string, just a different signup page.)*

## Step 2 — Put the code on GitHub

1. Go to **[github.com](https://github.com)** and sign up if you don't have an
   account (free).
2. Click the **+** in the top right → **New repository**. Name it `orb-scanner`,
   leave it **Public** or **Private** (either works fine), don't add a README, and
   click **Create repository**.
3. On the new repo's page, click **uploading an existing file**.
4. Unzip the project file I gave you on your computer, then **drag the entire
   unzipped folder** onto that upload page (modern browsers let you drop a whole
   folder, not just individual files). GitHub will recreate the folder structure
   automatically.
5. Scroll down, click **Commit changes**.

That's it — no `git` install, no command line.

## Step 3 — Tell GitHub Actions your database connection

1. In your new repo, click **Settings** (top menu) → **Secrets and variables** →
   **Actions** (left sidebar).
2. Click **New repository secret**.
   - Name: `DATABASE_URL`
   - Value: paste the connection string from Step 1
   - Click **Add secret**
3. *(Optional, for live data instead of the sample demo data — see the note below
   before deciding)*: add another secret named `POLYGON_API_KEY` with your key from
   [polygon.io](https://polygon.io).
4. *(Optional, for alerts)*: add secrets named `DISCORD_WEBHOOK_URL`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_HOST`, `SMTP_USER`,
   `SMTP_PASSWORD`, `ALERT_EMAIL_TO` for whichever alert channels you want. Leave
   any of these out and that channel just stays off — nothing breaks.

## Step 4 — Turn on the scheduled scan

1. Click the **Actions** tab in your repo.
2. You should see a workflow called **Pre-Market Scan**. If GitHub shows a button
   asking you to enable Actions, click it.
3. Click **Pre-Market Scan** → **Run workflow** → **Run workflow** (green button).
   This runs it right now regardless of the time, so you can confirm everything
   works without waiting for tomorrow morning.
4. After ~30 seconds, refresh the page — you should see a green checkmark. Click
   into the run to see the log if you want to peek at what happened.
5. From here on, it runs itself automatically every trading morning at your 5 scan
   times — you don't need to do anything.

## Step 5 — Deploy the dashboard (Streamlit Cloud)

1. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with your
   GitHub account.
2. Click **Create app** → **From existing repo**.
3. Fill in:
   - **Repository**: your `orb-scanner` repo
   - **Branch**: `main`
   - **Main file path**: `cloud/streamlit_app/app.py`
4. Click **Advanced settings** → **Secrets**, and paste:
   ```toml
   DATABASE_URL = "postgresql://postgres.xxxxxxxx:...your string from Step 1..."
   ```
5. Click **Deploy**. After a minute or two, you'll get a permanent link like
   `https://your-app-name.streamlit.app` — that's your dashboard. Bookmark it.

You're done. Open that link any time to see the scanner table, click into a ticker
for its trading plan, or manage your watchlist. There's also a **Run Scan Now**
button on the dashboard itself if you don't want to wait for the next scheduled run.

---

## About live data (read this part)

Everything above works immediately with **zero API keys**, using small bundled
sample tickers (`DEMO1`-`DEMO5`) so you can see the whole thing work end to end.
To get *real* stocks, you need a Polygon.io API key — but here's the honest
picture on cost:

Polygon's free tier is quite limited for this use case: around 5 requests per
minute and delayed/end-of-day data rather than live pre-market quotes. This scanner
needs to check the whole market's pre-market activity in real time, which in
practice means Polygon's paid tier (their real-time stocks plan, commonly around
$29/month at the time of writing — **check polygon.io/pricing for current numbers**,
this changes). If you're just trying it out, the free key will get you set up and
you'll see it work, but expect it to be too rate-limited for genuinely useful live
scanning until you upgrade.

If that cost doesn't make sense for you yet, this is worth knowing *before* you pay
for anything, not after.

## Keeping it updated

If I send you code changes later, you'll re-upload the changed files the same way
as Step 2 (drag them into the repo's file browser, or use **Add file → Upload
files** on the specific folder) — GitHub Actions and Streamlit Cloud both pick up
changes automatically within a minute or two of a commit.

## If something looks wrong

- **Dashboard says "No scans yet"**: go to your repo's **Actions** tab and manually
  run the workflow (Step 4.3) — then refresh the Streamlit page.
- **Actions run shows a red X**: click into it, click the failed step, and read the
  error at the bottom — it's almost always a missing/mistyped secret (Step 3).
- **Streamlit page won't load**: check the app's logs from the Streamlit Cloud
  dashboard (the "Manage app" menu) — usually a missing `DATABASE_URL` secret.
