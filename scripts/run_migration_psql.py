#!/usr/bin/env python3
"""
Run a checked-in SQL migration through psql using DATABASE_URL from .env.

This is intended for the production VPS layout documented in docs/vps-deployment-notes.md,
where the Linux SSH user and PostgreSQL user are different.

Examples:
    python scripts/run_migration_psql.py
    python scripts/run_migration_psql.py --latest
    python scripts/run_migration_psql.py 20260504_add_parts_list_line_original_part_fields.sql
    python scripts/run_migration_psql.py --env-file /srv/sproutt/.env --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def migrations_dir() -> Path:
    return repo_root() / "migrations"


def default_env_file() -> Path:
    return repo_root() / ".env"


def parse_dotenv(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def resolve_database_url(env_path: Path | None) -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    if env_path is None:
        env_path = default_env_file()

    env_values = parse_dotenv(env_path)
    database_url = env_values.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            f"DATABASE_URL not found in environment or env file: {env_path}"
        )
    return database_url


def find_latest_migration() -> Path:
    files = sorted(
        path for path in migrations_dir().glob("*.sql")
        if path.is_file()
    )
    if not files:
        raise RuntimeError("No SQL migration files found in migrations/")
    return files[-1]


def resolve_migration(migration_arg: str | None) -> Path:
    if not migration_arg:
        return find_latest_migration()

    candidate = Path(migration_arg)
    if candidate.is_absolute():
        if not candidate.exists():
            raise RuntimeError(f"Migration file not found: {candidate}")
        return candidate

    by_name = migrations_dir() / migration_arg
    if by_name.exists():
        return by_name

    if candidate.exists():
        return candidate.resolve()

    raise RuntimeError(f"Migration file not found: {migration_arg}")


def build_psql_command(database_url: str, migration_path: Path) -> tuple[list[str], dict[str, str]]:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")

    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    hostname = parsed.hostname or "localhost"
    port = str(parsed.port or 5432)
    database_name = (parsed.path or "").lstrip("/")

    if not username or not database_name:
        raise RuntimeError("DATABASE_URL must include both username and database name")

    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password

    command = [
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        hostname,
        "-p",
        port,
        "-U",
        username,
        "-d",
        database_name,
        "-f",
        str(migration_path),
    ]
    return command, env


def mask_command_for_display(command: list[str], database_url: str) -> str:
    parsed = urlparse(database_url)
    username = unquote(parsed.username or "")
    hostname = parsed.hostname or "localhost"
    port = str(parsed.port or 5432)
    database_name = (parsed.path or "").lstrip("/")
    migration_path = command[-1]
    return (
        f"psql -v ON_ERROR_STOP=1 -h {hostname} -p {port} "
        f"-U {username} -d {database_name} -f {migration_path}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a repo SQL migration through psql using DATABASE_URL."
    )
    parser.add_argument(
        "migration",
        nargs="?",
        help="Migration filename or path. Defaults to the latest file in migrations/.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Explicitly run the latest migration file in migrations/.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env file. Defaults to <repo>/.env if DATABASE_URL is not already set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved migration and psql command without running it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        migration_arg = None if args.latest else args.migration
        migration_path = resolve_migration(migration_arg)
        if args.latest and args.migration:
            raise RuntimeError("Use either a migration filename or --latest, not both.")

        database_url = resolve_database_url(args.env_file)
        command, env = build_psql_command(database_url, migration_path)

        print(f"Migration: {migration_path}")
        print(f"Command:   {mask_command_for_display(command, database_url)}")

        if args.dry_run:
            return 0

        completed = subprocess.run(command, env=env, check=False)
        return completed.returncode
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
