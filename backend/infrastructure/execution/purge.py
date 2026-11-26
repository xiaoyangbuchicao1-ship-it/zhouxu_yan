"""Delete what a conversation left on disk when it is permanently deleted."""

import shutil
from pathlib import Path

from backend.config.settings import BACKEND_ROOT, Settings
from backend.infrastructure.execution.workspace import _safe_part


def conversation_directories(settings: Settings, account_id: str, conversation_id: str) -> list[Path]:
    root = Path(settings.execution.workspace_root)
    root = ((BACKEND_ROOT.parent / root) if not root.is_absolute() else root).resolve()
    account, conversation = _safe_part(account_id), _safe_part(conversation_id)
    return [
        root / account / conversation,
        root / ".skill-packages" / account / conversation,
    ]


def account_directories(settings: Settings, account_id: str) -> list[Path]:
    root = Path(settings.execution.workspace_root)
    root = ((BACKEND_ROOT.parent / root) if not root.is_absolute() else root).resolve()
    account = _safe_part(account_id)
    return [
        root / account,
        root / ".skill-packages" / account,
    ]


def purge_conversation_files(settings: Settings, account_id: str, conversation_id: str) -> int:
    """Remove the conversation's generated files and installed skill copies.

    Returns how many directories were removed.  Anything that resolves outside
    the workspace root is left alone.
    """

    root = Path(settings.execution.workspace_root)
    root = ((BACKEND_ROOT.parent / root) if not root.is_absolute() else root).resolve()
    removed = 0
    for directory in conversation_directories(settings, account_id, conversation_id):
        resolved = directory.resolve()
        if root not in resolved.parents or not resolved.is_dir():
            continue
        shutil.rmtree(resolved)
        removed += 1
    return removed


def purge_account_files(settings: Settings, account_id: str) -> int:
    """Remove every generated workspace and installed skill copy owned by an account."""

    root = Path(settings.execution.workspace_root)
    root = ((BACKEND_ROOT.parent / root) if not root.is_absolute() else root).resolve()
    removed = 0
    for directory in account_directories(settings, account_id):
        resolved = directory.resolve()
        if root not in resolved.parents or not resolved.is_dir():
            continue
        shutil.rmtree(resolved)
        removed += 1
    return removed


def purge_material_files(settings: Settings, storage_keys: list[str]) -> int:
    """Remove original uploaded material files on disk for a deleted account."""

    configured = Path(settings.materials.storage_root)
    root = (configured if configured.is_absolute() else BACKEND_ROOT.parent / configured).resolve()
    removed = 0
    for key in storage_keys:
        if not key:
            continue
        path = (root / key).resolve()
        if root not in path.parents:
            continue
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
        for parent in (path.parent, path.parent.parent):
            if parent == root or root not in parent.parents:
                break
            try:
                parent.rmdir()
            except OSError:
                break
    return removed
