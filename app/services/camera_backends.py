from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

_LEGACY_CSI_VIDEO_RE = re.compile(r"^/dev/video(\d+)$")


class CameraBackend(str, Enum):
    AUTO = "auto"
    PICAMERA2 = "picamera2"
    V4L2 = "v4l2"
    GSTREAMER = "gstreamer"
    JETSON = "jetson"


@dataclass(frozen=True)
class CameraSource:
    backend: CameraBackend
    device: str
    label: str


_NON_CAMERA_DEVICE_NAMES = {"pispbe", "rpi-hevc-dec"}


def parse_camera_backend(raw: str | None) -> CameraBackend:
    value = (raw or CameraBackend.AUTO.value).strip().lower()
    try:
        return CameraBackend(value)
    except ValueError:
        return CameraBackend.AUTO


def parse_csi_camera_index(raw_device: str) -> int | None:
    """
    Map legacy CSI naming to libcamera cam-index.

    On older Pi setups MIPI CSI cameras appeared as /dev/video0 and /dev/video1.
    On Pi 5 libcamera uses cam-index 0/1 instead, but users still configure video0/video1.
    """
    value = str(raw_device or "0").strip()
    if value.isdigit():
        return int(value)

    match = _LEGACY_CSI_VIDEO_RE.match(value)
    if not match:
        return None

    index = int(match.group(1))
    # Legacy dual-CSI convention: /dev/video0 and /dev/video1.
    if index in {0, 1}:
        return index
    return None


def is_legacy_csi_device(raw_device: str) -> bool:
    return parse_csi_camera_index(raw_device) is not None


def build_v4l2_gstreamer_pipeline(device_path: str) -> str:
    return (
        f"v4l2src device={device_path} ! "
        "video/x-raw, width=1280, height=720, framerate=30/1 ! "
        "videoconvert ! video/x-raw, format=BGR ! "
        "appsink drop=1"
    )


def build_libcamera_gstreamer_pipeline(sensor_id: int) -> str:
    return (
        f"libcamerasrc cam-index={sensor_id} ! "
        "video/x-raw, width=1920, height=1080, framerate=30/1 ! "
        "videoconvert ! video/x-raw, format=BGR ! "
        "appsink drop=1"
    )


def build_jetson_gstreamer_pipeline(sensor_id: int) -> str:
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        "video/x-raw(memory:NVMM), width=1280, height=720, framerate=30/1 ! "
        "nvvidconv ! video/x-raw, format=BGRx ! "
        "videoconvert ! video/x-raw, format=BGR ! "
        "appsink drop=1"
    )


def _list_picamera2_sources() -> list[CameraSource]:
    try:
        from picamera2 import Picamera2
    except Exception:
        return []

    try:
        cameras = list(Picamera2.global_camera_info())
    except Exception as exc:
        logger.warning("Picamera2 discovery failed: %s", exc)
        return []

    sources: list[CameraSource] = []
    for index, info in enumerate(cameras):
        model = str(info.get("Model") or info.get("model") or "libcamera")
        sources.extend(
            [
                CameraSource(
                    backend=CameraBackend.PICAMERA2,
                    device=str(index),
                    label=f"picamera2:{index} ({model})",
                ),
                CameraSource(
                    backend=CameraBackend.PICAMERA2,
                    device=f"/dev/video{index}",
                    label=f"csi-legacy:/dev/video{index} -> cam-index {index} ({model})",
                ),
            ]
        )
    return sources


def _parse_v4l2_ctl_devices() -> list[tuple[str, list[str]]]:
    try:
        output = subprocess.check_output(["v4l2-ctl", "--list-devices"], text=True, stderr=subprocess.STDOUT)
    except Exception as exc:
        logger.debug("v4l2-ctl unavailable: %s", exc)
        return []

    blocks: list[tuple[str, list[str]]] = []
    current_name = ""
    current_nodes: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            if current_name and current_nodes:
                blocks.append((current_name, current_nodes))
            current_name = ""
            current_nodes = []
            continue
        if line.startswith("\t"):
            node = line.strip()
            if node.startswith("/dev/video"):
                current_nodes.append(node)
            continue
        if current_name and current_nodes:
            blocks.append((current_name, current_nodes))
            current_nodes = []
        current_name = line.strip().rstrip(":")
    if current_name and current_nodes:
        blocks.append((current_name, current_nodes))
    return blocks


def _list_v4l2_sources() -> list[CameraSource]:
    sources: list[CameraSource] = []
    for name, nodes in _parse_v4l2_ctl_devices():
        if any(token in name.lower() for token in _NON_CAMERA_DEVICE_NAMES):
            continue
        for node in nodes:
            if parse_csi_camera_index(node) is not None:
                continue
            sources.append(
                CameraSource(
                    backend=CameraBackend.V4L2,
                    device=node,
                    label=f"v4l2:{node} ({name})",
                )
            )
    return sources


def discover_camera_sources() -> list[CameraSource]:
    seen: set[tuple[str, str]] = set()
    sources: list[CameraSource] = []

    # Expose expected legacy CSI aliases even before libcamera detects hardware.
    for index in (0, 1):
        alias = CameraSource(
            backend=CameraBackend.PICAMERA2,
            device=f"/dev/video{index}",
            label=f"csi-legacy:/dev/video{index} -> cam-index {index}",
        )
        key = (alias.backend.value, alias.device)
        if key not in seen:
            seen.add(key)
            sources.append(alias)

    for source in [*_list_picamera2_sources(), *_list_v4l2_sources()]:
        key = (source.backend.value, source.device)
        if key in seen:
            continue
        seen.add(key)
        sources.append(source)
    return sources


def resolve_backend_order(
    preferred_backend: CameraBackend,
    *,
    has_custom_pipeline: bool,
    has_libcamera: bool,
    legacy_csi: bool,
) -> list[CameraBackend]:
    if preferred_backend != CameraBackend.AUTO:
        return [preferred_backend]

    order: list[CameraBackend] = []
    if has_custom_pipeline:
        order.append(CameraBackend.GSTREAMER)
    if legacy_csi or has_libcamera:
        order.extend([CameraBackend.PICAMERA2, CameraBackend.GSTREAMER])
    order.extend([CameraBackend.V4L2, CameraBackend.JETSON])
    unique: list[CameraBackend] = []
    for backend in order:
        if backend not in unique:
            unique.append(backend)
    return unique


def normalize_camera_device(raw_device: str) -> tuple[str, int | None]:
    value = str(raw_device or "0").strip()
    csi_index = parse_csi_camera_index(value)
    if csi_index is not None:
        return str(csi_index), csi_index
    if value.startswith("/dev/video"):
        return value, None
    if value.isdigit():
        return value, int(value)
    return "0", 0


def resolve_source_for_device(
    raw_device: str,
    discovered: list[CameraSource] | None = None,
) -> CameraSource:
    device, sensor_id = normalize_camera_device(raw_device)
    discovered = discovered if discovered is not None else discover_camera_sources()

    if sensor_id is not None:
        for source in discovered:
            if source.backend == CameraBackend.PICAMERA2 and source.device in {str(sensor_id), f"/dev/video{sensor_id}"}:
                return source
        return CameraSource(
            backend=CameraBackend.PICAMERA2,
            device=str(sensor_id),
            label=f"csi:/dev/video{sensor_id} -> cam-index {sensor_id}",
        )

    if device.startswith("/dev/video"):
        return CameraSource(
            backend=CameraBackend.V4L2,
            device=device,
            label=f"v4l2:{device}",
        )

    return CameraSource(
        backend=CameraBackend.V4L2,
        device=device,
        label=f"v4l2-index:{device}",
    )


def default_backend_from_env() -> CameraBackend:
    return parse_camera_backend(os.getenv("CRAN_CAMERA_BACKEND"))
