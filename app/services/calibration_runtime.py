import asyncio
import json
from dataclasses import asdict
from threading import Lock
from typing import Any, Protocol

from app.services.calibration_algorithms import (
    BridgeCalibrationAlgorithm,
    HookCalibrationAlgorithm,
    MockBridgeCalibrationAlgorithm,
    MockHookCalibrationAlgorithm,
    draw_roi_overlay,
)
from app.services.external_pose_processes import ensure_pose_supervisor_scripts_running, stop_pose_supervisor_scripts

_POSE_SCRIPTS_STATE_LOCK = Lock()
_ACTIVE_CALIBRATION_RUNTIMES = 0


def _enter_calibration_mode() -> None:
    global _ACTIVE_CALIBRATION_RUNTIMES
    with _POSE_SCRIPTS_STATE_LOCK:
        if _ACTIVE_CALIBRATION_RUNTIMES == 0:
            stop_pose_supervisor_scripts()
        _ACTIVE_CALIBRATION_RUNTIMES += 1


def _leave_calibration_mode() -> None:
    global _ACTIVE_CALIBRATION_RUNTIMES
    with _POSE_SCRIPTS_STATE_LOCK:
        _ACTIVE_CALIBRATION_RUNTIMES = max(0, _ACTIVE_CALIBRATION_RUNTIMES - 1)
        if _ACTIVE_CALIBRATION_RUNTIMES == 0:
            ensure_pose_supervisor_scripts_running()

class CameraFrameProvider(Protocol):
    def get_frame_bytes(self) -> bytes:
        """Return latest frame from camera stream."""

    def close(self) -> None:
        """Release camera resources."""


class MockCameraFrameProvider:
    def get_frame_bytes(self) -> bytes:
        return b""

    def close(self) -> None:
        return


class BridgeCalibrationRuntime:
    def __init__(
        self,
        algorithm: BridgeCalibrationAlgorithm | None = None,
        camera_provider: CameraFrameProvider | None = None,
    ) -> None:
        self.algorithm = algorithm or MockBridgeCalibrationAlgorithm()
        self.camera_provider = camera_provider or MockCameraFrameProvider()
        self.is_calibration_running = False
        self.last_frame_bytes: bytes = b""
        self._pose_scripts_stopped = False

    def handle_command(self, raw_text: str) -> None:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return
        if data.get("type") != "start_calibration":
            return
        command = data.get("command")
        if command == "start":
            self.is_calibration_running = True
        elif command == "stop":
            self.is_calibration_running = False

    async def tick(
        self,
        marker_size_mm: int | None = None,
        zero_marker_offset_m: float = 0.0,
    ) -> dict[str, Any]:
        if not self._pose_scripts_stopped:
            _enter_calibration_mode()
            self._pose_scripts_stopped = True
        await asyncio.sleep(0.15)
        frame = self.camera_provider.get_frame_bytes()
        result = self.algorithm.process_frame(
            frame or b"",
            calibration_enabled=self.is_calibration_running,
            marker_size_mm=marker_size_mm or 100,
            zero_marker_offset_m=zero_marker_offset_m,
        )
        payload = asdict(result)
        if frame:
            self.last_frame_bytes = draw_roi_overlay(frame, payload.get("roi_preview"))
        payload["is_calibration_running"] = self.is_calibration_running
        return payload

    def close(self) -> None:
        self.camera_provider.close()
        self.last_frame_bytes = b""
        self.is_calibration_running = False
        if self._pose_scripts_stopped:
            _leave_calibration_mode()
        self._pose_scripts_stopped = False


class HookCalibrationRuntime:
    def __init__(
        self,
        algorithm: HookCalibrationAlgorithm | None = None,
        camera_provider: CameraFrameProvider | None = None,
    ) -> None:
        self.algorithm = algorithm or MockHookCalibrationAlgorithm()
        self.camera_provider = camera_provider or MockCameraFrameProvider()
        self.is_calibration_running = False
        self.last_frame_bytes: bytes = b""
        self._pose_scripts_stopped = False

    def handle_command(self, raw_text: str) -> None:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return
        if data.get("type") != "start_calibration":
            return
        command = data.get("command")
        if command == "start":
            self.is_calibration_running = True
        elif command == "stop":
            self.is_calibration_running = False

    async def tick(
        self,
        marker_size_mm: int | None = None,
        marker_id: int | None = None,
    ) -> dict[str, Any]:
        if not self._pose_scripts_stopped:
            _enter_calibration_mode()
            self._pose_scripts_stopped = True
        await asyncio.sleep(0.15)
        frame = self.camera_provider.get_frame_bytes()
        if frame:
            self.last_frame_bytes = draw_roi_overlay(frame, None)
        result = self.algorithm.process_frame(
            frame or b"",
            marker_size_mm=marker_size_mm or 100,
            target_marker_id=marker_id,
        )
        payload = asdict(result)
        payload["is_calibration_running"] = self.is_calibration_running
        return payload

    def close(self) -> None:
        self.camera_provider.close()
        self.last_frame_bytes = b""
        self.is_calibration_running = False
        if self._pose_scripts_stopped:
            _leave_calibration_mode()
        self._pose_scripts_stopped = False

