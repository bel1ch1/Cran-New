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
