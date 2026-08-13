import argparse
import json
from pathlib import Path

from app.db.session import get_session_factory
from app.services.alias_importer import import_aliases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file")
    args = parser.parse_args()
    payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    with get_session_factory()() as session:
        report = import_aliases(payload, session)
        session.commit()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
