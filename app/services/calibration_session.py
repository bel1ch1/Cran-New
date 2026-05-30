"""Coordinate calibration websocket sessions with pose supervisor processes."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from app.core.runtime_paths import calibration_lock_path
from app.core.settings import get_settings
from app.services.camera_config import camera_release_delay_s, pose_release_timeout_s

logger = logging.getLogger(__name__)


class PoseChildRegistry:
    @staticmethod
    def runtime_dir() -> Path:
        settings = get_settings()
        path = settings.data_dir / "runtime"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def pid_files(cls) -> list[Path]:
        runtime = cls.runtime_dir()
        return [
            runtime / "bridge_pose_modbus.pid",
            runtime / "hook_pose_modbus.pid",
        ]

    @staticmethod
    def _read_pid(path: Path) -> int | None:
        try:
            raw = path.read_text(encoding="utf-8").strip()
            return int(raw) if raw else None
        except Exception:
            return None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @classmethod
    def _remove_stale_pid_file(cls, pid_file: Path) -> None:
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass

    @classmethod
    def any_running(cls) -> bool:
        running = False
        for pid_file in cls.pid_files():
            if not pid_file.exists():
                continue
            pid = cls._read_pid(pid_file)
            if pid is not None and cls._process_alive(pid):
                running = True
                continue
            cls._remove_stale_pid_file(pid_file)
        return running

    @classmethod
    def wait_until_released(cls, timeout_s: float) -> bool:
        deadline = time.time() + max(0.2, timeout_s)
        while time.time() < deadline:
            if not cls.any_running():
                return True
            time.sleep(0.1)
        if cls.any_running():
            logger.warning("Pose child processes still running after %.1fs", timeout_s)
            return False
        return True


class CalibrationSessionCoordinator:
    _active_sessions = 0

    @classmethod
    def reset_counter(cls) -> None:
        cls._active_sessions = 0

    @classmethod
    def acquire(cls) -> None:
        cls._active_sessions += 1
        if cls._active_sessions == 1:
            cls._set_lock()
        cls._release_pose_cameras()

    @classmethod
    def release(cls) -> None:
        cls._active_sessions = max(0, cls._active_sessions - 1)
        if cls._active_sessions == 0:
            delay_s = camera_release_delay_s(had_running_children=True)
            if delay_s > 0:
                time.sleep(delay_s)
            cls._clear_lock()

    @staticmethod
    def _set_lock() -> None:
        lock_file = calibration_lock_path()
        try:
            lock_file.write_text(f"{int(time.time())}:{os.getpid()}\n", encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _clear_lock() -> None:
        try:
            calibration_lock_path().unlink(missing_ok=True)
        except Exception:
            pass

    @classmethod
    def _release_pose_cameras(cls) -> bool:
        had_children = PoseChildRegistry.any_running()
        if had_children or not calibration_lock_path().exists():
            cls._set_lock()
        released = PoseChildRegistry.wait_until_released(pose_release_timeout_s())
        delay_s = camera_release_delay_s(had_running_children=had_children)
        if delay_s > 0:
            time.sleep(delay_s)
        return released


def reset_calibration_session_counter() -> None:
    CalibrationSessionCoordinator.reset_counter()


def acquire_calibration_session() -> None:
    CalibrationSessionCoordinator.acquire()


def release_calibration_session() -> None:
    CalibrationSessionCoordinator.release()


def stop_pose_supervisor_scripts() -> None:
    CalibrationSessionCoordinator._set_lock()


def ensure_pose_supervisor_scripts_running() -> None:
    CalibrationSessionCoordinator._clear_lock()


def wait_pose_children_released(timeout_s: float = 5.0) -> bool:
    return PoseChildRegistry.wait_until_released(timeout_s)


def release_pose_cameras() -> bool:
    return CalibrationSessionCoordinator._release_pose_cameras()
