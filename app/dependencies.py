from functools import lru_cache

from fastapi.templating import Jinja2Templates

from app.core.settings import get_settings
from app.services.calibration_runtime import BridgeCalibrationRuntime, HookCalibrationRuntime
from app.services.config_store import ConfigStore
from app.services.jetson_camera_provider import JetsonCameraFrameProvider


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    settings = get_settings()
    return Jinja2Templates(directory=str(settings.templates_dir))


@lru_cache(maxsize=1)
def get_config_store() -> ConfigStore:
    settings = get_settings()
    return ConfigStore(settings.config_file)


@lru_cache(maxsize=1)
def get_bridge_runtime() -> BridgeCalibrationRuntime:
    settings = get_settings()
    bridge_provider = JetsonCameraFrameProvider(
        camera_device=settings.bridge_camera_device,
        gstreamer_pipeline=settings.bridge_camera_pipeline or None,
    )
    return BridgeCalibrationRuntime(camera_provider=bridge_provider)


@lru_cache(maxsize=1)
def get_hook_runtime() -> HookCalibrationRuntime:
    settings = get_settings()
    hook_provider = JetsonCameraFrameProvider(
        camera_device=settings.hook_camera_device,
        gstreamer_pipeline=settings.hook_camera_pipeline or None,
    )
    return HookCalibrationRuntime(camera_provider=hook_provider)

