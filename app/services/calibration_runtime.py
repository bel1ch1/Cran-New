import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol

from app.services.calibration_algorithms import (
    BridgeCalibrationAlgorithm,
    HookCalibrationAlgorithm,
    MockBridgeCalibrationAlgorithm,
    MockHookCalibrationAlgorithm,
    draw_roi_overlay,
)
from app.services.calibration_session import (
    acquire_calibration_session,
    release_calibration_session,
)
from app.services.camera_config import calibration_tick_interval_s


def _parse_calibration_command(raw_text: str) -> str | None:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if data.get("type") != "start_calibration":
        return None
    command = data.get("command")
    return command if isinstance(command, str) else None


class FrameSource(Protocol):
    last_error: str | None

    def get_frame_bytes(self) -> bytes:
        """Return latest frame from camera stream."""

    def close(self) -> None:
        """Release camera resources."""

    def reset(self) -> None:
        """Re-open camera after configuration changes."""

    def warm_up_until_frame(self, *, is_active: Callable[[], bool]) -> bytes:
        """Try opening the camera until the first frame or timeout."""


class MockCameraFrameProvider:
    last_error: str | None = None

    def get_frame_bytes(self) -> bytes:
        return b""

    def close(self) -> None:
        return

    def reset(self) -> None:
        return

    def warm_up_until_frame(self, *, is_active: Callable[[], bool]) -> bytes:
        return b""


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
        self._stream_attached = False
        self._tick_lock = asyncio.Lock()
        self._last_tick_monotonic = 0.0
        self._stream_active = False

    def reset_calibration_session(self) -> None:
        self.last_state = None
        self.last_frame_bytes = b""

    def start_calibration_session(self) -> None:
        self.reset_calibration_session()
        self.is_calibration_running = True

    def handle_command(self, raw_text: str) -> None:
        command = _parse_calibration_command(raw_text)
        if command == "start":
            self.start_calibration_session()
        elif command == "stop":
            self.is_calibration_running = False

    def _prepare_stream_sync(self) -> None:
        self._stream_active = True
        if not self._stream_attached:
            acquire_calibration_session()
            self._stream_attached = True
        frame = self.camera_provider.warm_up_until_frame(is_active=lambda: self._stream_active)
        if frame:
            self.last_frame_bytes = frame

    async def prepare_stream(self) -> None:
        await asyncio.to_thread(self._prepare_stream_sync)

    def attach_stream(self) -> None:
        """Blocking attach kept for tests; websocket handler uses prepare_stream()."""
        self._prepare_stream_sync()

    def _release_stream_resources(self) -> None:
        self._stream_active = False
        self.is_calibration_running = False
        self.last_frame_bytes = b""
        self.camera_provider.close()
        if self._stream_attached:
            release_calibration_session()
            self._stream_attached = False

    async def finalize_stream(self) -> None:
        """Wait for in-flight frame processing, then release camera and calibration lock."""
        async with self._tick_lock:
            self._release_stream_resources()
            self.reset_calibration_session()

    def detach_stream(self) -> None:
        """End a websocket preview session without waiting for in-flight ticks."""
        self._release_stream_resources()
        self.reset_calibration_session()

    def close(self) -> None:
        self.detach_stream()

    def _apply_frame_overlay(self, frame: bytes, roi_preview: dict | None = None) -> None:
        overlay = getattr(self.algorithm, "last_overlay_jpeg", b"")
        if overlay:
            self.last_frame_bytes = overlay
        elif frame:
            self.last_frame_bytes = draw_roi_overlay(frame, roi_preview)
        else:
            self.last_frame_bytes = b""

    async def _throttle_tick(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        elapsed = now - self._last_tick_monotonic
        tick_interval_s = calibration_tick_interval_s()
        if elapsed < tick_interval_s:
            await asyncio.sleep(tick_interval_s - elapsed)
        self._last_tick_monotonic = loop.time()

    async def _run_tick(self, process_fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        if not self._stream_active:
            return dict(self.last_state or {})
        if self._tick_lock.locked():
            return dict(self.last_state or {})
        async with self._tick_lock:
            await self._throttle_tick()
            return await asyncio.to_thread(process_fn, *args)


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
        self._session_reference_marker_id = 0
        self._session_zero_marker_offset_m = 0.0

    def configure_session(
        self,
        *,
        reference_marker_id: int = 0,
        zero_marker_offset_m: float = 0.0,
    ) -> None:
        self._session_reference_marker_id = int(reference_marker_id)
        self._session_zero_marker_offset_m = float(zero_marker_offset_m)

    def get_session_settings(self) -> dict[str, float | int]:
        return {
            "reference_marker_id": self._session_reference_marker_id,
            "zero_marker_offset_m": self._session_zero_marker_offset_m,
        }

    def reset_calibration_session(self) -> None:
        reset = getattr(self.algorithm, "reset_session", None)
        if callable(reset):
            reset(
                reference_marker_id=self._session_reference_marker_id,
                zero_marker_offset_m=self._session_zero_marker_offset_m,
            )
        super().reset_calibration_session()

    def _process_tick_sync(
        self,
        marker_size_mm: int | None,
        zero_marker_offset_m: float,
    ) -> dict[str, Any]:
        frame = self.camera_provider.get_frame_bytes()
        result = self.algorithm.process_frame(
            frame or b"",
            calibration_enabled=self.is_calibration_running,
            marker_size_mm=marker_size_mm or 100,
            zero_marker_offset_m=zero_marker_offset_m,
        )
        payload = asdict(result)
        self._apply_frame_overlay(frame or b"", payload.get("roi_preview"))
        payload["is_calibration_running"] = self.is_calibration_running
        self.last_state = payload
        return payload

    async def tick(
        self,
        marker_size_mm: int | None = None,
        zero_marker_offset_m: float = 0.0,
    ) -> dict[str, Any]:
        return await self._run_tick(
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
        frame = self.camera_provider.get_frame_bytes()
        result = self.algorithm.process_frame(
            frame or b"",
            marker_size_mm=marker_size_mm or 100,
            target_marker_id=marker_id,
        )
        payload = asdict(result)
        self._apply_frame_overlay(frame or b"")
        payload["is_calibration_running"] = self.is_calibration_running
        self.last_state = payload
        return payload

    async def tick(
        self,
        marker_size_mm: int | None = None,
        marker_id: int | None = None,
    ) -> dict[str, Any]:
        return await self._run_tick(
            self._process_tick_sync,
            marker_size_mm,
            marker_id,
        )
