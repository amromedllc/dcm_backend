# DCM Backend API Platform

This is the backend API service for **DCM (Data Collection Platform)**, built using **Django 5**, **django-ninja** (for high-performance type-safe REST APIs), and **PostgreSQL**.

DCM functions as a clinical companion product to **TherapyPMS**, adding ABA-specific clinical data collection capabilities (programs, targets, session recording, notes, and analytics) on top of scheduling and billing data.

---

## 🛠️ Tech Stack & Core Libraries

- **Framework**: Django 5.x
- **API Engine**: django-ninja (OpenAPI/Swagger documentation generated at `/api/v1/docs`)
- **Database**: PostgreSQL (multi-tenant architecture via schemas + row-level filters)
- **Task Queue**: Celery + Redis (for PDF export generation and sync workers)
- **Authentication**: Custom stateless JWT authentication + API Key validation
- **Testing**: pytest + pytest-django

---

## 🚀 Local Development Setup

### 1. Prerequisites
Ensure you have the following installed locally:
- **Python 3.10+** (Python 3.11 recommended)
- **PostgreSQL**
- **Redis** (running on port `6379`)

### 2. Environment Setup
Create a Python virtual environment and activate it:
```bash
# Create the virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate
```

### 3. Install Dependencies
Install the required packages for local development:
```bash
pip install -r requirements/base.txt -r requirements/local.txt
```

### 4. Configuration (`.env`)
Copy the template configuration file and customize the variables:
```bash
cp .env.example .env
```
Open `.env` and fill in your PostgreSQL credentials (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`), your Redis URL, and generate secrets.

### 5. Database Setup (Multi-Tenancy)
DCM uses a **hybrid multi-tenant architecture**:
1. **Schema-based isolation (`django-tenants`)**: Separate Postgres schemas per organization.
2. **Row-level isolation (`shared.tenancy`)**: Automatic tenant scoping using an `organization_id` ContextVar.

To run migrations across all schemas:
```bash
# Migrate shared/public tables (e.g. Tenants, Domains, Legacy apps)
python manage.py migrate_schemas --shared

# Migrate tenant-scoped tables (e.g. Clients, Programs, Sessions, Notes)
python manage.py migrate_schemas --tenant
```

To create a tenant organization locally, use the django-tenants management commands or Django shell:
```bash
python manage.py create_tenant
```
Ensure you bind a `Domain` to the organization mapping to your local hostname (e.g., `localhost` or `practice1.localhost`).

### 6. Run Server
Start the local development server:
```bash
python manage.py runserver
```
The API documentation will be available at `http://127.0.0.1:8000/api/v1/docs`.

---

## ⏱️ Celery Task Queue

Background tasks (like generating notes PDF exports and behavior analytics graphs) are handled by Celery.

### Running Celery Worker
```bash
celery -A config worker --loglevel=info
```

The backend and worker must use the same Redis instance and database. When
running the backend on the host with the Docker Compose worker, the worker
defaults to `redis://host.docker.internal:6379/0`. If the backend uses another
Redis endpoint, set `DCM_WORKER_REDIS_URL` to its Docker-accessible equivalent
before starting Compose. After a broker change, recreate the worker with
`docker compose up -d --no-deps worker` from the repository root.

### Running Celery Beat (Periodic Tasks)
```bash
celery -A config beat --loglevel=info
```

---

## 🤝 TherapyPMS Integration

- **APIs and Login**: Real-time staff/admin logins are routed through legacy table lookups using bcrypt.

Admin integrations are scoped to a mapped facility within the current organization.
Use `facility_admin_id=<TherapyPMS practice admin ID>` on the integration settings,
connect, disconnect, pull, and pull-job list endpoints. Settings without a selection
return the facility list; mutations require a selection when several facilities are
mapped. A single facility remains the default for older callers.

Credentials are encrypted on `OrganizationTpmsAdminId`, and cached tokens include
both organization and facility IDs. Each queued pull records `facility_admin_id`
in its params, which the worker resolves within the job's organization. The worker
rejects older jobs without a facility selection. Migration `0012` moves an existing
organization connection to its matching facility. Apply it with
`python manage.py migrate_schemas --shared`, then restart the Celery worker.

To verify facility login and token refresh over real HTTP without contacting
TherapyPMS, start the local fixture from this directory:

```bash
postman simulation run postman/simulations/therapypms-facilities.sim.yaml --port 4911
DCM_TPMS_SIMULATION_URL=http://127.0.0.1:4911/therapypms python manage.py test apps.tenants.tests.test_facility_integrations
```

The fixture follows the repository's admin-login payload contract and uses fake
credentials for two facilities. It is a local stand-in; live TherapyPMS verification
still requires the facility's real credentials.


## 🧪 Running Tests

The test suite runs on `pytest`. It sets up organization fixtures (`org_a`, `org_b`) and checks multi-tenant isolation boundaries.

To execute tests:
```bash
pytest
```

To run tests with detailed verbosity:
```bash
pytest -v
```

---

## 📁 App Directory Layout

- `api/v1/` — High-level routing and exceptions handlers.
- `apps/accounts/` — User authentication, JWT issuance, roles, and TPMS credential verification.
- `apps/clients/` — Core clinical learner/client profiles.
- `apps/programs/` — Clinical programming (Programs, Modules, Targets, Prompting & Fading Templates).
- `apps/sessions/` — Real-time trial-by-trial recording workflows, behavior logs, and ABC collection.
- `apps/notes/` — Document editor, supervisor approval workflows, and PDF exports.
- `apps/exports/` — Task runner bindings for CSV, Zip, and PDF exports.
- `shared/` — Multitenancy middlewares (`TenantResolverMiddleware`), mixins, and storage utils.


## Session service setting

`POST /sessions` and `POST /group-sessions` accept `service_setting`: `Home`,
`Office`, `School`, `Community`, `Other`, or `Telehealth`. Session responses
return the saved value. Manual sessions use the same field. Each new trial
inherits its session's setting, including batch-synced trials, and trial
responses return it. Program Category is independent of this field.

Apply migration `dcm_sessions.0019_session_service_setting` before deploying.
Existing sessions and trials retain an empty setting; older API clients may
omit the field. The web creation dialogs require an explicit selection.

Before submitting a Home, Office, School, or Community session, send
`service_address` with `street`, optional `unit`, `city`, two-letter US `state`,
`zip_code` (five digits or ZIP+4), and `country: "US"`. Both
`POST /sessions/{id}/submit` and batch sync with `submit_after_sync: true`
enforce this requirement. The address is saved on the session and returned
as `service_address`. Telehealth is online, and Other needs no address.
Legacy sessions without a setting can still be submitted without an address.
Apply migration `dcm_sessions.0020_sessionrun_service_address` before deploying.

## Web offline recording event IDs

`POST /sessions/{id}/trials` now accepts an optional nonempty
`client_event_id` (up to 64 characters), returned in trial responses and lists.
For the same session, retries with the same ID and immutable payload return the
original trial. Reusing that ID for different data or colliding with an existing
trial number returns 409, without overwriting the recorded entry. Existing
recording permissions, completed-session edit reasons, and manual time-window
rules still apply. Behavior and ABC endpoints already support this ID.

Migration `dcm_sessions.0018_trialevent_client_event_id_and_more` adds the nullable
field and conditional uniqueness constraint. Older clients can omit the field;
the mobile batch-sync endpoint keeps its existing natural-key deduplication for
legacy trials and supports event IDs when present.

The web client durably queues open-session creates/deletes, retries the existing
per-event endpoints with the original IDs, and requires confirmation before
submitting. It does not depend on closed-browser background sync. See the web
README for local simulation and recovery tests.
