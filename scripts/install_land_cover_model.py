"""Explicitly download and checksum-verify the pinned Apache-2.0 baseline."""

import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen

from app.config import get_settings
from app.services.land_cover_raster import SOURCE_REVISION, WEIGHTS_SHA256


def install(destination):
    path = Path(destination)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != WEIGHTS_SHA256:
            raise ValueError("Existing weights have a different checksum; choose a new path or inspect the file.")
        return path
    url = f"https://raw.githubusercontent.com/calebrob6/dynamic_world_pytorch/{SOURCE_REVISION}/weights/dynamic_world.pt"
    with urlopen(url, timeout=60) as response:
        content = response.read(2_000_001)
    if len(content) > 2_000_000 or hashlib.sha256(content).hexdigest() != WEIGHTS_SHA256:
        raise ValueError("Model download checksum mismatch; no weights were installed.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    temporary.write_bytes(content)
    temporary.replace(path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default=get_settings().land_cover_weights_path)
    args = parser.parse_args()
    path = install(args.destination)
    print(f"Installed verified Dynamic World baseline: {path}")
    print(f"SHA-256: {WEIGHTS_SHA256}")
    print("Apache-2.0; attribution in app/services/vendor/NOTICE.dynamic-world.")


if __name__ == "__main__":
    main()
