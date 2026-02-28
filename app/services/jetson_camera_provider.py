from typing import Optional

try:
    import cv2
except Exception:
    cv2 = None


class JetsonCameraFrameProvider:
    """
    Integration point for Jetson camera capture.

    Implement `get_frame_bytes` with OpenCV/GStreamer pipeline for Jetson Nano.
    Return JPEG/PNG bytes for a single frame.
    """

    def __init__(self, camera_device: str, gstreamer_pipeline: Optional[str] = None) -> None:
        self.camera_device = camera_device
        self.gstreamer_pipeline = gstreamer_pipeline
        self._capture = None

    def _resolve_sensor_id(self) -> int:
        source = self.camera_device
        if isinstance(source, str) and source.isdigit():
            return int(source)
        if isinstance(source, str) and source.startswith("/dev/video"):
            suffix = source.replace("/dev/video", "")
            if suffix.isdigit():
                return int(suffix)
        return 0

    def _build_default_pipeline(self, sensor_id: int) -> str:
        return (
            f"nvarguscamerasrc sensor-id={sensor_id} ! "
            "video/x-raw(memory:NVMM), width=1920, height=1080, framerate=29/1 ! "
            "nvvidconv ! video/x-raw, format=BGRx ! "
            "videoconvert ! video/x-raw, format=BGR ! "
            "appsink drop=1"
        )

    def _open_capture(self):
        if cv2 is None:
            return None
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        sensor_id = self._resolve_sensor_id()
        pipeline = self.gstreamer_pipeline or self._build_default_pipeline(sensor_id)
        self._capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        # Fallback for development environments without nvargus.
        self._capture = cv2.VideoCapture(sensor_id)
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
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

