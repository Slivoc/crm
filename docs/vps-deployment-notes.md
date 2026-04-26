# VPS Deployment Notes

## Purpose

Working notes for moving the CRM from the current local Windows environment to a public Linux VPS.

This document is intended to be updated during the deployment.

## Current Decisions

- OS: `Ubuntu 24.04 LTS x64`
- Connectivity: `Public IPv4`
- Plan: `Shared CPU`
- vCPU: `4`
- RAM: `8 GB`
- Storage: `160 GB`
- Backups: `Enabled`
- Region: `Manchester, GB`
- Git repository: `https://github.com/Slivoc/crm`
- Live hostname: `mgc.sproutt.io`
- Public app URL: `https://mgc.sproutt.io`

## Recommended Settings

- Enable `Limited User Login`
- Leave `DDoS Protection` off unless there is a specific need
- Leave `Cloud-Init User Data` off unless a bootstrap script is prepared
- VPC is optional for a single-server setup

## Suggested Identity

- Hostname: `crm-prod`
- Label: `crm-prod`

## Deployment Order

1. Provision VPS
2. Install PostgreSQL
3. Create database and database user
4. Restore database dump from local machine
5. Apply small schema-alignment patch if needed
6. Clone app from git
7. Create Python virtual environment
8. Install Python requirements
9. Add environment variables / `.env`
10. Configure process manager and reverse proxy
11. Run smoke tests
12. Open public access last

## App State Relevant To VPS Move

- The app is now much closer to Linux-ready.
- Windows-only mail sending via `pywin32` / Outlook COM has been removed.
- The old `pdfkit` / `wkhtmltopdf` sales-order acknowledgment flow has been removed.
- Several unused Windows-leaning and local-ML dependencies have been removed from `requirements.txt`.
- Authentication has been hardened so private routes now fail closed before login.
- Users can change their own password inside the app without email reset flow.

## Known Database Notes

- There is no migration tracking table in the current database.
- The local PostgreSQL database should be treated as the source of truth for first VPS cutover.
- Current recommendation is:
  - dump local DB
  - restore to VPS
  - then apply additive schema fixes if needed

### Known Schema Drift Identified

- Missing table: `graph_mailbox_folders_cache`
- Missing table: `qpl_manufacturer_mappings`
- Missing column: `parts_list_supplier_quote_lines.revision`
- Missing column: `customer_quote_lines.target_price_gbp`

## Suggested First-Cut Runtime Shape

- App server: Flask app on Linux
- Database: PostgreSQL on same VPS initially
- Reverse proxy: `nginx`
- Python app runner: `waitress`
- Service management: likely `systemd`

## To Fill In Next

- VPS provider name: `Vultr`
- Public IP address: `64.176.186.181`
- SSH user: `linuxuser`
- SSH key path / key name
- PostgreSQL version: `18`
- Database name: `sproutt`
- Database username: `tom`
- App directory on server: `/srv/sproutt`
- Virtualenv path: `/srv/sproutt/.venv`
- Systemd service name: `sproutt`
- Nginx site config path: `/etc/nginx/sites-available/sproutt`
- Domain / subdomain: `mgc.sproutt.io`
- SSL method: `certbot` / Let's Encrypt
- Backup/restore command history

## Command Log

Add important setup commands here as deployment progresses.

### Completed So Far

- Connected to VPS via SSH as `linuxuser`
- Installed PostgreSQL on Ubuntu
- Added PostgreSQL 18 tooling to match the local dump format
- Created and restored the `sproutt` database on the VPS
- Verified that application data is present in the restored database

### Current Database State

- Database name: `sproutt`
- Database user: `tom`
- PostgreSQL major version used for restore: `18`

### Current Live Service State

- `waitress` runs the app via `systemd`
- `nginx` proxies public traffic to `127.0.0.1:5000`
- Public HTTPS endpoint is working:
  - `https://mgc.sproutt.io`
- Expected unauthenticated response:
  - redirect to `/auth/login`

### Restore Notes

- The restore succeeded with two non-blocking default-privilege warnings:
  - `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO tom`
  - `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO tom`
- These warnings are expected when restoring as `tom` instead of the original `postgres` role.
- Recommended restore flag for future clean restores:
  - `--no-acl`

### Next Deployment Steps

1. Verify login and key pages in browser
2. Apply any additive schema fixes still needed
3. Tighten production config / reduce debug logging if desired
4. Record final backup and recovery process
5. Optionally add deployment/update script

## Operating Instructions

### SSH In

```bash
ssh linuxuser@64.176.186.181
```

### App Directory

```bash
cd /srv/sproutt
```

### Do I Need To Activate The Virtualenv?

- `Yes` if you are running Python or `pip` commands manually.
- `No` if you are managing the app through `systemd`, because the service already points at the virtualenv Python.

Activate manually:

```bash
cd /srv/sproutt
source .venv/bin/activate
```

Deactivate:

```bash
deactivate
```

### Restart The App

Use `systemd`, not a manual Python command:

```bash
sudo systemctl restart sproutt
```

Check status:

```bash
sudo systemctl status sproutt
```

Live logs:

```bash
sudo journalctl -u sproutt -f
```

### Start / Stop The App

```bash
sudo systemctl start sproutt
sudo systemctl stop sproutt
```

### Update Code From Git

```bash
cd /srv/sproutt
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart sproutt
```

### Nginx

Test config:

```bash
sudo nginx -t
```

Reload after config change:

```bash
sudo systemctl reload nginx
```

Restart if needed:

```bash
sudo systemctl restart nginx
```

### Firewall

Expected open ports:

- `22/tcp`
- `80/tcp`
- `443/tcp`

Check:

```bash
sudo ufw status
```

Note:
- `5000` should not be publicly open after nginx is in front.

### Database Access

Connect to the app database:

```bash
psql -h localhost -U tom -d sproutt
```

Exit `psql`:

```sql
\q
```

Change DB password:

```bash
sudo -u postgres psql
```

Then:

```sql
ALTER USER tom WITH PASSWORD 'new-password';
\q
```

### Environment File

App env file:

```bash
/srv/sproutt/.env
```

After changing `.env`, restart the app:

```bash
sudo systemctl restart sproutt
```

### Waitress Service

Current service model:

- service name: `sproutt`
- runner: `python -m waitress --host=127.0.0.1 --port=5000 app:app`

The service already uses the venv Python. Manual activation is not required for normal operation.

### Public URL Tests

Expected HTTP behavior:

```bash
curl -I http://mgc.sproutt.io
```

- should redirect to HTTPS

Expected HTTPS behavior:

```bash
curl -I https://mgc.sproutt.io
```

- should return a `302` redirect to `/auth/login` when not authenticated
