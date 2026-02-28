from typing import Optional, Set

try:
    import cv2
except Exception:
    cv2 = None


# Backend names: "jetson" (nvarguscamerasrc), "rpi5_libcamera" (libcamerasrc for RPi 5 + IMX219/CSI)
CAMERA_BACKEND_JETSON = "jetson"
CAMERA_BACKEND_RPI5_LIBCAMERA = "rpi5_libcamera"

# RPi 5 + libcamera: в одном процессе нельзя стабильно держать открытыми две камеры.
# Провайдеры с rpi5_libcamera регистрируются здесь; при открытии одной остальные закрываются.
_rpi5_providers: Set["JetsonCameraFrameProvider"] = set()


class JetsonCameraFrameProvider:
    """
    Integration point for CSI camera capture via GStreamer.

    Supports:
    - Jetson: nvarguscamerasrc (sensor-id 0/1).
    - Raspberry Pi 5: libcamerasrc for IMX 219 (Camera Module v2.1) on MIPI CSI.
      Requires gstreamer1.0-libcamera. Camera selected by full path from
      `libcamera-hello --list-cameras` or `rpicam-vid --list-cameras` (e.g.
      /base/axi/pcie@1000120000/rp1/i2c@80000/imx219@10). For two CSI cameras
      set CRAN_BRIDGE_CAMERA_DEVICE and CRAN_HOOK_CAMERA_DEVICE to those paths.
      Важно: на RPi 5 в одном процессе одновременно может быть открыта только одна
      камера (ограничение libcamera). Для двух камер задавайте полные пути из
      rpicam-vid --list-cameras для каждой (0 = первая, вторая — только по пути).
    """

    def __init__(
        self,
        camera_device: str,
        gstreamer_pipeline: Optional[str] = None,
        *,
        backend: str = CAMERA_BACKEND_JETSON,
        width: int = 1920,
        height: int = 1080,
        framerate: str = "10/1",
    ) -> None:
        self.camera_device = camera_device.strip()
        self.gstreamer_pipeline = gstreamer_pipeline
        self.backend = (backend or CAMERA_BACKEND_JETSON).strip().lower()
        self.width = width
        self.height = height
        self.framerate = framerate
        self._capture = None
        if self.backend == CAMERA_BACKEND_RPI5_LIBCAMERA:
            _rpi5_providers.add(self)

    def _resolve_sensor_id(self) -> int:
        source = self.camera_device
        if isinstance(source, str) and source.isdigit():
            return int(source)
        if isinstance(source, str) and source.startswith("/dev/video"):
            suffix = source.replace("/dev/video", "")
            if suffix.isdigit():
                return int(suffix)
        return 0

    def _build_jetson_pipeline(self, sensor_id: int) -> str:
        return (
            f"nvarguscamerasrc sensor-id={sensor_id} ! "
            "video/x-raw(memory:NVMM), width=1920, height=1080, framerate=29/1 ! "
            "nvvidconv ! video/x-raw, format=BGRx ! "
            "videoconvert ! video/x-raw, format=BGR ! "
            "appsink drop=1"
        )

    def _build_rpi5_libcamera_pipeline(self) -> str:
        # libcamerasrc for RPi 5 + IMX219 (Camera Module v2.1) on CSI.
        # camera_device must be full camera path from: libcamera-hello --list-cameras
        # Example: /base/axi/pcie@120000/rp1/i2c@80000/imx219@10
        if self.camera_device.startswith("/"):
            name_part = f"camera-name={self.camera_device}"
        else:
            # "0" = first camera (omit camera-name); for second camera use full path from --list-cameras
            name_part = "" if self.camera_device == "0" else f"camera-name={self.camera_device}"
        src = ("libcamerasrc " + name_part + " ! ") if name_part else "libcamerasrc ! "
        return (
            f"{src} "
            f"video/x-raw,width={self.width},height={self.height},framerate={self.framerate},format=NV12 ! "
            "queue ! videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
        )

    def _build_default_pipeline(self) -> str:
        if self.backend == CAMERA_BACKEND_RPI5_LIBCAMERA:
            return self._build_rpi5_libcamera_pipeline()
        return self._build_jetson_pipeline(self._resolve_sensor_id())

    def _close_other_rpi5_captures(self) -> None:
        """Закрыть остальные RPi5-провайдеры, чтобы была открыта только одна камера."""
        for other in _rpi5_providers:
            if other is not self and other._capture is not None:
                try:
                    other._capture.release()
                except Exception:
                    pass
                other._capture = None

    def _open_capture(self):
        if cv2 is None:
            return None
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        if self.backend == CAMERA_BACKEND_RPI5_LIBCAMERA:
            self._close_other_rpi5_captures()

        pipeline = self.gstreamer_pipeline or self._build_default_pipeline()
        self._capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        # Fallback for development: try index /dev/video (Jetson or V4L2)
        if self.backend == CAMERA_BACKEND_JETSON:
            self._capture = cv2.VideoCapture(self._resolve_sensor_id())
        return self._capture

    def get_frame_bytes(self) -> bytes:
        cap = self._open_capture()
        if cap is None or not cap.isOpened():
            return b""
        ok, frame = cap.read()
        if not ok or frame is None:
            return b""
        encoded_ok, encoded = cv2.imencode(".jpg", frame)
        if not encoded_ok:
            return b""
        return encoded.tobytes()

    def close(self) -> None:
        if self.backend == CAMERA_BACKEND_RPI5_LIBCAMERA:
            _rpi5_providers.discard(self)
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

