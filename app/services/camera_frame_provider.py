import logging
from typing import Optional

try:
    import cv2
except Exception:
    cv2 = None

try:
    from picamera2 import Picamera2
except Exception:
    Picamera2 = None

from app.services.camera_backends import (
    CameraBackend,
    CameraSource,
    build_jetson_gstreamer_pipeline,
    build_libcamera_gstreamer_pipeline,
    build_v4l2_gstreamer_pipeline,
    default_backend_from_env,
    discover_camera_sources,
    is_legacy_csi_device,
    normalize_camera_device,
    parse_csi_camera_index,
    resolve_backend_order,
    resolve_source_for_device,
)

logger = logging.getLogger(__name__)


def list_available_cameras() -> list[dict]:
    return [
        {
            "backend": source.backend.value,
            "device": source.device,
            "label": source.label,
        }
        for source in discover_camera_sources()
    ]


class CameraFrameProvider:
    """
    Multi-backend camera provider for Raspberry Pi / V4L2 / Jetson pipelines.

    Backends (env CRAN_CAMERA_BACKEND):
    - auto: picamera2 -> gstreamer(libcamera/v4l2) -> v4l2 -> jetson
    - picamera2, v4l2, gstreamer, jetson
    """

    def __init__(
        self,
        camera_device: str,
        gstreamer_pipeline: Optional[str] = None,
        *,
        backend: CameraBackend | None = None,
    ) -> None:
        self.camera_device = camera_device
        self.gstreamer_pipeline = gstreamer_pipeline
        self.backend = backend or default_backend_from_env()
        self._capture = None
        self._picamera2 = None
        self._active_backend: CameraBackend | None = None
        self._active_source: CameraSource | None = None
        self.last_error: str | None = None
        self._discovered = discover_camera_sources()

    def _resolve_sensor_id(self) -> int:
        device, sensor_id = normalize_camera_device(self.camera_device)
        if sensor_id is not None:
            requested = sensor_id
        elif device.isdigit():
            requested = int(device)
        else:
            requested = 0

        picamera_sources = [s for s in self._discovered if s.backend == CameraBackend.PICAMERA2]
        if not picamera_sources:
            index = parse_csi_camera_index(self.camera_device)
            if index is not None:
                self.last_error = (
                    f"CSI cam-index {index} (/dev/video{index}) не обнаружен libcamera. "
                    "Проверьте MIPI CSI шлейf, включение Camera в raspi-config и перезагрузку."
                )
            else:
                self.last_error = (
                    "CSI/libcamera камеры не обнаружены. "
                    "Проверьте MIPI CSI подключение или укажите CRAN_CAMERA_BACKEND=v4l2."
                )
            return requested

        if requested >= len(picamera_sources):
            logger.warning(
                "Requested camera index %s unavailable (%s libcamera camera(s)), using 0",
                requested,
                len(picamera_sources),
            )
            self.last_error = (
                f"Камера {requested} недоступна, используется камера 0 "
                f"(libcamera: {len(picamera_sources)})"
            )
            return 0
        self.last_error = None
        return requested

    def _pipeline_for_backend(self, backend: CameraBackend) -> str | None:
        if self.gstreamer_pipeline:
            return self.gstreamer_pipeline

        source = resolve_source_for_device(self.camera_device, self._discovered)
        if backend == CameraBackend.GSTREAMER:
            if source.backend == CameraBackend.V4L2 and source.device.startswith("/dev/"):
                return build_v4l2_gstreamer_pipeline(source.device)
            return build_libcamera_gstreamer_pipeline(self._resolve_sensor_id())
        if backend == CameraBackend.JETSON:
            _, sensor_id = normalize_camera_device(self.camera_device)
            return build_jetson_gstreamer_pipeline(sensor_id if sensor_id is not None else 0)
        return None

    def _open_picamera2(self) -> bool:
        if self._picamera2 is not None:
            return True
        if Picamera2 is None:
            self.last_error = "Модуль picamera2 недоступен"
            return False

        sensor_id = self._resolve_sensor_id()
        if not [s for s in self._discovered if s.backend == CameraBackend.PICAMERA2]:
            return False

        try:
            self._picamera2 = Picamera2(sensor_id)
            config = self._picamera2.create_still_configuration(
                main={"size": (1920, 1080), "format": "BGR888"},
                buffer_count=2,
            )
            self._picamera2.configure(config)
            self._picamera2.start()
            self._active_backend = CameraBackend.PICAMERA2
            self._active_source = CameraSource(
                backend=CameraBackend.PICAMERA2,
                device=str(sensor_id),
                label=f"picamera2:{sensor_id}",
            )
            self.last_error = None
            logger.info("Opened Picamera2 sensor %s", sensor_id)
            return True
        except Exception as exc:
            self.last_error = f"Picamera2({sensor_id}) failed: {exc}"
            logger.exception("Failed to open Picamera2(%s)", sensor_id)
            self._picamera2 = None
            return False

    def _open_v4l2(self) -> bool:
        if cv2 is None:
            self.last_error = "OpenCV недоступен"
            return False
        if self._capture is not None and self._capture.isOpened():
            return True

        source = resolve_source_for_device(self.camera_device, self._discovered)
        device_ref = source.device
        if device_ref.startswith("/dev/"):
            cap = cv2.VideoCapture(device_ref, cv2.CAP_V4L2)
        else:
            cap = cv2.VideoCapture(int(device_ref), cv2.CAP_V4L2)

        if cap is not None and cap.isOpened():
            self._capture = cap
            self._active_backend = CameraBackend.V4L2
            self._active_source = source
            self.last_error = None
            logger.info("Opened V4L2 camera %s", device_ref)
            return True

        self.last_error = f"V4L2 open failed for {device_ref}"
        return False

    def _open_gstreamer(self, backend: CameraBackend) -> bool:
        if cv2 is None:
            self.last_error = "OpenCV недоступен"
            return False
        if self._capture is not None and self._capture.isOpened():
            return True

        pipeline = self._pipeline_for_backend(backend)
        if not pipeline:
            self.last_error = "GStreamer pipeline not configured"
            return False

        self._capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if self._capture is not None and self._capture.isOpened():
            self._active_backend = backend
            self._active_source = resolve_source_for_device(self.camera_device, self._discovered)
            self.last_error = None
            logger.info("Opened GStreamer camera via %s", backend.value)
            return True

        self.last_error = f"GStreamer open failed ({backend.value})"
        return False

    def _capture_with_backend(self, backend: CameraBackend) -> bytes:
        if backend == CameraBackend.PICAMERA2:
            if not self._open_picamera2() or self._picamera2 is None:
                return b""
            try:
                frame = self._picamera2.capture_array()
                if frame is None:
                    self.last_error = "Picamera2 returned empty frame"
                    return b""
                encoded_ok, encoded = cv2.imencode(".jpg", frame)
                if not encoded_ok:
                    self.last_error = "Picamera2 JPEG encode failed"
                    return b""
                self.last_error = None
                return encoded.tobytes()
            except Exception as exc:
                self.last_error = f"Picamera2 capture failed: {exc}"
                self._close_picamera2()
                return b""

        if backend == CameraBackend.V4L2:
            if not self._open_v4l2() or self._capture is None:
                return b""
        elif backend in {CameraBackend.GSTREAMER, CameraBackend.JETSON}:
            if not self._open_gstreamer(backend) or self._capture is None:
                return b""
        else:
            return b""

        ok, frame = self._capture.read()
        if not ok or frame is None:
            self.last_error = f"Capture read failed ({backend.value})"
            return b""
        encoded_ok, encoded = cv2.imencode(".jpg", frame)
        if not encoded_ok:
            self.last_error = "JPEG encode failed"
            return b""
        self.last_error = None
        return encoded.tobytes()

    def get_frame_bytes(self) -> bytes:
        if cv2 is None:
            self.last_error = "OpenCV недоступен"
            return b""

        has_libcamera = any(s.backend == CameraBackend.PICAMERA2 for s in self._discovered)
        legacy_csi = is_legacy_csi_device(self.camera_device)
        order = resolve_backend_order(
            self.backend,
            has_custom_pipeline=bool(self.gstreamer_pipeline),
            has_libcamera=has_libcamera,
            legacy_csi=legacy_csi,
        )

        errors: list[str] = []
        for backend in order:
            frame_bytes = self._capture_with_backend(backend)
            if frame_bytes:
                return frame_bytes
            if self.last_error:
                errors.append(f"{backend.value}: {self.last_error}")
            self._reset_capture_handles()

        if errors:
            self.last_error = "; ".join(errors)
        elif not self.last_error:
            self.last_error = "Не удалось получить кадр ни одним backend"
        return b""

    def _reset_capture_handles(self) -> None:
        self._close_picamera2()
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None
        self._active_backend = None
        self._active_source = None

    def _close_picamera2(self) -> None:
        if self._picamera2 is not None:
            try:
                self._picamera2.stop()
                self._picamera2.close()
            except Exception:
                pass
            self._picamera2 = None

    def close(self) -> None:
        self._reset_capture_handles()

    def reset(self) -> None:
        self.close()
        self._discovered = discover_camera_sources()
        self.last_error = None

    @property
    def active_backend(self) -> str | None:
        return self._active_backend.value if self._active_backend else None
