from __future__ import annotations

import os
import time
from pathlib import Path

from app.core.settings import get_settings


def _runtime_dir() -> Path:
    settings = get_settings()
    path = settings.data_dir / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pose_child_pid_files() -> list[Path]:
    runtime = _runtime_dir()
    return [
        runtime / "bridge_pose_modbus.pid",
        runtime / "hook_pose_modbus.pid",
    ]


def _lock_file_path() -> Path:
    settings = get_settings()
    raw = (os.getenv("CRAN_SUPERVISOR_LOCK_FILE") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = settings.base_dir / path
    else:
        path = settings.data_dir / "runtime" / "calibration.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _set_calibration_lock() -> None:
    lock_file = _lock_file_path()
    try:
        lock_file.write_text(f"{int(time.time())}:{os.getpid()}\n", encoding="utf-8")
    except Exception:
        pass


def _clear_calibration_lock() -> None:
    lock_file = _lock_file_path()
    try:
        lock_file.unlink(missing_ok=True)
    except Exception:
        pass


def _pose_children_running() -> bool:
    # Supervisors are expected to remove child PID files when children stop.
    return any(pid_file.exists() for pid_file in _pose_child_pid_files())


def wait_pose_children_released(timeout_s: float = 5.0) -> bool:
    deadline = time.time() + max(0.2, timeout_s)
    while time.time() < deadline:
        if not _pose_children_running():
            return True
        time.sleep(0.1)
    return not _pose_children_running()


def stop_pose_supervisor_scripts() -> None:
    _set_calibration_lock()


def ensure_pose_supervisor_scripts_running() -> None:
    _clear_calibration_lock()
