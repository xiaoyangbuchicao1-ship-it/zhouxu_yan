"""Explicit, offline transfer of runtime SQLite stores to/from Linux storage.

Reference: https://sqlite.org/wal.html (all readers must share one host).
Run only with the API stopped. Never replace an existing destination.
"""

import argparse
import hashlib
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from backend.infrastructure.persistence.run_store import run_store_owner

DATABASES = ("runs.sqlite3", "maritime-office.sqlite3")
RUNTIME = Path("/app/backend/.runtime")
LEGACY = Path("/app/legacy-runtime")


def check_ready(target: Path = RUNTIME, legacy: Path = LEGACY) -> None:
    for name in DATABASES:
        if (legacy / name).is_file() and not (target / name).is_file():
            raise RuntimeError(
                "Existing runtime data requires explicit migration; stop the API, then run: "
                "docker compose -f deploy/app/compose.yaml run --rm --no-deps "
                "--entrypoint python zhouxu-api /app/start/common/runtime_volume.py migrate"
            )


def digest(db: sqlite3.Connection) -> str:
    checksum = hashlib.sha256()
    for line in db.iterdump():
        checksum.update(line.encode("utf-8"))
        checksum.update(b"\n")
    return checksum.hexdigest()


def transfer(source: Path, target: Path) -> None:
    if source.resolve() == target.resolve():
        raise ValueError("Source and destination must differ")
    names = [name for name in DATABASES if (source / name).is_file()]
    if not names:
        raise FileNotFoundError(f"No runtime databases in {source}")
    target.mkdir(parents=True, exist_ok=True)
    if any(path.name != "runs.sqlite3.lock" for path in target.iterdir()):
        raise FileExistsError(f"Destination must be empty: {target}")
    # Hold the scheduler's own process lock so an active service cannot be
    # exported. Import from a legacy mount additionally requires API stop.
    lock_root = source if source == RUNTIME else target
    with (
        run_store_owner(lock_root / "runs.sqlite3"),
        tempfile.TemporaryDirectory(prefix="runtime-transfer-") as staging,
    ):
        stage = Path(staging)
        # Copy quiescent source DB + journal as one set. Opening only the
        # private Linux copy cannot disturb the Windows source's WAL/SHM.
        for name in names:
            for suffix in ("", "-wal", "-journal"):
                path = source / (name + suffix)
                if path.is_file():
                    shutil.copy2(path, stage / path.name)
        for name in names:
            with closing(sqlite3.connect(stage / name)) as db:
                if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError(f"Source integrity check failed: {name}")
                before = digest(db)
                with closing(sqlite3.connect(stage / (name + ".verified"))) as out:
                    db.backup(out)
                    out.execute("PRAGMA journal_mode=DELETE")
                    if out.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise RuntimeError(f"Backup integrity check failed: {name}")
                    if digest(out) != before:
                        raise RuntimeError(f"Backup content mismatch: {name}")
                print(f"Verified {name}: logical SHA256 {before}")
        # Only publish after every database has passed both checks.
        for name in names:
            shutil.copy2(stage / (name + ".verified"), target / name)
    print(f"Transferred {len(names)} databases to {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "migrate", "export"))
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.operation == "check":
        check_ready()
    elif args.operation == "migrate":
        transfer(LEGACY, RUNTIME)
    else:
        if args.destination is None:
            parser.error("export requires --destination")
        transfer(RUNTIME, args.destination)


if __name__ == "__main__":
    main()
