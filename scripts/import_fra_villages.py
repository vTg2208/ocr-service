"""Import a synthetic Tamil Nadu FRA village GeoJSON pack."""

import argparse
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from app.db.session import get_session_factory
from app.services.fra_atlas import import_village_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--actor-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    payload = json.loads(args.file.read_text(encoding="utf-8"))
    with get_session_factory()() as session:
        report = import_village_profiles(session, payload, actor_id=args.actor_id)
        session.commit()
    print(json.dumps(asdict(report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
