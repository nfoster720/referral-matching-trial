# Referral Matching Pilot

A small full-stack application for a referral-matching pilot: customer referrals
arrive from an upstream lead platform, the system identifies and ranks candidate
agents, agents claim referrals from a queue, and Operations monitors the whole
flow.

**If you were sent this repository as part of a hiring trial, start with
[CANDIDATE_BRIEF.md](CANDIDATE_BRIEF.md).**

All data in this repository is synthetic. Names, emails, and phone numbers are
fabricated (phones use the reserved 555-01xx range).

## Stack

- **Backend:** Python / Django / Django REST Framework / PostgreSQL
- **Frontend:** React / TypeScript / Vite
- **Tests:** pytest, Vitest + React Testing Library
- **Infra:** Docker Compose (PostgreSQL only)

## Prerequisites

- Python 3.11+
- Node 20+
- Docker with the Compose plugin
- GNU Make

## Quickstart

```bash
make setup     # start Postgres, create venv, install deps, run migrations
make seed      # load deterministic trial data (prints sign-in identities)
make dev       # run backend (:8000) and frontend (:5173) together
```

Then open http://localhost:5173 and pick an identity from the top-right
dropdown. `make seed` prints the full identity list; useful ones:

| Identity      | Role  | Token             | Notes                          |
| ------------- | ----- | ----------------- | ------------------------------ |
| `agent.carol` | agent | `tok-agent.carol` | Active Denver agent            |
| `agent.tessa` | agent | `tok-agent.tessa` | Dallas agent                   |
| `ops.riley`   | ops   | `tok-ops.riley`   | Operations dashboard access    |

Authentication uses DRF token auth. The identity dropdown is a development
convenience backed by `GET /api/identities/` (DEBUG only). For direct API
calls: `Authorization: Token tok-agent.carol`.

### Windows local development without Docker

For a lightweight local run, initialize the Python environment and frontend
dependencies once, then use SQLite:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Set-Location frontend
npm install
```

Open a backend PowerShell terminal at the repository root:

```powershell
$env:DJANGO_DEBUG = "1"
$env:DB_ENGINE = "sqlite"
Set-Location backend
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py seed_trial
..\.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

Open the second terminal at the repository root:

```powershell
Set-Location frontend
npm run dev
```

Open http://localhost:5173. SQLite supports local development but does not
provide PostgreSQL row-lock behavior; use PostgreSQL for concurrency testing.

Offer expiration and failed notification delivery are handled by management
commands, but the application does not run a scheduler itself. Configure a
platform scheduler (cron, Task Scheduler, or a managed scheduled job) to run
both commands every minute, using the same settings and database configuration
as the web processes:

```text
python manage.py expire_offers
python manage.py send_pending_notifications
```

Run each command as a separate scheduled process with non-overlapping
invocations where possible. A non-zero command exit must be alerted and retried
by the scheduler; the commands do not swallow database or unexpected provider
errors. `expire_offers` reports remaining overdue offers. Alert if that count
stays above zero across runs. The notification command reports due work left,
retries, and terminal failures. Operations can inspect the notification status,
attempt count, and safe error code on the dashboard; alert on any terminal
failure or sustained due backlog.

Notification delivery is outside the database transaction and claimed through
a short-lived lease. A process crash can cause a retry after the lease expires.
The worker passes a stable claim-based idempotency key for a provider that
supports idempotency, but the pilot gateway is simulated and does not provide
exactly-once delivery. Configure the real provider's request timeout below the
five-minute lease and confirm its idempotency-key semantics before production.
Retries are capped at eight attempts; terminal failures remain visible to
Operations and require manual investigation/redrive.

Matching does not reserve agent capacity for offers. Concurrent matching runs
may each send an offer for an agent's final available slot; capacity and
eligibility are rechecked under the agent lock at claim time, so only the
allowed number of claims can succeed. This avoids treating an unanswered offer
as a permanent capacity reservation.

For production, set `DJANGO_DEBUG=0`, a `DJANGO_SECRET_KEY` of at least 50
characters, a `WEBHOOK_TOKEN` of at least 32 characters, and explicit
`DJANGO_ALLOWED_HOSTS`. Configure `CORS_ALLOWED_ORIGINS` if the frontend is
hosted on another origin. The development identity/token endpoint and
permissive CORS behavior are disabled when debug mode is off.

If port 5433 is busy on your machine, override it:
`POSTGRES_PORT=5444 make setup` and set `DB_PORT=5444` when running backend
commands (see `.env.example`).

## Commands

| Command              | What it does                             |
| -------------------- | ---------------------------------------- |
| `make setup`         | One-time environment setup               |
| `make seed`          | Reset and reseed the database            |
| `make dev`           | Run backend + frontend                   |
| `make test`          | Backend + frontend test suites           |
| `make test-backend`  | pytest suite                             |
| `make test-frontend` | Vitest suite                             |
| `make lint`          | ruff, eslint, and tsc                    |
| `make db-up/db-down` | Start/stop PostgreSQL                    |

## Simulating the upstream platform

The upstream lead platform delivers referrals by webhook. Simulate it locally:

```bash
# One new referral
python tools/send_webhook.py

# The same delivery retried twice (as the upstream platform does on timeout)
python tools/send_webhook.py --repeat 2 --same-event-id

# Concurrent requests against any endpoint
python tools/generate_load.py --url http://127.0.0.1:8000/api/ops/referrals/ \
    --token tok-ops.riley --count 10
```

## Repository layout

```
backend/
  referral_project/   Django project settings and URLs
  referrals/          Domain app: models, matching, claims, webhook intake
  users/              Identity endpoints for the dev sign-in picker
frontend/
  src/pages/          Agent queue and Operations dashboard
  src/components/     Shared UI pieces
  src/api/            Fetch client and auth token handling
tools/                Webhook sender and load generator
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the data model and request flows,
and [OPERATIONS_REPORTS.md](OPERATIONS_REPORTS.md) for reports collected from
the pilot operations team.
