from typing import Optional, Set
import logging

try:
    import cv2
except Exception:
    cv2 = None

try:
    from picamera2 import Picamera2
except Exception:
    Picamera2 = None

logger = logging.getLogger(__name__)

# Backend names: "jetson" (nvarguscamerasrc), "rpi5_libcamera" (libcamerasrc for RPi 5 + IMX219/CSI)
CAMERA_BACKEND_JETSON = "jetson"
CAMERA_BACKEND_RPI5_LIBCAMERA = "rpi5_libcamera"
CAMERA_BACKEND_RPI5_PICAMERA2 = "rpi5_picamera2"
CAMERA_BACKEND_RPI5_V4L2 = "rpi5_v4l2"

# RPi 5 + libcamera: в одном процессе нельзя стабильно держать открытыми две камеры.
# Провайдеры с rpi5_libcamera/picamera2 регистрируются здесь; при открытии одной остальные закрываются.
_rpi5_providers: Set["JetsonCameraFrameProvider"] = set()

# Для Picamera2 храним отдельный объект камеры
_picamera2_instances: dict[str, "Picamera2"] = {}


class JetsonCameraFrameProvider:
    """
    Integration point for CSI camera capture via GStreamer or Picamera2.

    Supports:
    - Jetson: nvarguscamerasrc (sensor-id 0/1).
    - Raspberry Pi 5: Multiple backends:
      * rpi5_picamera2 (RECOMMENDED): Native Picamera2 library, best performance
      * rpi5_libcamera: libcamerasrc via GStreamer (requires gstreamer1.0-libcamera)
      * rpi5_v4l2: Direct V4L2 access via /dev/video*
      
      For IMX 219 (Camera Module v2.1) on MIPI CSI.
      Camera selected by index (0, 1) or full path from `rpicam-vid --list-cameras`.
      
      Важно: на RPi 5 в одном процессе одновременно может быть открыта только одна
      камера (ограничение libcamera). Для двух камер задавайте индексы 0 и 1.
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
        self._picamera2 = None
        if self.backend in (CAMERA_BACKEND_RPI5_LIBCAMERA, CAMERA_BACKEND_RPI5_PICAMERA2):
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
        # camera_device can be: "0", "1", or full path from libcamera-hello --list-cameras
        # Example: /base/axi/pcie@120000/rp1/i2c@80000/imx219@10
        if self.camera_device.startswith("/"):
            name_part = f"camera-name={self.camera_device}"
        else:
            # "0" = first camera (omit camera-name); "1" or other = use camera-name
            name_part = "" if self.camera_device == "0" else f"camera-name={self.camera_device}"
        src = ("libcamerasrc " + name_part + " ! ") if name_part else "libcamerasrc ! "
        return (
            f"{src} "
            f"video/x-raw,width={self.width},height={self.height},framerate={self.framerate},format=NV12 ! "
            "queue ! videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
        )
    
    def _build_rpi5_v4l2_pipeline(self) -> str:
        # V4L2 direct access for RPi 5
        # camera_device: "0" -> /dev/video0, "1" -> /dev/video1, or full path "/dev/video0"
        if self.camera_device.startswith("/dev/video"):
            device = self.camera_device
        else:
            device = f"/dev/video{self.camera_device}"
        return (
            f"v4l2src device={device} ! "
            f"video/x-raw,width={self.width},height={self.height},framerate={self.framerate} ! "
            "videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
        )

    def _build_default_pipeline(self) -> str:
        if self.backend == CAMERA_BACKEND_RPI5_LIBCAMERA:
            return self._build_rpi5_libcamera_pipeline()
        elif self.backend == CAMERA_BACKEND_RPI5_V4L2:
            return self._build_rpi5_v4l2_pipeline()
        return self._build_jetson_pipeline(self._resolve_sensor_id())

    def _close_other_rpi5_captures(self) -> None:
        """Закрыть остальные RPi5-провайдеры, чтобы была открыта только одна камера."""
        for other in _rpi5_providers:
            if other is not self:
                if other._capture is not None:
                    try:
                        other._capture.release()
                    except Exception:
                        pass
                    other._capture = None
                if other._picamera2 is not None:
                    try:
                        other._picamera2.stop()
                        other._picamera2.close()
                    except Exception:
                        pass
                    other._picamera2 = None

    def _open_picamera2(self):
        """Открыть камеру через Picamera2 (рекомендуется для RPi 5)."""
        if Picamera2 is None:
            logger.error("Picamera2 not available. Install: pip install picamera2")
            return None
        
        if self._picamera2 is not None:
            return self._picamera2
        
        self._close_other_rpi5_captures()
        
        try:
            # Определяем индекс камеры
            camera_num = 0
            if self.camera_device.isdigit():
                camera_num = int(self.camera_device)
            
            logger.info(f"Opening Picamera2 camera {camera_num}")
            self._picamera2 = Picamera2(camera_num)
            
            # Конфигурация для видео
            config = self._picamera2.create_video_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"},
                controls={"FrameRate": float(self.framerate.split("/")[0]) / float(self.framerate.split("/")[1])}
            )
            self._picamera2.configure(config)
            self._picamera2.start()
            logger.info(f"Picamera2 camera {camera_num} started successfully")
            return self._picamera2
        except Exception as e:
            logger.error(f"Failed to open Picamera2: {e}")
            self._picamera2 = None
            return None

    def _open_capture(self):
        # Picamera2 backend
        if self.backend == CAMERA_BACKEND_RPI5_PICAMERA2:
            return self._open_picamera2()
        
        # OpenCV backends
        if cv2 is None:
            return None
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        if self.backend in (CAMERA_BACKEND_RPI5_LIBCAMERA, CAMERA_BACKEND_RPI5_V4L2):
            self._close_other_rpi5_captures()

        pipeline = self.gstreamer_pipeline or self._build_default_pipeline()
        
        # Для V4L2 и libcamera используем GStreamer
        if self.backend in (CAMERA_BACKEND_RPI5_LIBCAMERA, CAMERA_BACKEND_RPI5_V4L2):
            logger.info(f"Opening camera with pipeline: {pipeline}")
            self._capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        else:
            self._capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        
        if self._capture is not None and self._capture.isOpened():
            logger.info("Camera opened successfully")
            return self._capture

        # Fallback for development: try index /dev/video (Jetson or V4L2)
        if self.backend == CAMERA_BACKEND_JETSON:
            logger.info(f"Trying fallback to camera index {self._resolve_sensor_id()}")
            self._capture = cv2.VideoCapture(self._resolve_sensor_id())
        
        return self._capture

    def get_frame_bytes(self) -> bytes:
        # Picamera2 backend
        if self.backend == CAMERA_BACKEND_RPI5_PICAMERA2:
            cam = self._open_picamera2()
            if cam is None:
                return b""
            try:
                frame = cam.capture_array()
                if frame is None:
                    return b""
                # Picamera2 возвращает RGB, конвертируем в BGR для OpenCV
                if cv2 is not None:
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    encoded_ok, encoded = cv2.imencode(".jpg", frame)
                    if not encoded_ok:
                        return b""
                    return encoded.tobytes()
                return b""
            except Exception as e:
                logger.error(f"Failed to capture frame from Picamera2: {e}")
                return b""
        
        # OpenCV backends
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
        if self.backend in (CAMERA_BACKEND_RPI5_LIBCAMERA, CAMERA_BACKEND_RPI5_PICAMERA2):
            _rpi5_providers.discard(self)
        
        if self._picamera2 is not None:
            try:
                self._picamera2.stop()
                self._picamera2.close()
            except Exception:
                pass
            self._picamera2 = None
        
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

