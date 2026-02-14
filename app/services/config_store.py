import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class ConfigStore:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self._lock = Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self._write(self._default_payload())

    def _default_payload(self) -> dict[str, Any]:
        return {
            "meta": {
                "app": "CRAN Calibration Console",
                "version": "1.0.0",
                "updated_at": None,
            },
            "hook_calibration": {
                "marker_size_mm": None,
                "marker_id": None,
                "camera": {
                    "camera_id": 1,
                    "description": "Hook camera",
                },
            },
            "bridge_calibration": {
                "marker_size_mm": None,
                "movement_direction": "unknown",
                "camera": {
                    "camera_id": 0,
                    "description": "Bridge/Trolley camera",
                },
                "marker_positions_m": {"1": 0.0},
                "roi_preview": {},
                "xy_calib_poses": {},
                "roi": {},
                "result": {
                    "crane_x_m": None,
                    "trolley_y_m": None,
                    "known_marker_count": 1,
                    "calibration_quality": 0.0,
                },
            },
            "statistics": {
                "dashboard_url": "http://192.168.0.18:8888/sources/1/dashboards/4",
            },
            "management": {
                "last_command": None,
            },
        }

    def _read(self) -> dict[str, Any]:
        with self.config_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        self._ensure_schema(payload)
        return payload

    def _ensure_schema(self, payload: dict[str, Any]) -> None:
        payload.setdefault("meta", {})
        payload["meta"].setdefault("app", "CRAN Calibration Console")
        payload["meta"].setdefault("version", "1.0.0")
        payload["meta"].setdefault("updated_at", None)

        payload.setdefault("hook_calibration", {})
        hook = payload["hook_calibration"]
        hook.setdefault("marker_size_mm", None)
        hook.setdefault("marker_id", None)
        hook.setdefault("camera", {"camera_id": 1, "description": "Hook camera"})
        hook.pop("result", None)

        payload.setdefault("bridge_calibration", {})
        bridge = payload["bridge_calibration"]
        bridge.setdefault("marker_size_mm", None)
        bridge.setdefault("movement_direction", "unknown")
        bridge.setdefault("camera", {"camera_id": 0, "description": "Bridge/Trolley camera"})
        bridge.setdefault("marker_positions_m", {"1": 0.0})
        bridge.setdefault("roi_preview", {})
        if "xy_calib_poses" not in bridge:
            legacy_1920 = bridge.pop("xy_calib_poses_1920x1080", {})
            legacy_640 = bridge.pop("xy_calib_poses_640x480", {})
            bridge["xy_calib_poses"] = legacy_1920 or legacy_640 or {}
        if "roi" not in bridge:
            legacy_roi_1920 = bridge.pop("roi_for_1920x1080", {})
            legacy_roi_640 = bridge.pop("roi_for_640x480", {})
            bridge["roi"] = legacy_roi_1920 or legacy_roi_640 or {}
        bridge.setdefault(
            "result",
            {"crane_x_m": None, "trolley_y_m": None, "known_marker_count": 1, "calibration_quality": 0.0},
        )

        payload.setdefault("statistics", {})
        payload["statistics"].setdefault("dashboard_url", "http://192.168.0.18:8888/sources/1/dashboards/4")
        payload.setdefault("management", {})
        payload["management"].setdefault("last_command", None)

    def _write(self, payload: dict[str, Any]) -> None:
        with self.config_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    def _mark_updated(self, payload: dict[str, Any]) -> None:
        payload["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()

    def update_hook_settings(self, marker_size: int, marker_id: int) -> None:
        with self._lock:
            payload = self._read()
            payload["hook_calibration"]["marker_size_mm"] = marker_size
            payload["hook_calibration"]["marker_id"] = marker_id
            self._mark_updated(payload)
            self._write(payload)

    def update_bridge_settings(self, marker_size: int) -> None:
        with self._lock:
            payload = self._read()
            payload["bridge_calibration"]["marker_size_mm"] = marker_size
            self._mark_updated(payload)
            self._write(payload)

    def get_hook_settings(self) -> dict[str, Any]:
        payload = self.load()
        hook = payload["hook_calibration"]
        return {
            "marker_size_mm": hook.get("marker_size_mm"),
            "marker_id": hook.get("marker_id"),
            "camera": hook.get("camera", {}),
        }

    def get_bridge_settings(self) -> dict[str, Any]:
        payload = self.load()
        bridge = payload["bridge_calibration"]
        return {
            "marker_size_mm": bridge.get("marker_size_mm"),
            "camera": bridge.get("camera", {}),
            "marker_positions_m": bridge.get("marker_positions_m", {"1": 0.0}),
            "roi_preview": bridge.get("roi_preview", {}),
            "movement_direction": bridge.get("movement_direction", "unknown"),
        }

    def update_bridge_runtime_result(
        self,
        crane_x_m: float,
        trolley_y_m: float,
        known_marker_count: int | None = None,
        calibration_quality: float | None = None,
        marker_positions_m: dict[str, float] | None = None,
        roi_preview: dict[str, Any] | None = None,
        movement_direction: str | None = None,
    ) -> None:
        with self._lock:
            payload = self._read()
            payload["bridge_calibration"]["result"]["crane_x_m"] = crane_x_m
            payload["bridge_calibration"]["result"]["trolley_y_m"] = trolley_y_m
            if known_marker_count is not None:
                payload["bridge_calibration"]["result"]["known_marker_count"] = known_marker_count
            if calibration_quality is not None:
                payload["bridge_calibration"]["result"]["calibration_quality"] = calibration_quality
            if marker_positions_m is not None:
                payload["bridge_calibration"]["marker_positions_m"] = marker_positions_m
            if roi_preview is not None:
                payload["bridge_calibration"]["roi_preview"] = roi_preview
            if movement_direction is not None:
                payload["bridge_calibration"]["movement_direction"] = movement_direction
            self._mark_updated(payload)
            self._write(payload)

    def update_last_command(self, command: str) -> None:
        with self._lock:
            payload = self._read()
            payload["management"]["last_command"] = command
            self._mark_updated(payload)
            self._write(payload)

    def get_calibration_data(self) -> dict[str, Any]:
        payload = self.load()
        bridge = payload["bridge_calibration"]
        return {
            "marker_positions_m": bridge.get("marker_positions_m", {"1": 0.0}),
            "roi_preview": bridge.get("roi_preview", {}),
            "xy_calib_poses": bridge.get("xy_calib_poses", {}),
            "roi": bridge.get("roi", {}),
            "movement_direction": bridge.get("movement_direction", "unknown"),
            "result": bridge.get("result", {}),
        }

    def save_bridge_calibration(self) -> dict[str, Any]:
        with self._lock:
            payload = self._read()
            bridge = payload["bridge_calibration"]
            marker_positions = bridge.get("marker_positions_m", {})
            roi_preview = bridge.get("roi_preview", {})
            if marker_positions:
                bridge["xy_calib_poses"] = {
                    f"aruco_{marker_id}": {"x_m": x_pos} for marker_id, x_pos in marker_positions.items()
                }
            if roi_preview and isinstance(roi_preview, dict):
                padded = roi_preview.get("padded", {})
                if all(k in padded for k in ("x", "y", "w", "h")):
                    bridge["roi"] = {
                        "x": int(padded["x"]),
                        "y": int(padded["y"]),
                        "w": int(padded["w"]),
                        "h": int(padded["h"]),
                    }
            if not bridge.get("xy_calib_poses"):
                bridge["xy_calib_poses"] = {
                    "aruco_12": {"x": 120, "y": 160},
                    "aruco_18": {"x": 470, "y": 155},
                }
            if not bridge.get("roi"):
                bridge["roi"] = {"x": 180, "y": 120, "w": 1620, "h": 820}

            self._mark_updated(payload)
            self._write(payload)
            return self.get_calibration_data()

