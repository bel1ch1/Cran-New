"""Shared camera, pose runtime, spatial map, and Modbus configuration from environment."""

from __future__ import annotations

import os

DEFAULT_POSE_FPS = 8.0


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    return max(0, int(env_float(name, float(default))))


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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


def pose_smooth_alpha() -> float:
    """EMA blend for pose runtime: 0 = off, 1 = no smoothing."""
    return max(0.0, min(1.0, env_float("CRAN_POSE_SMOOTH_ALPHA", 0.55)))


def pose_max_step_m() -> float:
    return max(0.0, env_float("CRAN_POSE_MAX_STEP_M", 0.022))


def pose_outlier_m() -> float:
    return max(0.0, env_float("CRAN_POSE_OUTLIER_M", 0.020))


def pose_window_size() -> int:
    return max(1, int(env_float("CRAN_POSE_WINDOW", 5)))


def pose_fps() -> float:
    return max(0.5, env_float("CRAN_POSE_FPS", DEFAULT_POSE_FPS))


def resolve_pose_fps(cli_fps: float | None = None) -> float:
    """Prefer CRAN_POSE_FPS when set; otherwise use CLI --fps."""
    if os.getenv("CRAN_POSE_FPS") is not None:
        return pose_fps()
    if cli_fps is None:
        return pose_fps()
    return max(0.5, float(cli_fps))


def pose_hold_last_valid() -> bool:
    return env_bool("CRAN_POSE_HOLD_LAST", True)


def pose_skip_jpeg() -> bool:
    return env_bool("CRAN_POSE_SKIP_JPEG", True)


def pose_use_subpix() -> bool:
    return env_bool("CRAN_POSE_USE_SUBPIX", True)


def pose_use_solvepnp() -> bool:
    return env_bool("CRAN_POSE_USE_SOLVEPNP", True)


def pose_debug() -> bool:
    raw = os.getenv("CRAN_POSE_DEBUG", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def spatial_min_trust_hits() -> int:
    return max(1, env_int("CRAN_MIN_TRUST_HITS", 7))


def spatial_max_trust_sigma_m() -> float:
    return max(0.0, env_float("CRAN_MAX_TRUST_SIGMA_M", 0.08))


def spatial_min_landmark_separation_m() -> float:
    return max(0.0, env_float("CRAN_MIN_LANDMARK_SEPARATION_M", 0.03))


def spatial_merge_tolerance_m() -> float:
    return max(0.0, env_float("CRAN_MERGE_TOLERANCE_M", 0.02))


def spatial_runtime_match_tolerance_m() -> float:
    return max(0.0, env_float("CRAN_RUNTIME_MATCH_TOLERANCE_M", 0.04))


def modbus_port() -> int:
    return env_int("CRAN_MODBUS_PORT", 5020)


def modbus_unit_id() -> int:
    return max(1, env_int("CRAN_MODBUS_UNIT_ID", 1))


def modbus_bridge_base_register() -> int:
    return env_int("CRAN_MODBUS_BRIDGE_BASE_REGISTER", 100)


def modbus_hook_base_register() -> int:
    return env_int("CRAN_MODBUS_HOOK_BASE_REGISTER", 200)
