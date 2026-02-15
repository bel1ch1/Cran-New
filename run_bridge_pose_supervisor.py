#!/usr/bin/env python3
"""
Supervisor for bridge_pose_modbus.py.

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supervisor for bridge_pose_modbus.py")
    parser.add_argument("--restart-delay", type=float, default=1.0, help="Delay before restart (seconds)")
    parser.add_argument("--python", default=sys.executable, help="Python executable for child process")
    parser.add_argument("--pid-file", type=Path, default=Path("data/runtime/bridge_pose_supervisor.pid"))
    parser.add_argument("--child-pid-file", type=Path, default=Path("data/runtime/bridge_pose_modbus.pid"))
    parser.add_argument("child_args", nargs=argparse.REMAINDER, help="Arguments forwarded to bridge_pose_modbus.py")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.pid_file.parent.mkdir(parents=True, exist_ok=True)
    args.child_pid_file.parent.mkdir(parents=True, exist_ok=True)
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

    child_cmd = [args.python, "bridge_pose_modbus.py", *args.child_args]
    print(f"[SUPERVISOR] bridge command: {' '.join(child_cmd)}")
    try:
        while not stop["value"]:
            child = subprocess.Popen(child_cmd)
            args.child_pid_file.write_text(str(child.pid), encoding="utf-8")
            while child.poll() is None and not stop["value"]:
                time.sleep(0.3)
            if stop["value"]:
                break
            print(f"[SUPERVISOR] bridge exited with code={child.returncode}, restarting...")
            time.sleep(max(0.1, float(args.restart_delay)))
    finally:
        if child is not None and child.poll() is None:
            try:
                child.terminate()
                child.wait(timeout=3)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
        try:
            args.pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            args.child_pid_file.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
