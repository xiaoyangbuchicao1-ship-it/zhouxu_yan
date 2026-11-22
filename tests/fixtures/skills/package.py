"""Package a fixture directory as a ZIP without importing or executing its files."""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path


def make_zip(source: Path) -> bytes:
    if not source.is_dir():
        raise SystemExit(f"fixture directory not found: {source}")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.writestr(path.relative_to(source).as_posix(), path.read_bytes())
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = make_zip(args.source)
    if args.output:
        args.output.write_bytes(payload)
    else:
        sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
