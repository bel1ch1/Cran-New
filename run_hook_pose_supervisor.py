#!/usr/bin/env python3
"""
Supervisor for hook_pose_modbus.py.

Restarts child process on any exit until supervisor itself is stopped.
Writes PID file so web calibration runtime can stop it and free camera access.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_path(name: str, default: str) -> Path:
    raw = (os.getenv(name) or "").strip()
    return Path(raw) if raw else Path(default)


def _preferred_python_executable() -> str:
    explicit = (os.getenv("CRAN_CHILD_PYTHON") or os.getenv("CRAN_PYTHON_EXECUTABLE") or "").strip()
    if explicit:
        return explicit
    venv_python = Path("/home/cran/cran/venv/bin/python")
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supervisor for hook_pose_modbus.py")
    parser.add_argument(
        "--restart-delay",
        type=float,
        default=_env_float("CRAN_HOOK_RESTART_DELAY", 1.0),
        help="Delay before restart (seconds)",
    )
    parser.add_argument(
        "--restart-backoff-max",
        type=float,
        default=_env_float("CRAN_SUPERVISOR_RESTART_BACKOFF_MAX", 8.0),
        help="Maximum restart delay when child exits repeatedly",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=_env_path("CRAN_SUPERVISOR_LOCK_FILE", "data/runtime/calibration.lock"),
        help="When lock file exists, child process is kept stopped",
    )
    parser.add_argument(
        "--lock-poll-interval",
        type=float,
        default=_env_float("CRAN_SUPERVISOR_LOCK_POLL_INTERVAL", 0.5),
        help="Polling interval while waiting for lock release",
    )
    parser.add_argument(
        "--heartbeat-file",
        type=Path,
        default=Path("data/runtime/hook_pose_supervisor.heartbeat"),
        help="Heartbeat file updated by supervisor loop",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=_env_float("CRAN_SUPERVISOR_HEARTBEAT_INTERVAL", 2.0),
        help="Heartbeat update interval in seconds",
    )
    parser.add_argument("--python", default=_preferred_python_executable(), help="Python executable for child process")
    parser.add_argument("--pid-file", type=Path, default=Path("data/runtime/hook_pose_supervisor.pid"))
    parser.add_argument("--child-pid-file", type=Path, default=Path("data/runtime/hook_pose_modbus.pid"))
    parser.add_argument("child_args", nargs=argparse.REMAINDER, help="Arguments forwarded to hook_pose_modbus.py")
    return parser.parse_args()


def _is_lock_active(lock_file: Path) -> bool:
    return lock_file.exists()


def _write_heartbeat(path: Path) -> None:
    try:
        path.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass


def _terminate_child(child: subprocess.Popen | None, child_pid_file: Path) -> None:
    if child is None:
        return
    if child.poll() is not None:
        try:
            child_pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        return
    try:
        child.terminate()
        child.wait(timeout=3)
    except Exception:
        try:
            child.kill()
        except Exception:
            pass
    try:
        child_pid_file.unlink(missing_ok=True)
    except Exception:
        pass


def main() -> int:
    args = parse_args()
    args.pid_file.parent.mkdir(parents=True, exist_ok=True)
    args.child_pid_file.parent.mkdir(parents=True, exist_ok=True)
    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    args.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    args.pid_file.write_text(str(os.getpid()), encoding="utf-8")

    stop = {"value": False}
    child: subprocess.Popen | None = None

    def _handle_stop(_sig, _frame) -> None:
        stop["value"] = True
        if child is not None and child.poll() is None:
            try:
                child.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    child_args = list(args.child_args)
    if child_args and child_args[0] == "--":
        child_args = child_args[1:]
    child_cmd = [args.python, "hook_pose_modbus.py", *child_args]
    print(f"[SUPERVISOR] hook command: {' '.join(child_cmd)}")
    base_restart_delay = max(0.1, float(args.restart_delay))
    max_restart_delay = max(base_restart_delay, float(args.restart_backoff_max))
    next_restart_delay = base_restart_delay
    last_heartbeat_ts = 0.0
    lock_wait_msg_ts = 0.0
    child_started_at = 0.0
    try:
        while not stop["value"]:
            now = time.time()
            if now - last_heartbeat_ts >= max(0.2, float(args.heartbeat_interval)):
                _write_heartbeat(args.heartbeat_file)
                last_heartbeat_ts = now

            if _is_lock_active(args.lock_file):
                if child is not None:
                    print("[SUPERVISOR] calibration lock detected, stopping hook child...")
                    _terminate_child(child, args.child_pid_file)
                    child = None
                if now - lock_wait_msg_ts >= 5:
                    print("[SUPERVISOR] hook paused while calibration lock is active")
                    lock_wait_msg_ts = now
                time.sleep(max(0.1, float(args.lock_poll_interval)))
                continue

            if child is None:
                child = subprocess.Popen(child_cmd)
                args.child_pid_file.write_text(str(child.pid), encoding="utf-8")
                child_started_at = time.time()

            if child.poll() is None:
                time.sleep(0.3)
                continue

            print(f"[SUPERVISOR] hook exited with code={child.returncode}, restarting...")
            _terminate_child(child, args.child_pid_file)
            child = None
            time.sleep(next_restart_delay)
            if time.time() - child_started_at >= 10.0:
                next_restart_delay = base_restart_delay
            else:
                next_restart_delay = min(next_restart_delay * 2.0, max_restart_delay)
    finally:
        _terminate_child(child, args.child_pid_file)
        try:
            args.pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            args.child_pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            args.heartbeat_file.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
