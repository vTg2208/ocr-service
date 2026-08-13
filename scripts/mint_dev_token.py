import argparse
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings


def main():
    parser = argparse.ArgumentParser(description="Mint a short-lived local development JWT.")
    parser.add_argument("external_id")
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    settings = get_settings()
    now = datetime.now(timezone.utc)
    print(jwt.encode(
        {
            "sub": args.external_id, "iat": now, "exp": now + timedelta(minutes=args.minutes),
            "iss": settings.auth_issuer, "aud": settings.auth_audience,
        },
        settings.auth_secret, algorithm="HS256",
    ))


if __name__ == "__main__":
    main()
