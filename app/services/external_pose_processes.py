from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.core.settings import get_settings


def _runtime_dir() -> Path:
    settings = get_settings()
    path = settings.data_dir / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop_pid(pid: int, timeout_s: float = 3.0) -> None:
    if pid <= 0:
        return
    if not _is_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.time() + max(0.5, timeout_s)
    while time.time() < deadline:
        if not _is_running(pid):
            return
        time.sleep(0.1)

    # Last resort.
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, sigkill)
    except OSError:
        pass


def _stop_from_pid_file(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except Exception:
        pid = -1
    _stop_pid(pid)
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass


def _read_pid_from_file(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except Exception:
        return None
    return pid if pid > 0 else None


def _spawn_supervisor(script_name: str) -> None:
    settings = get_settings()
    script_path = settings.base_dir / script_name
    if not script_path.exists():
        return
    cmd = [sys.executable, str(script_path)]
    kwargs: dict = {
        "cwd": str(settings.base_dir),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "start_new_session": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(cmd, **kwargs)


def stop_pose_supervisor_scripts() -> None:
    runtime = _runtime_dir()
    pid_files = [
        runtime / "bridge_pose_supervisor.pid",
        runtime / "hook_pose_supervisor.pid",
        runtime / "bridge_pose_modbus.pid",
        runtime / "hook_pose_modbus.pid",
    ]
    for pid_file in pid_files:
        _stop_from_pid_file(pid_file)


def ensure_pose_supervisor_scripts_running() -> None:
    runtime = _runtime_dir()
    targets = [
        ("bridge_pose_supervisor.pid", "run_bridge_pose_supervisor.py"),
        ("hook_pose_supervisor.pid", "run_hook_pose_supervisor.py"),
    ]
    for pid_file_name, script_name in targets:
        pid_file = runtime / pid_file_name
        pid = _read_pid_from_file(pid_file)
        if pid is not None and _is_running(pid):
            continue
        _spawn_supervisor(script_name)
