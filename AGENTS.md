# Unified Project Context — Sproutt CRM + Customer Portal

> **Hard-coded directories for this workspace**
> - **Core CRM:** `c:\crm`
> - **Customer Portal:** `c:\customer-portal`

These two repositories are siblings. The CRM owns the database and all business logic; the Portal is a thin, stateless Flask frontend that proxies every request through the CRM's `/api/portal/*` endpoints.

---

## 1. Repo Overview

### 1.1 Core CRM (`c:\crm`)

| Property | Value |
|----------|-------|
| **Directory** | `c:\crm` |
| **Repo** | `https://github.com/Slivoc/crm.git` |
| **Framework** | Flask (Python) |
| **Database** | PostgreSQL (local to VPS) |
| **Live host** | `https://mgc.sproutt.io` |
| **VPS** | Vultr Ubuntu 24.04 — `64.176.186.181` |
| **App dir on server** | `/srv/sproutt` |
| **Venv on server** | `/srv/sproutt/.venv` |
| **Systemd service** | `sproutt` |
| **Reverse proxy** | nginx (`/etc/nginx/sites-available/sproutt`) |
| **Python runner** | `waitress` on `127.0.0.1:5000` |

**Key entry points**
- `app.py` — Flask app factory, scheduler, blueprint registration
- `db.py` — PostgreSQL connection pool (`psycopg2`)
- `models/` — Split model files (`part_1.py` … `part_5.py`)
- `routes/` — ~60 route modules; the portal-facing ones are:
  - `routes/portal_api.py` — customer-facing API (`/api/portal/*`)
  - `routes/portal_admin.py` — internal sales portal admin (`/portal-admin/*`)
- `migrations/` — One-off `.sql` migration files (no Alembic)

**Environment variables (`.env`)**
- `DATABASE_URL` — required (`postgresql://...`)
- `SECRET_KEY`, `JWT_SECRET` — must match Portal's `JWT_SECRET`
- `API_KEY` — shared secret; Portal must send this in `X-API-Key` header
- `OPENAI_API_KEY`, `HUBSPOT_API_KEY`, `APOLLO_API_KEY`, `EXCHANGE_RATE_API_KEY`
- `TICKETS_HUB_URL`, `TICKETS_HUB_API_KEY`

### 1.2 Customer Portal (`c:\customer-portal`)

| Property | Value |
|----------|-------|
| **Directory** | `c:\customer-portal` |
| **Repo** | `https://github.com/Slivoc/customer-portal.git` |
| **Framework** | Flask (Python) — **no database** |
| **Live host** | `https://portal.sproutt.io` |
| **VPS** | Vultr Ubuntu 24.04 — `45.32.211.139` |
| **App dir on server** | `/var/www/portal` |
| **Venv on server** | `/var/www/portal/venv` |
| **Systemd service** | `portal` |
| **Reverse proxy** | nginx (`/etc/nginx/sites-available/portal`) |
| **Python runner** | `gunicorn` on `127.0.0.1:5001` |

**Key entry points**
- `app.py` — all routes + `call_crm_api()` helper
- `config.py` — `CRM_API_URL`, `JWT_SECRET`, `API_KEY`
- `wsgi.py` — WSGI entry for gunicorn
- `templates/` — Jinja2 HTML (login, dashboard, quote, POs, etc.)
- `static/` — CSS/JS/images

**How it talks to the CRM**
```python
# In c:\customer-portal\app.py
def call_crm_api(endpoint, data=None, method='POST', params=None):
    url = f"{app.config['CRM_API_URL']}/{endpoint}"
    headers = {
        'X-API-Key': app.config['API_KEY'],
        'Content-Type': 'application/json'
    }
    if 'token' in session:
        headers['Authorization'] = f"Bearer {session['token']}"
    ...
```

**Critical shared secrets**
- `JWT_SECRET` must be identical in both apps or login sessions break.
- `API_KEY` must match the CRM's `API_KEY` or all portal API calls are rejected.

---

## 2. Architecture

```
Customer Browser
       |
       v
 portal.sproutt.io  (nginx → gunicorn → Flask @ 127.0.0.1:5001)
       |
       |  HTTPS  |  Headers: X-API-Key + Authorization: Bearer <JWT>
       v
 mgc.sproutt.io   (nginx → waitress → Flask @ 127.0.0.1:5000)
       |
       v
   PostgreSQL
```

- **Portal has zero DB tables.** Every read/write goes through `call_crm_api()`.
- **CRM owns auth.** Portal login POSTs credentials to `/api/portal/auth/login`; CRM returns a JWT that the Portal stores in Flask `session`.
- **CRM routes for Portal are namespaced:**
  - `/api/portal/*` — public customer API (`portal_api.py`)
  - `/portal-admin/*` — internal sales UI (`portal_admin.py`)

---

## 3. Portal ↔ CRM Endpoint Map

| Portal Page / Action | Portal Route | CRM Endpoint (`portal_api.py`) |
|----------------------|--------------|-------------------------------|
| Login | `POST /login` | `POST /api/portal/auth/login` |
| Request access | `POST /request-access` | `POST /api/portal/auth/request-access` |
| Dashboard | `GET /dashboard` | `GET /api/portal/quote/requests`, `GET /api/portal/common-parts`, `GET /api/portal/pricing-agreements`, `GET /api/portal/suggested-parts`, `GET /api/portal/search/recent` |
| Quote search | `GET/POST /quote` | `POST /api/portal/quote/analyze` |
| Submit quote | `POST /quote/submit` | `POST /api/portal/quote/submit` |
| Save BOM | `POST /quote/save-bom` | `POST /api/portal/search/save-bom` |
| My quotes | `GET /my-quotes` | `GET /api/portal/quote/requests` |
| View quote | `GET /my-quotes/<id>` | `GET /api/portal/quote/requests/<id>` |
| Review PO | `POST /po/review` | (local Portal page only) |
| Submit PO | `POST /po/submit` | `POST /api/portal/po/submit` |
| My orders | `GET /my-orders` | `GET /api/portal/po/list` |
| View PO | `GET /my-orders/<id>` | `GET /api/portal/po/<id>` |
| Request pricing agreement | `POST /agreement/request` | `POST /api/portal/agreements/request` |

Portal admin (internal sales) lives in CRM templates/routes under `/portal-admin/…`.

---

## 4. Shared Data Concepts

### 4.1 Portal Quote Request Lifecycle
1. **Intake** — customer submits parts via Portal → CRM creates:
   - `portal_quote_requests` (top-level)
   - `portal_quote_request_lines` (customer-facing lines)
   - `parts_list_lines` (operational sourcing lines)
2. **Sales works it** — in CRM `/portal-admin/requests/<id>` (`portal_admin.py`)
3. **Customer views it** — Portal calls `GET /api/portal/quote/requests/<id>`

### 4.2 Quote Overrides
Sales can override what the customer sees without losing the original request.
See `c:\crm\PORTAL_QUOTE_OVERRIDES.md` for full precedence rules.

Key precedence (highest wins):
- `portal_quote_request_lines.quoted_part_number`
- `customer_quote_lines.quoted_part_number`
- `customer_quote_lines.display_part_number`
- original `portal_quote_request_lines.part_number`

### 4.3 Recent Searches / Snapshot Model
- Table: `portal_search_history_lines` (migration `20260501_add_portal_search_history_lines.sql`)
- CRM `analyze_quote` stores per-line snapshots for `manual_quote_search`.
- Portal dashboard calls `GET /api/portal/search/recent?limit=50` and groups rows by `search_history_id` client-side.
- See `c:\crm\docs\portal_recent_searches_backend_handoff.md`

---

## 5. Deployment Notes

### 5.1 CRM (`c:\crm`)
Follow `c:\crm\docs\vps-deployment-notes.md`.

Quick commands (VPS):
```bash
# Restart app
sudo systemctl restart sproutt
sudo systemctl status sproutt
sudo journalctl -u sproutt -f

# Update code
cd /srv/sproutt
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart sproutt

# Nginx
sudo nginx -t
sudo systemctl reload nginx
```

### 5.2 Portal (`c:\customer-portal`)
Follow `c:\customer-portal\DEPLOY_PROD.md` and `c:\customer-portal\server_setup.md`.

Quick commands (VPS):
```bash
# Restart app
sudo systemctl restart portal
sudo systemctl status portal
sudo journalctl -u portal -n 100 --no-pager

# Update code
cd /var/www/portal
git fetch origin
git checkout main
git pull origin main
./venv/bin/pip install -r requirements.txt
sudo systemctl restart portal

# Nginx
sudo nginx -t
sudo systemctl reload nginx
```

### 5.3 Common Failure Points
- `JWT_SECRET` mismatch between Portal and CRM → auth loops / 401s.
- `CRM_API_URL` in Portal points to `localhost` instead of the real CRM host.
- `API_KEY` mismatch → every Portal API call rejected.
- nginx default site still enabled on either VPS → 404s.

---

## 6. Performance & Postgres Context

The CRM recently migrated from SQLite to PostgreSQL. Several hotspots were fixed:

| Issue | Fix Location |
|-------|-------------|
| Costing page: one DB request per line | `routes/parts_list.py` — added bulk `/parts-lists/<id>/lines/quote-availability` |
| Costing page: `unit_price` string crash | `static/js/parts_list_costing.js` — safe number parsing before `.toFixed()` |
| Static assets stalled by DB hits | `app.py` — skip DB work in `load_user`/`before_request` for static requests |
| Customer search N+1 associations | `routes/customers.py` — added `/customers/associations/bulk` |
| Contacts page slow subqueries | `models/part_3.py` — CTEs for comm counts |
| Planner heavy cross-customer query | `routes/salespeople.py` — limit first-order lookup to relevant IDs |

**Indexes added**
- `migrations/20251220_add_parts_list_quote_indexes.sql`

---

## 7. Important Files to Know

### CRM (`c:\crm`)
- `app.py` — bootstrap, blueprints, scheduler, DB-skip logic for statics
- `db.py` — PostgreSQL pool, `_database_url()`, `execute()` helper
- `routes/portal_api.py` — all Portal-facing API endpoints
- `routes/portal_admin.py` — internal sales portal admin UI
- `routes/customer_quoting.py` — customer quote module (feeds portal overrides)
- `routes/parts_list.py` — parts lists, supplier quotes, costing
- `models/part_3.py` — contacts, CTE optimizations
- `models/part_4.py` — overdue counts, planner data
- `PORTAL_QUOTE_OVERRIDES.md` — override precedence rules
- `docs/vps-deployment-notes.md` — CRM deployment guide
- `docs/portal_recent_searches_backend_handoff.md` — recent searches backend

### Portal (`c:\customer-portal`)
- `app.py` — all routes + `call_crm_api()`
- `config.py` — `CRM_API_URL`, `JWT_SECRET`, `API_KEY`
- `server_setup.md` — infrastructure architecture (nginx/gunicorn)
- `DEPLOY_PROD.md` — step-by-step production deployment

---

## 8. Notes for Future Agents

1. **If you change auth/session logic in one repo, you MUST update the other.**
   - JWT secret rotation requires simultaneous `.env` updates on both VPSs.
2. **If you add a new Portal page that needs data, you usually need:**
   - A new route in `c:\customer-portal\app.py`
   - A matching endpoint in `c:\crm\routes\portal_api.py`
   - Optionally a template in `c:\customer-portal\templates/`
3. **If the Portal feels slow:**
   - Check CRM `portal_api.py` endpoints for N+1 queries.
   - The Portal itself is stateless; slowness is almost always CRM DB/API latency.
4. **Schema changes:**
   - Add migration files to `c:\crm\migrations/YYYYmmDD_description.sql`.
   - There is no migration runner; apply them manually or via a script.
5. **When in doubt about portal behavior:**
   - Read `call_crm_api()` in `c:\customer-portal\app.py` to see what endpoint is being hit.
