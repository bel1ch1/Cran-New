from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.runtime_paths import calibration_lock_path, resolve_project_path


class SupervisorRole(str, Enum):
    BRIDGE = "bridge"
    HOOK = "hook"


@dataclass(frozen=True)
class SupervisorSpec:
    role: SupervisorRole
    child_script: str
    restart_delay_env: str
    default_restart_delay: float
    default_heartbeat_file: str
    default_pid_file: str
    default_child_pid_file: str


SPECS = {
    SupervisorRole.BRIDGE: SupervisorSpec(
        role=SupervisorRole.BRIDGE,
        child_script="bridge_pose_modbus.py",
        restart_delay_env="CRAN_BRIDGE_RESTART_DELAY",
        default_restart_delay=1.0,
        default_heartbeat_file="data/runtime/bridge_pose_supervisor.heartbeat",
        default_pid_file="data/runtime/bridge_pose_supervisor.pid",
        default_child_pid_file="data/runtime/bridge_pose_modbus.pid",
    ),
    SupervisorRole.HOOK: SupervisorSpec(
        role=SupervisorRole.HOOK,
        child_script="hook_pose_modbus.py",
        restart_delay_env="CRAN_HOOK_RESTART_DELAY",
        default_restart_delay=1.0,
        default_heartbeat_file="data/runtime/hook_pose_supervisor.heartbeat",
        default_pid_file="data/runtime/hook_pose_supervisor.pid",
        default_child_pid_file="data/runtime/hook_pose_modbus.pid",
    ),
}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _preferred_python_executable() -> str:
    explicit = (os.getenv("CRAN_CHILD_PYTHON") or os.getenv("CRAN_PYTHON_EXECUTABLE") or "").strip()
    if explicit:
        return explicit
    return sys.executable


def parse_supervisor_args(role: SupervisorRole) -> argparse.Namespace:
    spec = SPECS[role]
    parser = argparse.ArgumentParser(description=f"Supervisor for {spec.child_script}")
    parser.add_argument(
        "--restart-delay",
        type=float,
        default=_env_float(spec.restart_delay_env, spec.default_restart_delay),
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
        default=calibration_lock_path(),
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
        default=resolve_project_path(spec.default_heartbeat_file),
        help="Heartbeat file updated by supervisor loop",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=_env_float("CRAN_SUPERVISOR_HEARTBEAT_INTERVAL", 2.0),
        help="Heartbeat update interval in seconds",
    )
    parser.add_argument("--python", default=_preferred_python_executable(), help="Python executable for child process")
    parser.add_argument("--pid-file", type=Path, default=resolve_project_path(spec.default_pid_file))
    parser.add_argument("--child-pid-file", type=Path, default=resolve_project_path(spec.default_child_pid_file))
    parser.add_argument("child_args", nargs=argparse.REMAINDER, help=f"Arguments forwarded to {spec.child_script}")
    return parser.parse_args()


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


def run_pose_supervisor(role: SupervisorRole) -> int:
    spec = SPECS[role]
    args = parse_supervisor_args(role)

    for path in (args.pid_file, args.child_pid_file, args.lock_file, args.heartbeat_file):
        path.parent.mkdir(parents=True, exist_ok=True)
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
    child_cmd = [args.python, spec.child_script, *child_args]
    print(f"[SUPERVISOR] {spec.role.value} command: {' '.join(child_cmd)}")

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

            if args.lock_file.exists():
                if child is not None:
                    print(f"[SUPERVISOR] calibration lock detected, stopping {spec.role.value} child...")
                    _terminate_child(child, args.child_pid_file)
                    child = None
                if now - lock_wait_msg_ts >= 5:
                    print(f"[SUPERVISOR] {spec.role.value} paused while calibration lock is active")
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

            print(f"[SUPERVISOR] {spec.role.value} exited with code={child.returncode}, restarting...")
            _terminate_child(child, args.child_pid_file)
            child = None
            time.sleep(next_restart_delay)
            if time.time() - child_started_at >= 10.0:
                next_restart_delay = base_restart_delay
            else:
                next_restart_delay = min(next_restart_delay * 2.0, max_restart_delay)
    finally:
        _terminate_child(child, args.child_pid_file)
        for path in (args.pid_file, args.child_pid_file, args.heartbeat_file):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
    return 0
