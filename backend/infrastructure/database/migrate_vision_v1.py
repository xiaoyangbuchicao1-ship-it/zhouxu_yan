"""Explicit, idempotent migration enabling multimodal vision on inferred vision-capable models.

Upgrades existing active model profiles whose ``capabilities.vision`` is currently
``"unconfirmed"`` and whose model family supports multimodal image input
(GPT-4o / GPT-4.1 / GPT-5 / GPT-6 / Claude / Gemini / GLM-5 / VL models) to
``"confirmed"``, creating matching immutable ``model_profile_revisions`` rows
while keeping connectivity verification intact and leaving text-only models
(such as ``deepseek-v4-pro``) untouched.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.infrastructure.database.connection import WORKSPACE_ROOT
from backend.infrastructure.model.inference import infer_vision_support


def backup_sqlite(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}.pre_vision_v1.{stamp}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return backup_path


def migrate(db_path: Path, *, dry_run: bool = False, skip_backup: bool = False) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT profile_id, provider_id, upstream_model_id, label, family,
                   protocol_override, context_tokens, max_output_tokens,
                   thinking_modes_json, default_thinking, capabilities_json,
                   access_scope, verification_json, current_revision, source, checked_on
            FROM model_profiles
            WHERE archived_at IS NULL
            ORDER BY profile_id
            """
        ).fetchall()

        plan: list[tuple[sqlite3.Row, dict, str]] = []
        for row in rows:
            caps = json.loads(row["capabilities_json"] or "{}")
            current_vision = caps.get("vision", "unconfirmed")
            if current_vision != "unconfirmed":
                continue
            if not infer_vision_support(row["upstream_model_id"]):
                continue
            updated_caps = {**caps, "vision": "confirmed"}
            prov_rev = conn.execute(
                """
                SELECT id FROM model_provider_revisions
                WHERE provider_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (row["provider_id"],),
            ).fetchone()
            provider_rev_id = prov_rev["id"] if prov_rev else f"{row['provider_id']}:r1"
            plan.append((row, updated_caps, provider_rev_id))

        summary = {
            "database": str(db_path),
            "dry_run": dry_run,
            "upgraded_count": len(plan),
            "upgraded_profiles": [row["profile_id"] for row, _, _ in plan],
            "backup_path": None,
        }
        if dry_run or not plan:
            return summary

        if not skip_backup:
            summary["backup_path"] = str(backup_sqlite(db_path))

        now_iso = datetime.now(timezone.utc).isoformat()
        for row, updated_caps, provider_rev_id in plan:
            profile_id = row["profile_id"]
            next_revision = int(row["current_revision"] or 1) + 1
            verification = json.loads(row["verification_json"] or "{}")
            connectivity = verification.get("connectivity")
            if isinstance(connectivity, dict) and connectivity.get("status") == "verified":
                connectivity["profile_revision"] = next_revision
            facts = {
                "profile_id": profile_id,
                "provider_id": row["provider_id"],
                "model_id": row["upstream_model_id"],
                "label": row["label"],
                "family": row["family"],
                "protocol_override": row["protocol_override"],
                "context_tokens": row["context_tokens"],
                "max_output_tokens": row["max_output_tokens"],
                "thinking_modes": json.loads(row["thinking_modes_json"] or '["none"]'),
                "default_thinking": row["default_thinking"],
                "capabilities": updated_caps,
                "access_scope": row["access_scope"],
                "source": row["source"],
                "checked_on": row["checked_on"],
            }
            conn.execute(
                """
                UPDATE model_profiles
                SET capabilities_json = ?,
                    verification_json = ?,
                    current_revision = ?,
                    updated_at = ?
                WHERE profile_id = ?
                """,
                (
                    json.dumps(updated_caps, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(verification, ensure_ascii=False, separators=(",", ":")),
                    next_revision,
                    now_iso,
                    profile_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO model_profile_revisions
                    (id, profile_id, provider_revision_id, revision, facts_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{profile_id}:r{next_revision}",
                    profile_id,
                    provider_rev_id,
                    next_revision,
                    json.dumps(facts, ensure_ascii=False, separators=(",", ":")),
                    now_iso,
                ),
            )
        conn.commit()
        return summary
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Enable vision on inferred multimodal model profiles.")
    parser.add_argument(
        "--db",
        default=str(WORKSPACE_ROOT / "data" / "relational" / "zhouxu.db"),
        help="Path to SQLite database",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = migrate(Path(args.db), dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
