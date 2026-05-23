from functools import lru_cache

from fastapi.templating import Jinja2Templates

from app.core.settings import get_settings
from app.services.calibration_runtime import BridgeCalibrationRuntime, HookCalibrationRuntime
from app.services.camera_frame_provider import CameraFrameProvider
from app.services.config_store import ConfigStore


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    settings = get_settings()
    return Jinja2Templates(directory=str(settings.templates_dir))


@lru_cache(maxsize=1)
def get_config_store() -> ConfigStore:
    settings = get_settings()
    return ConfigStore(settings.config_file)


def _build_bridge_runtime() -> BridgeCalibrationRuntime:
    settings = get_settings()
    provider = CameraFrameProvider(
        camera_device=settings.bridge_camera_device,
        gstreamer_pipeline=settings.bridge_camera_pipeline or None,
    )
    return BridgeCalibrationRuntime(camera_provider=provider)


def _build_hook_runtime() -> HookCalibrationRuntime:
    settings = get_settings()
    provider = CameraFrameProvider(
        camera_device=settings.hook_camera_device,
        gstreamer_pipeline=settings.hook_camera_pipeline or None,
    )
    return HookCalibrationRuntime(camera_provider=provider)


@lru_cache(maxsize=1)
def get_bridge_runtime() -> BridgeCalibrationRuntime:
    return _build_bridge_runtime()


@lru_cache(maxsize=1)
def get_hook_runtime() -> HookCalibrationRuntime:
    return _build_hook_runtime()


def reset_bridge_runtime() -> BridgeCalibrationRuntime:
    get_bridge_runtime.cache_clear()
    runtime = get_bridge_runtime()
    runtime.camera_provider.reset()
    return runtime


def reset_hook_runtime() -> HookCalibrationRuntime:
    get_hook_runtime.cache_clear()
    runtime = get_hook_runtime()
    runtime.camera_provider.reset()
    return runtime


def persist_bridge_runtime_to_store() -> bool:
    """Write in-memory bridge calibration snapshot to config file."""
    runtime = get_bridge_runtime()
    state = runtime.last_state
    if not state:
        return False

    store = get_config_store()
    store.update_bridge_runtime_result(
        crane_x_m=float(state.get("crane_x_m") or 0.0),
        trolley_y_m=float(state.get("trolley_y_m") or 0.0),
        known_marker_count=state.get("known_marker_count"),
        calibration_quality=state.get("calibration_quality"),
        marker_positions_m=state.get("marker_positions_m"),
        roi_preview=state.get("roi_preview"),
        movement_direction=state.get("movement_direction"),
    )
    return True


def merge_bridge_calibration_view() -> dict:
    """Merge persisted config with live runtime snapshot for UI display."""
    stored = get_config_store().get_calibration_data()
    runtime_state = get_bridge_runtime().last_state or {}
    if not runtime_state:
        return stored

    merged = dict(stored)
    runtime_markers = runtime_state.get("marker_positions_m") or {}
    stored_markers = stored.get("marker_positions_m") or {}
    if len(runtime_markers) >= len(stored_markers):
        merged["marker_positions_m"] = runtime_markers
    else:
        merged["marker_positions_m"] = stored_markers

    if runtime_state.get("roi_preview"):
        merged["roi_preview"] = runtime_state.get("roi_preview")
    if runtime_state.get("movement_direction"):
        merged["movement_direction"] = runtime_state.get("movement_direction")

    merged["result"] = dict(stored.get("result", {}))
    if runtime_state.get("crane_x_m") is not None:
        merged["result"]["crane_x_m"] = runtime_state.get("crane_x_m")
    if runtime_state.get("trolley_y_m") is not None:
        merged["result"]["trolley_y_m"] = runtime_state.get("trolley_y_m")
    if runtime_state.get("known_marker_count") is not None:
        merged["result"]["known_marker_count"] = runtime_state.get("known_marker_count")
    if runtime_state.get("calibration_quality") is not None:
        merged["result"]["calibration_quality"] = runtime_state.get("calibration_quality")
    return merged
