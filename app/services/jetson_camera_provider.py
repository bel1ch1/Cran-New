from typing import Optional
import io

try:
    import cv2
except Exception:
    cv2 = None

try:
    from picamera2 import Picamera2
except Exception:
    Picamera2 = None


class JetsonCameraFrameProvider:
    """
    Camera frame provider for Raspberry Pi 5 with PiCamera Module v2.1 (IMX219).

    Uses picamera2 library (recommended for Raspberry Pi 5) with fallback to OpenCV.
    Returns JPEG bytes for a single frame.
    """

    def __init__(self, camera_device: str, gstreamer_pipeline: Optional[str] = None) -> None:
        self.camera_device = camera_device
        self.gstreamer_pipeline = gstreamer_pipeline
        self._capture = None
        self._picamera2 = None
        self._use_picamera2 = Picamera2 is not None

    def _resolve_sensor_id(self) -> int:
        source = self.camera_device
        if isinstance(source, str) and source.isdigit():
            return int(source)
        if isinstance(source, str) and source.startswith("/dev/video"):
            suffix = source.replace("/dev/video", "")
            if suffix.isdigit():
                return int(suffix)
        return 0

    def _build_libcamera_pipeline(self, sensor_id: int) -> str:
        """Build GStreamer pipeline using libcamera for Raspberry Pi 5."""
        return (
            f"libcamerasrc camera-name=/base/axi/pcie@120000/rp1/i2c@80000/imx219@10 ! "
            "video/x-raw, width=1920, height=1080, framerate=30/1 ! "
            "videoconvert ! video/x-raw, format=BGR ! "
            "appsink drop=1"
        )

    def _open_picamera2(self):
        """Initialize Picamera2 (recommended for Raspberry Pi 5)."""
        if self._picamera2 is not None:
            return self._picamera2
        
        if Picamera2 is None:
            return None
        
        try:
            sensor_id = self._resolve_sensor_id()
            self._picamera2 = Picamera2(sensor_id)
            
            config = self._picamera2.create_still_configuration(
                main={"size": (1920, 1080), "format": "RGB888"},
                buffer_count=2
            )
            self._picamera2.configure(config)
            self._picamera2.start()
            
            return self._picamera2
        except Exception:
            self._picamera2 = None
            return None

    def _open_capture(self):
        """Fallback to OpenCV with libcamera GStreamer pipeline."""
        if cv2 is None:
            return None
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        sensor_id = self._resolve_sensor_id()
        
        if self.gstreamer_pipeline:
            pipeline = self.gstreamer_pipeline
        else:
            pipeline = self._build_libcamera_pipeline(sensor_id)
        
        self._capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        self._capture = cv2.VideoCapture(sensor_id)
        return self._capture

    def get_frame_bytes(self) -> bytes:
        if self._use_picamera2:
            picam = self._open_picamera2()
            if picam is not None:
                try:
                    frame = picam.capture_array()
                    if frame is not None:
                        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        encoded_ok, encoded = cv2.imencode(".jpg", frame_bgr)
                        if encoded_ok:
                            return encoded.tobytes()
                except Exception:
                    self._use_picamera2 = False
                    self._close_picamera2()
        
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

    def _close_picamera2(self) -> None:
        if self._picamera2 is not None:
            try:
                self._picamera2.stop()
                self._picamera2.close()
            except Exception:
                pass
            self._picamera2 = None

    def close(self) -> None:
        self._close_picamera2()
        
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

