"""Restore a tested custom-format backup with an explicit destructive confirmation."""

import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description="Restore the PostgreSQL/PostGIS database.")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--confirm-destructive-restore", action="store_true")
    args = parser.parse_args()
    if not args.confirm_destructive_restore:
        raise SystemExit("Restore can overwrite database objects; pass --confirm-destructive-restore.")
    if not args.backup.is_file():
        raise SystemExit("Backup file does not exist.")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL.")
    libpq_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--no-owner", "--dbname", libpq_url, str(args.backup)],
        check=True,
    )


if __name__ == "__main__":
    main()
