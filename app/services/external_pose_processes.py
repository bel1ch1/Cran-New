"""Backward-compatible exports for calibration/pose process coordination."""

from app.services.calibration_session import (
    acquire_calibration_session,
    ensure_pose_supervisor_scripts_running,
    release_calibration_session,
    release_pose_cameras,
    reset_calibration_session_counter,
    stop_pose_supervisor_scripts,
    wait_pose_children_released,
)

__all__ = [
    "acquire_calibration_session",
    "ensure_pose_supervisor_scripts_running",
    "release_calibration_session",
    "release_pose_cameras",
    "reset_calibration_session_counter",
    "stop_pose_supervisor_scripts",
    "wait_pose_children_released",
]
