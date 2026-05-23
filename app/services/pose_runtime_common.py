from __future__ import annotations

import signal
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import cv2
import numpy as np

from app.services.aruco_common import ARUCO_DICT, ARUCO_PARAMS
from app.services.camera_backends import build_jetson_gstreamer_pipeline
from app.services.camera_frame_provider import CameraFrameProvider
from app.services.pose_modbus_common import resolve_pose_camera_device

ConfigT = TypeVar("ConfigT")


def detect_markers(gray_frame: np.ndarray):
    if ARUCO_DICT is None or ARUCO_PARAMS is None:
        raise RuntimeError("OpenCV ArUco is not available")
    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(ARUCO_DICT, ARUCO_PARAMS)
        return detector.detectMarkers(gray_frame)
    return cv2.aruco.detectMarkers(gray_frame, ARUCO_DICT, parameters=ARUCO_PARAMS)


def decode_jpeg_frame(frame_bytes: bytes) -> np.ndarray | None:
    if not frame_bytes:
        return None
    try:
        np_buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
        return cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
    except Exception:
        return None


def install_stop_handlers() -> dict[str, bool]:
    stop = {"value": False}

    def _handle_stop(_sig, _frame) -> None:
        stop["value"] = True

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    return stop


class PoseCameraSession:
    def __init__(
        self,
        *,
        camera_id: int,
        use_gstreamer: bool,
        config_path: Path,
        camera_id_override: int | None = None,
        role: str | None = None,
        camera_device: str | None = None,
    ) -> None:
        self.config_path = config_path
        self.camera_id_override = camera_id_override
        self.role = role
        self._camera_id = camera_id
        self._use_gstreamer = use_gstreamer
        self._camera_device = camera_device or resolve_pose_camera_device(role, camera_id)
        self._config_mtime = config_path.stat().st_mtime
        self.provider = self._create_provider()

    @property
    def camera_id(self) -> int:
        return self._camera_id

    @property
    def camera_device(self) -> str:
        return self._camera_device

    def _create_provider(self) -> CameraFrameProvider:
        pipeline = build_jetson_gstreamer_pipeline(self._camera_id) if self._use_gstreamer else None
        return CameraFrameProvider(
            camera_device=self._camera_device,
            gstreamer_pipeline=pipeline,
        )

    def set_camera_id(self, camera_id: int) -> None:
        if camera_id == self._camera_id:
            return
        self._camera_id = camera_id
        self._camera_device = resolve_pose_camera_device(self.role, camera_id)
        self.provider.close()
        self.provider = self._create_provider()

    def read_frame(self) -> np.ndarray | None:
        return decode_jpeg_frame(self.provider.get_frame_bytes())

    def reload_config_if_changed(
        self,
        loader: Callable[[Path], ConfigT],
        on_reload: Callable[[ConfigT], None] | None = None,
    ) -> ConfigT | None:
        current_mtime = self.config_path.stat().st_mtime
        if current_mtime == self._config_mtime:
            return None

        cfg = loader(self.config_path)
        camera_id = self.camera_id_override if self.camera_id_override is not None else int(cfg.camera_id)
        self.set_camera_id(camera_id)
        self._config_mtime = current_mtime
        if on_reload is not None:
            on_reload(cfg)
        return cfg

    def close(self) -> None:
        self.provider.close()
