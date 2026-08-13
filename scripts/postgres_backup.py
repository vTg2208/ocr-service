"""Create a PostgreSQL custom-format backup without placing credentials on the command line."""

import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description="Back up the configured PostgreSQL/PostGIS database.")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL.")
    libpq_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["pg_dump", "--format=custom", "--no-owner", "--file", str(args.output), libpq_url],
        check=True,
    )


if __name__ == "__main__":
    main()
