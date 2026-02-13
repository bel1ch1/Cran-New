import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str
    session_secret: str
    auth_user: str
    auth_password: str
    base_dir: Path
    templates_dir: Path
    data_dir: Path
    config_file: Path
    use_jetson_cameras: bool
    bridge_camera_device: str
    hook_camera_device: str
    bridge_camera_pipeline: str
    hook_camera_pipeline: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    base_dir = Path(__file__).resolve().parents[2]
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    use_jetson_cameras = os.getenv("CRAN_USE_JETSON_CAMERAS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return Settings(
        app_name=os.getenv("CRAN_APP_NAME", "CRAN Calibration Console"),
        session_secret=os.getenv("CRAN_SESSION_SECRET", "change-this-in-production"),
        auth_user=os.getenv("CRAN_AUTH_USER", "admin"),
        auth_password=os.getenv("CRAN_AUTH_PASSWORD", "admin"),
        base_dir=base_dir,
        templates_dir=base_dir / "templates",
        data_dir=data_dir,
        config_file=data_dir / "calibration_config.json",
        use_jetson_cameras=use_jetson_cameras,
        bridge_camera_device=os.getenv("CRAN_BRIDGE_CAMERA_DEVICE", "0"),
        hook_camera_device=os.getenv("CRAN_HOOK_CAMERA_DEVICE", "0"),
        bridge_camera_pipeline=os.getenv("CRAN_BRIDGE_CAMERA_PIPELINE", ""),
        hook_camera_pipeline=os.getenv("CRAN_HOOK_CAMERA_PIPELINE", ""),
    )

