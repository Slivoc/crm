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
- Reverse proxy: likely `nginx`
- Python app runner: likely `gunicorn`
- Service management: likely `systemd`

## To Fill In Next

- VPS provider name: `Vultr`
- Public IP address: `64.176.186.181`
- SSH user: `linuxuser`
- SSH key path / key name
- PostgreSQL version
- Database name
- Database username
- App directory on server
- Virtualenv path
- Systemd service name
- Nginx site config path
- Domain / subdomain
- SSL method
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

### Restore Notes

- The restore succeeded with two non-blocking default-privilege warnings:
  - `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO tom`
  - `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO tom`
- These warnings are expected when restoring as `tom` instead of the original `postgres` role.
- Recommended restore flag for future clean restores:
  - `--no-acl`

### Next Deployment Steps

1. Install git / Python build tooling on the VPS
2. Clone `https://github.com/Slivoc/crm`
3. Create virtualenv
4. Install Python requirements
5. Create VPS `.env`
6. Run app locally on server for smoke test
7. Configure `gunicorn`
8. Configure `nginx`
9. Apply any additive schema fixes still needed
10. Open public access last
