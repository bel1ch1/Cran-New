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

    def _open_capture(self):
        if cv2 is None:
            return None
        if self._capture is not None and self._capture.isOpened():
            return self._capture
        if self.gstreamer_pipeline:
            self._capture = cv2.VideoCapture(self.gstreamer_pipeline, cv2.CAP_GSTREAMER)
            return self._capture
        source: int | str = self.camera_device
        if isinstance(source, str) and source.isdigit():
            source = int(source)
        if isinstance(source, str) and source.startswith("/dev/video"):
            suffix = source.replace("/dev/video", "")
            if suffix.isdigit():
                source = int(suffix)
        self._capture = cv2.VideoCapture(source)
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

