# ORB Scanner

Automated pre-market scanner for high-probability micro-cap and small-cap day trading
setups — Opening Range Breakout (ORB), First Pullback, VWAP, and Momentum Breakout.

> **Two ways to run this, pick one:**
> - **Self-hosted (Docker, this document)** — Next.js + FastAPI, run on your own
>   machine or server. More control, more setup.
> - **Fully hosted, no server to manage** — see **[`cloud/README.md`](cloud/README.md)**
>   for a GitHub Actions + Supabase + Streamlit Cloud setup with a plain step-by-step
>   walkthrough (no command line required). Good fit if you just want a link you can
>   open, with nothing running on your own computer.

> **Build status: working foundation, not the full spec yet.** This is phase one of a
> larger build. Everything below is real, tested, and runs — the full original brief
> (5-provider parity, alerts wired to the scheduler, backtesting engine, full settings
> UI, halts/short-interest feeds) is scoped as follow-up phases. See
> [What's built vs. what's next](#whats-built-vs-whats-next).

## Stack

| Layer      | Tech |
|------------|------|
| Frontend   | Next.js 14 (App Router) · React 18 · TypeScript · Tailwind CSS |
| Backend    | Python 3.12 · FastAPI · SQLAlchemy (async) |
| Database   | PostgreSQL 16 |
| Cache      | Redis 7 (provisioned, not yet used by the app — see below) |
| Scheduler  | APScheduler (cron jobs, 5 daily scan slots) |
| Market data| Polygon.io (fully wired) · Finnhub / Alpaca / TwelveData / IB (stub adapters, same interface) |
| Deployment | Docker Compose |

## Folder structure

```
orb-scanner/
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app, CORS, lifespan (DB init + scheduler)
│   │   ├── core/
│   │   │   ├── config.py            # All env-driven settings (Pydantic Settings)
│   │   │   ├── database.py          # Async SQLAlchemy engine/session
│   │   │   └── logging_config.py
│   │   ├── models/models.py         # ScanRun, ScanResult, WatchlistItem, AppSetting
│   │   ├── schemas/schemas.py       # Pydantic I/O contracts
│   │   ├── providers/               # Pluggable market-data adapters
│   │   │   ├── base.py              # DataProvider interface every vendor implements
│   │   │   ├── polygon_provider.py  # Fully implemented (cheap-filter -> enrich funnel)
│   │   │   ├── finnhub_provider.py  # Stub - same interface, ready to fill in
│   │   │   ├── alpaca_provider.py   # Stub
│   │   │   ├── twelvedata_provider.py # Stub
│   │   │   ├── ib_provider.py       # Stub
│   │   │   └── factory.py           # Picks the active provider from DATA_PROVIDER env
│   │   ├── services/
│   │   │   ├── scanner.py           # Orchestrates fetch -> filter -> score -> persist
│   │   │   ├── scoring.py           # 0-100 weighted scoring engine
│   │   │   ├── catalyst.py          # News keyword classifier (FDA, Earnings, AI, ...)
│   │   │   ├── scheduler.py         # APScheduler cron jobs for the 5 scan times
│   │   │   └── alerts.py            # Discord/Telegram/Email senders (not yet triggered
│   │   │                            #   automatically - see What's next)
│   │   └── api/routes.py            # /scans, /stocks, /watchlist, /settings, /health
│   ├── tests/                       # 18 unit tests, all passing (scoring, catalysts, filters)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
└── frontend/
    ├── app/
    │   ├── layout.tsx                # Root layout, font loading
    │   ├── page.tsx                  # Scanner table page
    │   └── stock/[ticker]/page.tsx   # Detail page: trading plan, catalyst, score breakdown
    ├── components/
    │   ├── scanner-table.tsx         # Sortable 19-column table
    │   ├── score-badge.tsx           # Score gauge + risk pill
    │   └── ui/                       # button.tsx, badge.tsx
    ├── lib/{api.ts,utils.ts}
    ├── types/stock.ts
    ├── package.json
    ├── Dockerfile
    └── .env.local.example
```

There's also a `cloud/` folder (standalone scan job + Streamlit dashboard) and
`.github/workflows/scan.yml` (the GitHub Actions schedule) for the hosted deployment
path — see [`cloud/README.md`](cloud/README.md) for that walkthrough. It reuses the
same provider/scoring/catalyst logic as `backend/`, ported to be synchronous and
FastAPI-free so it can run as a short-lived scheduled job instead of a server.

## Installation

### Option A — Docker Compose (recommended)

```bash
git clone <this-repo> orb-scanner && cd orb-scanner

cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local
# Edit backend/.env and add POLYGON_API_KEY for live scans (optional - see below)

docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API docs (Swagger): http://localhost:8000/docs
- Backend health check: http://localhost:8000/api/v1/health

### Option B — Run locally without Docker

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# For local (non-Docker) Postgres, point DATABASE_URL at localhost instead of "postgres"
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

### Running without a Polygon API key

The app works out of the box with **no API key**: `PolygonProvider` detects the
missing key and serves a small bundled sample dataset (clearly labeled `DEMO1`-`DEMO5`,
not real securities) so the full scan -> score -> store -> UI pipeline is demoable
immediately. Set `POLYGON_API_KEY` in `backend/.env` to switch to live market data -
no code changes needed.

## Switching data providers

Change one line in `backend/.env`:

```
DATA_PROVIDER=polygon   # or: finnhub | alpaca | twelvedata | ib
```

`finnhub`, `alpaca`, `twelvedata`, and `ib` are scaffolded with the same
`DataProvider` interface as Polygon (see `app/providers/base.py`) and currently
return the same mock dataset with a warning logged - they're the extension points
for wiring in real API calls without touching the scanner or scoring engine at all.

## Running tests

```bash
cd backend
pytest tests/ -v
```

18 tests covering the scoring engine, catalyst detection, and filter logic - all
pure-logic, no network or DB required. Verified passing as part of this build.

## API documentation

Full interactive docs (generated from the FastAPI schema) are at `/docs` (Swagger)
and `/redoc` once the backend is running. Key endpoints:

| Method | Path | Description |
|--------|------|--------------|
| GET | `/api/v1/scans/latest` | Most recent scan run + all results, sorted by score |
| GET | `/api/v1/scans/{id}` | A specific historical scan run |
| GET | `/api/v1/scans?limit=20` | Recent scan run summaries |
| POST | `/api/v1/scans/run` | Manually trigger a scan outside the fixed schedule |
| GET | `/api/v1/stocks/{ticker}` | Latest data for one ticker + derived trading plan |
| GET | `/api/v1/watchlist` | List watchlist |
| POST | `/api/v1/watchlist` | Add a ticker |
| DELETE | `/api/v1/watchlist/{ticker}` | Remove a ticker |
| GET | `/api/v1/settings` | Current filters + scoring weights |
| PUT | `/api/v1/settings/filters` | Update filter thresholds |
| GET | `/api/v1/health` | App + active provider status (live vs. mock mode) |

## Deployment guide

1. **Provision Postgres and Redis** (managed services recommended for prod - RDS/Cloud
   SQL for Postgres, ElastiCache/Memorystore for Redis - or keep the Compose services
   behind a reverse proxy for a single-box deploy).
2. **Set real secrets** in `backend/.env` (`POLYGON_API_KEY`, alert webhook URLs, SMTP
   credentials) - never commit `.env`, it's already git-ignored.
3. **Build and push images**:
   ```bash
   docker compose build
   docker tag orb-scanner-backend:latest <registry>/orb-scanner-backend:latest
   docker tag orb-scanner-frontend:latest <registry>/orb-scanner-frontend:latest
   docker push <registry>/orb-scanner-backend:latest
   docker push <registry>/orb-scanner-frontend:latest
   ```
4. **Migrations**: this build creates tables via `Base.metadata.create_all` on startup
   for simplicity. Before production, swap in Alembic migrations (`alembic` is already
   in `requirements.txt`) so schema changes are versioned instead of implicit.
5. Point `NEXT_PUBLIC_API_URL` (frontend) at the backend's public URL, and set
   `CORS_ORIGINS` (backend) to the frontend's public URL.
6. Terminate TLS at a reverse proxy (nginx/Caddy/your cloud LB) in front of both
   services; neither container serves HTTPS directly.
7. The frontend's `package.json` pins Next.js 14.2.35 (latest patched 14.x at time of
   writing) — `npm audit` still flags advisories that require a Next 16 major upgrade
   to fully clear (mostly server-side DoS/caching issues). Evaluate that upgrade before
   a public-facing production deploy.

## Known simplifications (documented in code, not hidden)

- **Average volume / ATR**: computed from a real 20-day daily-bar lookback, but only
  for candidates that already survived the cheap price/gap filter - see the docstring
  in `polygon_provider.py` for why (API cost at full-market scale).
- **Spread %**: Polygon's real-time NBBO quotes require a higher plan tier; falls back
  to a float-based heuristic if unavailable. Clearly marked in code.
- **Short interest & trading halts**: Polygon doesn't provide these at all. Fields
  exist in the schema and UI (ready to populate) but need a second data source (e.g.
  FINRA short-interest files, an exchange halts feed, or a vendor like Benzinga).
- **Redis**: provisioned in Compose but not yet used for caching - the natural next
  step is caching the bulk snapshot response between the cheap and enrich passes.

## What's built vs. what's next

**Built and verified working (18 backend tests passing, real scan -> API -> UI flow
exercised end-to-end):**
- Pluggable provider architecture (Polygon fully wired; 4 more stubbed behind the
  same interface)
- Filter pipeline (price, gap %, float, market cap, premarket volume, relative
  volume, ETF/preferred/warrant exclusion)
- Catalyst detection across all 11 requested categories
- Weighted 0-100 scoring engine, fully configurable, with per-factor breakdown
- APScheduler running the 5 daily scan times automatically
- Scan history persisted to Postgres
- Sortable scanner table (all 19 requested columns) and stock detail page
  (trading plan, catalyst, score breakdown, key stats)
- Watchlist add/list/remove
- Discord/Telegram/Email alert senders (functions are complete and callable)

**Scoped as next phases:**
- Wire `alerts.py` into the scheduler's trigger conditions (new-pass, score
  threshold, ORB breakout, VWAP reclaim, new PM high, volume spike) + browser push
- Intraday/premarket candlestick charts (TradingView Lightweight Charts) via a new
  `/candles` endpoint
- Full Settings UI (currently API-only - `GET/PUT /settings`)
- Backtesting module (win rate, avg R, profit factor, max drawdown, expectancy)
- Real implementations for Finnhub / Alpaca / TwelveData / IB adapters
- Short interest + halts feed integration
- Alembic migrations in place of `create_all`
