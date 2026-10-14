# Candidate Decision Note

## What I investigated

- Traced webhook intake, matching, offer claims, expiration, notification delivery, Operations overrides, and the dashboard query path.
- Confirmed the principal risks were duplicate referrals from distinct event IDs, capacity races, notification failures coupled to assignment, request-triggered expiration, and unauthorized customer-data access.

## What I prioritized, and why

- Enforced permanent uniqueness for upstream `external_id`, with a data migration that merges existing duplicate referral records before adding the constraint.
- Made webhook writes transactional and event-idempotent; validated payload fields and added a test for a new event that loses the unique-lead insertion race.
- Locked/refetched claims and agents, checked current eligibility and capacity, and revoked other pending offers after ownership is assigned.
- Added a transactional notification outbox with lease-based work claiming, bounded retries, safe error codes, and an idempotency key passed to the gateway.
- Added a repeatable expiration management command and serialized referral progression.
- Required explicit Operations override intent (`agent_id` integer or explicit `null`), checked eligibility and capacity, reconciled active-claim counters, and recorded the required audit note.
- Paginated and filtered the Operations dashboard, removed event history from the initial payload, and load timelines on demand.
- Restricted production defaults, hid development identity tokens when `DEBUG` is false, and ignored the local SQLite database.

## What I intentionally deferred

- True notification exactly-once delivery: it depends on the production provider honoring idempotency keys. The pilot gateway is simulated.
- Capacity reservation at offer-generation time: offers do not reserve an agent's slot; claim-time checks under the agent lock are authoritative.
- Deployment-specific job scheduling and alerting configuration. The README documents a one-minute schedule and backlog/terminal-failure signals, but the host must configure and monitor the scheduler.
- Full concurrency certification on PostgreSQL: PostgreSQL-specific tests are included but could not be executed in this environment.

## Assumptions

- An upstream `external_id` permanently identifies one referral. The migration keeps the earliest-created referral as canonical, combines related history, keeps at most one active claim, and deletes merged duplicates.
- Operations may assign only to currently eligible agents with capacity. Explicit `null` unassigns and requires an audit note; omission is invalid.
- An unanswered offer does not reserve capacity. Eligibility and capacity are rechecked at claim time.
- Notification delivery retries are bounded at eight attempts; a terminal failure remains visible and requires Operations review/redrive.

## How I validated my work

Validated locally with SQLite and the project virtual environment:

- `cd backend; python manage.py check` — passed.
- `cd backend; python manage.py makemigrations --check --dry-run` — no changes detected.
- `cd backend; python -m pytest referrals/tests -q` — 47 passed, 6 skipped.
- `cd backend; ruff check .` — passed.
- `cd frontend; npm test` — 9 tests passed.
- `cd frontend; npm run typecheck` — passed.
- `cd frontend; npm run lint` — passed.
- `cd frontend; npm run build` — passed.
- `git diff --check HEAD` — passed; `backend/db.sqlite3` is not tracked.

The skipped tests require PostgreSQL row locks. No PostgreSQL server was available at `127.0.0.1:5433`, and Docker/PostgreSQL command-line tools were unavailable, so the tests still need to be run using the repository's PostgreSQL setup.

## AI usage

AI assistance was used to inspect the application, draft and edit implementation changes, and add tests. It was not reliable without iterative validation: an intermediate notification-worker change tried to call `.iterator()` on a materialized list, and a workflow edit left a stale helper reference. The test suite caught both; they were corrected and the suite was rerun. The PostgreSQL-only concurrency tests have not been run, so their behavior is not represented as verified.

## Remaining launch risks

- Run the PostgreSQL-only claim, override, matching, and webhook concurrency tests against PostgreSQL before relying on row-lock guarantees.
- Configure the scheduled jobs, alerting, provider timeouts, and provider-side idempotency in the target environment.
- Review real provider failure modes and operational redrive policy before enabling customer notifications.
- Confirm production secrets, hostnames, TLS termination, CORS origins, and database backup/retention settings in deployment configuration.
