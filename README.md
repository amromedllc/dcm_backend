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

### Running Celery Beat (Periodic Tasks)
```bash
celery -A config beat --loglevel=info
```

---

## 🤝 TherapyPMS Integration

- **APIs and Login**: Real-time staff/admin logins are routed through legacy table lookups using bcrypt.


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
