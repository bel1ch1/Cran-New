"""Shared camera and calibration timing configuration from environment."""

from __future__ import annotations

import os


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def calibration_tick_interval_s() -> float:
    return env_float("CRAN_CALIBRATION_TICK_INTERVAL_S", 0.12)


def camera_warmup_timeout_s() -> float:
    return max(1.0, env_float("CRAN_CAMERA_WARMUP_TIMEOUT_S", 15.0))


def camera_open_retry_s() -> float:
    return max(0.2, env_float("CRAN_CAMERA_OPEN_RETRY_S", 0.75))


def pose_release_timeout_s() -> float:
    return env_float("CRAN_POSE_RELEASE_TIMEOUT_S", 10.0)


def camera_release_delay_s(*, had_running_children: bool) -> float:
    default_delay_s = env_float("CRAN_CAMERA_RELEASE_DELAY_S", 1.0)
    if had_running_children:
        return default_delay_s
    return min(default_delay_s, 0.25)
