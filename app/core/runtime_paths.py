from __future__ import annotations

import os
from pathlib import Path


def _project_base_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_project_path(relative_or_absolute: str | Path) -> Path:
    path = Path(relative_or_absolute)
    if path.is_absolute():
        return path
    return _project_base_dir() / path


def calibration_lock_path() -> Path:
    raw = (os.getenv("CRAN_SUPERVISOR_LOCK_FILE") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _project_base_dir() / path
    else:
        path = _project_base_dir() / "data" / "runtime" / "calibration.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
