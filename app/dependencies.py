from functools import lru_cache
from typing import Optional

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


def _make_camera_provider(camera_device: str, gstreamer_pipeline: Optional[str], settings) -> JetsonCameraFrameProvider:
    kwargs = dict(
        camera_device=camera_device,
        gstreamer_pipeline=gstreamer_pipeline or None,
        backend=settings.camera_backend,
    )
    if settings.camera_backend == "rpi5_libcamera":
        kwargs["width"] = settings.rpi5_camera_width
        kwargs["height"] = settings.rpi5_camera_height
        kwargs["framerate"] = settings.rpi5_camera_framerate
    return JetsonCameraFrameProvider(**kwargs)


@lru_cache(maxsize=1)
def get_bridge_runtime() -> BridgeCalibrationRuntime:
    settings = get_settings()
    bridge_provider = _make_camera_provider(
        settings.bridge_camera_device,
        settings.bridge_camera_pipeline,
        settings,
    )
    return BridgeCalibrationRuntime(camera_provider=bridge_provider)


@lru_cache(maxsize=1)
def get_hook_runtime() -> HookCalibrationRuntime:
    settings = get_settings()
    hook_provider = _make_camera_provider(
        settings.hook_camera_device,
        settings.hook_camera_pipeline,
        settings,
    )
    return HookCalibrationRuntime(camera_provider=hook_provider)

