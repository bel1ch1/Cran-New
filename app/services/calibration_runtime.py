import asyncio
import json
import os
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
from app.services.external_pose_processes import (
    ensure_pose_supervisor_scripts_running,
    stop_pose_supervisor_scripts,
    wait_pose_children_released,
)

_POSE_SCRIPTS_STATE_LOCK = Lock()
_ACTIVE_CALIBRATION_RUNTIMES = 0
_DEFAULT_TICK_INTERVAL_S = float(os.getenv("CRAN_CALIBRATION_TICK_INTERVAL_S", "0.12"))


def _enter_calibration_mode() -> None:
    global _ACTIVE_CALIBRATION_RUNTIMES
    with _POSE_SCRIPTS_STATE_LOCK:
        if _ACTIVE_CALIBRATION_RUNTIMES == 0:
            stop_pose_supervisor_scripts()
            wait_timeout_s = float(os.getenv("CRAN_POSE_RELEASE_TIMEOUT_S", "5.0"))
            wait_pose_children_released(timeout_s=wait_timeout_s)
        _ACTIVE_CALIBRATION_RUNTIMES += 1


def _leave_calibration_mode() -> None:
    global _ACTIVE_CALIBRATION_RUNTIMES
    with _POSE_SCRIPTS_STATE_LOCK:
        _ACTIVE_CALIBRATION_RUNTIMES = max(0, _ACTIVE_CALIBRATION_RUNTIMES - 1)
        if _ACTIVE_CALIBRATION_RUNTIMES == 0:
            ensure_pose_supervisor_scripts_running()


class FrameSource(Protocol):
    last_error: str | None

    def get_frame_bytes(self) -> bytes:
        """Return latest frame from camera stream."""

    def close(self) -> None:
        """Release camera resources."""

    def reset(self) -> None:
        """Re-open camera after configuration changes."""


class MockCameraFrameProvider:
    last_error: str | None = None

    def get_frame_bytes(self) -> bytes:
        return b""

    def close(self) -> None:
        return

    def reset(self) -> None:
        return


class BaseCalibrationRuntime:
    def __init__(
        self,
        *,
        algorithm,
        camera_provider: FrameSource | None = None,
    ) -> None:
        self.algorithm = algorithm
        self.camera_provider = camera_provider or MockCameraFrameProvider()
        self.is_calibration_running = False
        self.last_frame_bytes: bytes = b""
        self.last_state: dict[str, Any] | None = None
        self._pose_scripts_stopped = False
        self._tick_lock = asyncio.Lock()
        self._last_tick_monotonic = 0.0

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

    def _ensure_calibration_mode(self) -> None:
        if not self._pose_scripts_stopped:
            _enter_calibration_mode()
            self._pose_scripts_stopped = True

    async def _throttle_tick(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        elapsed = now - self._last_tick_monotonic
        if elapsed < _DEFAULT_TICK_INTERVAL_S:
            await asyncio.sleep(_DEFAULT_TICK_INTERVAL_S - elapsed)
        self._last_tick_monotonic = loop.time()

    def detach_stream(self) -> None:
        """End a websocket preview session without closing the shared camera."""
        self.is_calibration_running = False
        self.last_frame_bytes = b""
        if self._pose_scripts_stopped:
            _leave_calibration_mode()
            self._pose_scripts_stopped = False

    def close(self) -> None:
        self.detach_stream()
        self.camera_provider.close()


class BridgeCalibrationRuntime(BaseCalibrationRuntime):
    def __init__(
        self,
        algorithm: BridgeCalibrationAlgorithm | None = None,
        camera_provider: FrameSource | None = None,
    ) -> None:
        super().__init__(
            algorithm=algorithm or MockBridgeCalibrationAlgorithm(),
            camera_provider=camera_provider,
        )

    def _process_tick_sync(
        self,
        marker_size_mm: int | None,
        zero_marker_offset_m: float,
    ) -> dict[str, Any]:
        self._ensure_calibration_mode()
        frame = self.camera_provider.get_frame_bytes()
        result = self.algorithm.process_frame(
            frame or b"",
            calibration_enabled=self.is_calibration_running,
            marker_size_mm=marker_size_mm or 100,
            zero_marker_offset_m=zero_marker_offset_m,
        )
        payload = asdict(result)
        overlay = getattr(self.algorithm, "last_overlay_jpeg", b"")
        if overlay:
            self.last_frame_bytes = overlay
        elif frame:
            self.last_frame_bytes = draw_roi_overlay(frame, payload.get("roi_preview"))
        else:
            self.last_frame_bytes = b""
        payload["is_calibration_running"] = self.is_calibration_running
        self.last_state = payload
        return payload

    async def tick(
        self,
        marker_size_mm: int | None = None,
        zero_marker_offset_m: float = 0.0,
    ) -> dict[str, Any]:
        if self._tick_lock.locked():
            return dict(self.last_state or {})
        async with self._tick_lock:
            await self._throttle_tick()
            return await asyncio.to_thread(
                self._process_tick_sync,
                marker_size_mm,
                zero_marker_offset_m,
            )


class HookCalibrationRuntime(BaseCalibrationRuntime):
    def __init__(
        self,
        algorithm: HookCalibrationAlgorithm | None = None,
        camera_provider: FrameSource | None = None,
    ) -> None:
        super().__init__(
            algorithm=algorithm or MockHookCalibrationAlgorithm(),
            camera_provider=camera_provider,
        )

    def _process_tick_sync(
        self,
        marker_size_mm: int | None,
        marker_id: int | None,
    ) -> dict[str, Any]:
        self._ensure_calibration_mode()
        frame = self.camera_provider.get_frame_bytes()
        result = self.algorithm.process_frame(
            frame or b"",
            marker_size_mm=marker_size_mm or 100,
            target_marker_id=marker_id,
        )
        payload = asdict(result)
        if frame:
            self.last_frame_bytes = draw_roi_overlay(frame, None)
        payload["is_calibration_running"] = self.is_calibration_running
        self.last_state = payload
        return payload

    async def tick(
        self,
        marker_size_mm: int | None = None,
        marker_id: int | None = None,
    ) -> dict[str, Any]:
        if self._tick_lock.locked():
            return dict(self.last_state or {})
        async with self._tick_lock:
            await self._throttle_tick()
            return await asyncio.to_thread(
                self._process_tick_sync,
                marker_size_mm,
                marker_id,
            )
