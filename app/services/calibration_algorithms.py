import math
from collections import deque
from dataclasses import dataclass
from statistics import mean
from typing import Protocol

try:
    import cv2
    import numpy as np

    CV2_AVAILABLE = True
except Exception:
    cv2 = None
    np = None
    CV2_AVAILABLE = False

if CV2_AVAILABLE:
    from app.services.aruco_common import ARUCO_DICT, ARUCO_PARAM
    from app.services.camera_intrinsics import load_intrinsics_for_camera
    from app.services.spatial_marker_map import (
        SpatialMarkerMap,
        collect_camera_x_estimates,
        fuse_camera_x_estimate,
    )

    CAMERA_MATRIX_0, DIST_COEFFS_0 = load_intrinsics_for_camera(camera_id=0)
    CAMERA_MATRIX_1, DIST_COEFFS_1 = load_intrinsics_for_camera(camera_id=1)


def draw_detected_markers(frame_bytes: bytes) -> bytes:
    """
    Draw ArUco marker outlines over a frame before websocket send.
    """
    if not CV2_AVAILABLE or not frame_bytes:
        return frame_bytes
    try:
        np_buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
        if frame is None:
            return frame_bytes
        gray_scale_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = cv2.aruco.detectMarkers(
            gray_scale_frame,
            dictionary=ARUCO_DICT,
            parameters=ARUCO_PARAM,
        )
        if ids is not None and len(ids) > 0:
            cv2.aruco.drawDetectedMarkers(frame, corners, ids)
        encoded_ok, encoded = cv2.imencode(".jpg", frame)
        if not encoded_ok:
            return frame_bytes
        return encoded.tobytes()
    except Exception:
        return frame_bytes


def draw_roi_overlay(frame_bytes: bytes, roi_preview: dict | None) -> bytes:
    if not CV2_AVAILABLE or not frame_bytes:
        return frame_bytes
    frame = _decode_jpeg_frame(frame_bytes)
    if frame is None:
        return frame_bytes
    return render_frame_overlay(frame, roi_preview, corners=None, ids=None) or frame_bytes


def _decode_jpeg_frame(frame_bytes: bytes):
    if not CV2_AVAILABLE or not frame_bytes:
        return None
    try:
        np_buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
        return cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _encode_jpeg_frame(frame, quality: int = 75) -> bytes:
    if frame is None:
        return b""
    encoded_ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not encoded_ok:
        return b""
    return encoded.tobytes()


def render_frame_overlay(frame, roi_preview: dict | None, corners, ids) -> bytes:
    """Draw detected markers and ROI on a BGR frame without re-running detection."""
    if not CV2_AVAILABLE or frame is None:
        return b""
    try:
        annotated = frame.copy()
        if ids is not None and len(ids) > 0 and corners is not None:
            cv2.aruco.drawDetectedMarkers(annotated, corners, ids)

        if roi_preview and "padded" in roi_preview:
            padded = roi_preview["padded"]
            x = int(padded.get("x", 0))
            y = int(padded.get("y", 0))
            w = int(padded.get("w", 0))
            h = int(padded.get("h", 0))
            if w > 0 and h > 0:
                cv2.rectangle(annotated, (x, y), (x + w, y + h), (50, 220, 50), 2)
                cv2.putText(
                    annotated,
                    "ROI",
                    (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (50, 220, 50),
                    2,
                    cv2.LINE_AA,
                )
        return _encode_jpeg_frame(annotated)
    except Exception:
        return b""


def target_marker_overlay(corners, ids, target_index: int | None):
    """Return corners/ids arrays containing only the selected marker for preview."""
    if not CV2_AVAILABLE or np is None:
        return None, None
    if corners is None or ids is None or target_index is None:
        return None, None
    if target_index < 0 or target_index >= len(corners):
        return None, None
    marker_id = int(ids.flatten()[target_index])
    return [corners[target_index]], np.array([[marker_id]], dtype=ids.dtype)


@dataclass
class BridgeCalibrationResult:
    xy_calib_marker_size_px: int
    xy_calib_new_marker_xpose: int
    crane_x_m: float
    trolley_y_m: float
    calib_message: str
    condition_met: bool
    marker_positions_m: dict[str, float]
    visible_marker_ids: list[int]
    known_marker_count: int
    calibration_quality: float
    movement_direction: str
    reference_distance_m: float | None
    last_calibrated_markers_m: dict[str, float]
    roi_preview: dict[str, dict[str, int]] | None
    landmark_trust: dict[str, float]


@dataclass
class HookCalibrationResult:
    deviation_x: float
    deviation_y: float
    distance: float
    angle_deg: float
    marker_id: int | None
    resolution: str
    calib_message: str


class BridgeCalibrationAlgorithm(Protocol):
    def process_frame(
        self,
        frame_bytes: bytes,
        calibration_enabled: bool,
        marker_size_mm: int,
        zero_marker_offset_m: float = 0.0,
    ) -> BridgeCalibrationResult:
        """Calculate bridge/trolley calibration values from a camera frame."""


class HookCalibrationAlgorithm(Protocol):
    def process_frame(
        self,
        frame_bytes: bytes,
        marker_size_mm: int,
        target_marker_id: int | None,
    ) -> HookCalibrationResult:
        """Calculate hook calibration values from a camera frame."""


class MockBridgeCalibrationAlgorithm:
    """
    Bridge XY calibration using spatial landmark map (position-based trust).

    Marker ArUco IDs do not need to be sequential along the path; new landmarks
    are confirmed by repeated observations at the same X coordinate.
    """

    def __init__(self) -> None:
        self._spatial_map = SpatialMarkerMap.create_for_calibration_session(
            reference_marker_id=0,
            zero_offset_m=0.0,
        )
        self._reference_marker_id: int = 0
        self._zero_marker_offset_m: float = 0.0
        self._x_pose_m = 0.0
        self._prev_x_pose_m: float | None = None
        self._reference_distance_m: float | None = None
        self._roi_raw_bounds: dict[str, int] | None = None
        self._roi_frame_size: dict[str, int] | None = None
        self._direction_score: int = 0
        self._movement_direction: str = "unknown"
        self._orientation_votes: deque[int] = deque(maxlen=40)
        self._axis_orientation_sign: int = 1
        self._consistency_residuals: list[float] = []
        self.last_overlay_jpeg: bytes = b""

    def reset_session(
        self,
        *,
        reference_marker_id: int = 0,
        zero_marker_offset_m: float = 0.0,
    ) -> None:
        """Clear in-memory calibration progress for a fresh session."""
        self._reference_marker_id = int(reference_marker_id)
        self._zero_marker_offset_m = float(zero_marker_offset_m)
        self._spatial_map = SpatialMarkerMap.create_for_calibration_session(
            reference_marker_id=self._reference_marker_id,
            zero_offset_m=self._zero_marker_offset_m,
        )
        self._x_pose_m = 0.0
        self._prev_x_pose_m = None
        self._reference_distance_m = None
        self._roi_raw_bounds = None
        self._roi_frame_size = None
        self._direction_score = 0
        self._movement_direction = "unknown"
        self._orientation_votes = deque(maxlen=40)
        self._axis_orientation_sign = 1
        self._consistency_residuals = []
        self.last_overlay_jpeg = b""

    def _apply_zero_marker_offset(self, zero_marker_offset_m: float) -> None:
        target = float(zero_marker_offset_m)
        self._spatial_map.apply_zero_offset(target)
        self._zero_marker_offset_m = target

    def _update_movement_direction(self) -> None:
        prev_x = self._prev_x_pose_m
        current_x = self._x_pose_m
        self._prev_x_pose_m = current_x
        if prev_x is None:
            return
        delta = current_x - prev_x
        if delta >= 0.002:
            self._direction_score = min(self._direction_score + 1, 8)
        elif delta <= -0.002:
            self._direction_score = max(self._direction_score - 1, -8)
        if self._direction_score >= 2:
            self._movement_direction = "left_to_right"
        elif self._direction_score <= -2:
            self._movement_direction = "right_to_left"
        else:
            self._movement_direction = "unknown"

    def _update_axis_orientation(self, observations: list[dict], camera_x_hint: float | None) -> None:
        if len(observations) < 2 or camera_x_hint is None:
            return

        matched: list[tuple[float, float]] = []
        for obs in observations:
            rel_x_m = float(obs["x_rel_m"])
            landmark_x = self._spatial_map.match_landmark_for_detection(
                rel_x_m,
                camera_x_hint,
                self._axis_orientation_sign,
            )
            if landmark_x is not None:
                matched.append((landmark_x, rel_x_m))

        if len(matched) < 2:
            return

        matched.sort(key=lambda item: item[0])
        for idx in range(len(matched) - 1):
            known_delta = matched[idx + 1][0] - matched[idx][0]
            observed_delta = matched[idx + 1][1] - matched[idx][1]
            if abs(known_delta) < 1e-6 or abs(observed_delta) < 1e-6:
                continue
            orientation_vote = 1 if (known_delta * observed_delta) > 0 else -1
            self._orientation_votes.append(orientation_vote)

        if not self._orientation_votes:
            return
        score = sum(self._orientation_votes)
        if score >= 2:
            self._axis_orientation_sign = 1
        elif score <= -2:
            self._axis_orientation_sign = -1

    def _decode_frame(self, frame_bytes: bytes):
        return _decode_jpeg_frame(frame_bytes)

    def _extract_marker_size_px(self, corner) -> float:
        points = corner[0]
        side_lengths = []
        for idx in range(4):
            p1 = points[idx]
            p2 = points[(idx + 1) % 4]
            side_lengths.append(float(np.linalg.norm(p2 - p1)))
        return mean(side_lengths)

    def _detect_markers(self, frame, marker_size_mm: int):
        if not CV2_AVAILABLE or frame is None:
            return [], None, None, None, None
        frame_height, frame_width = frame.shape[:2]
        gray_scale_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = cv2.aruco.detectMarkers(
            gray_scale_frame,
            dictionary=ARUCO_DICT,
            parameters=ARUCO_PARAM,
        )
        if ids is None or len(ids) == 0:
            return [], None, {"width": int(frame_width), "height": int(frame_height)}, None, None

        marker_length_m = max(0.001, float(marker_size_mm) / 1000.0)
        observations: list[dict] = []
        min_x = frame_width
        min_y = frame_height
        max_x = 0
        max_y = 0
        for idx, marker_id_raw in enumerate(ids.flatten().tolist()):
            target_index = idx
            corner = corners[target_index]
            try:
                _, tvec, _ = cv2.aruco.estimatePoseSingleMarkers(
                    corners=corner,
                    markerLength=marker_length_m,
                    cameraMatrix=CAMERA_MATRIX_0,
                    distCoeffs=DIST_COEFFS_0,
                )
            except Exception:
                continue

            tvec_xyz = tvec[0][0]
            marker_points = corner[0]
            rel_x_m = float(tvec_xyz[0])
            distance_m = float(math.sqrt(float(tvec_xyz[0] ** 2 + tvec_xyz[1] ** 2 + tvec_xyz[2] ** 2)))
            marker_size_px = self._extract_marker_size_px(corner)
            min_x = min(min_x, int(np.min(marker_points[:, 0])))
            min_y = min(min_y, int(np.min(marker_points[:, 1])))
            max_x = max(max_x, int(np.max(marker_points[:, 0])))
            max_y = max(max_y, int(np.max(marker_points[:, 1])))
            observations.append(
                {
                    "id": int(marker_id_raw),
                    "x_rel_m": rel_x_m,
                    "distance_m": distance_m,
                    "marker_size_px": marker_size_px,
                }
            )

        bounds = {"min_x": int(min_x), "min_y": int(min_y), "max_x": int(max_x), "max_y": int(max_y)}
        frame_size = {"width": int(frame_width), "height": int(frame_height)}
        return sorted(observations, key=lambda item: item["id"]), bounds, frame_size, corners, ids

    def _prune_stale_candidates(self, visible_ids: list[int]) -> None:
        return

    def _estimate_camera_x(self, observations: list[dict]) -> float | None:
        weighted = collect_camera_x_estimates(
            observations,
            self._spatial_map,
            axis_sign=self._axis_orientation_sign,
            hint_x_m=self._x_pose_m if self._x_pose_m > 0.0 or self._prev_x_pose_m is not None else None,
        )
        return fuse_camera_x_estimate(weighted)

    def _update_marker_map_from_observations(
        self,
        observations: list[dict],
        calibration_enabled: bool,
    ) -> tuple[str, dict[str, float]]:
        if not observations:
            return "Маркеры не найдены", {}

        obs_by_id = {obs["id"]: obs for obs in observations}
        anchor_obs = obs_by_id.get(self._reference_marker_id)
        if anchor_obs is not None:
            self._reference_distance_m = anchor_obs["distance_m"]

        camera_x = self._estimate_camera_x(observations)
        if camera_x is not None:
            values = [value for value, _ in collect_camera_x_estimates(
                observations,
                self._spatial_map,
                axis_sign=self._axis_orientation_sign,
                hint_x_m=camera_x,
            )]
            if len(values) >= 2:
                spread = max(values) - min(values)
                self._consistency_residuals.append(spread)
                self._consistency_residuals = self._consistency_residuals[-80:]
            self._x_pose_m = camera_x

        self._update_axis_orientation(observations, camera_x)

        if not calibration_enabled:
            return "Мониторинг маркеров", {}

        message, newly_confirmed = self._spatial_map.ingest_observations(
            observations,
            camera_x,
            calibration_enabled=True,
            axis_sign=self._axis_orientation_sign,
        )
        if newly_confirmed:
            slots = ", ".join(f"точка {slot}={x_val:.3f} м" for slot, x_val in sorted(newly_confirmed.items(), key=lambda item: int(item[0])))
            return f"Подтверждена точка: {slots}", newly_confirmed
        if camera_x is None:
            return "Ожидание опорного маркера", {}
        return message, {}

    def _update_roi_bounds(self, marker_bounds: dict[str, int] | None, frame_size: dict[str, int] | None, calibration_enabled: bool) -> None:
        if not calibration_enabled or not marker_bounds or not frame_size:
            return
        self._roi_frame_size = frame_size
        if self._roi_raw_bounds is None:
            self._roi_raw_bounds = marker_bounds.copy()
            return
        self._roi_raw_bounds["min_x"] = min(self._roi_raw_bounds["min_x"], marker_bounds["min_x"])
        self._roi_raw_bounds["min_y"] = min(self._roi_raw_bounds["min_y"], marker_bounds["min_y"])
        self._roi_raw_bounds["max_x"] = max(self._roi_raw_bounds["max_x"], marker_bounds["max_x"])
        self._roi_raw_bounds["max_y"] = max(self._roi_raw_bounds["max_y"], marker_bounds["max_y"])

    def _build_roi_preview(self) -> dict[str, dict[str, int]] | None:
        if not self._roi_raw_bounds or not self._roi_frame_size:
            return None
        width = self._roi_frame_size["width"]
        height = self._roi_frame_size["height"]
        raw = self._roi_raw_bounds
        margin_x = max(12, int((raw["max_x"] - raw["min_x"]) * 0.1))
        margin_y = max(12, int((raw["max_y"] - raw["min_y"]) * 0.1))

        padded_x0 = max(0, raw["min_x"] - margin_x)
        padded_y0 = max(0, raw["min_y"] - margin_y)
        padded_x1 = min(width - 1, raw["max_x"] + margin_x)
        padded_y1 = min(height - 1, raw["max_y"] + margin_y)
        return {
            "raw": {
                "x": int(raw["min_x"]),
                "y": int(raw["min_y"]),
                "w": int(max(1, raw["max_x"] - raw["min_x"])),
                "h": int(max(1, raw["max_y"] - raw["min_y"])),
            },
            "padded": {
                "x": int(padded_x0),
                "y": int(padded_y0),
                "w": int(max(1, padded_x1 - padded_x0)),
                "h": int(max(1, padded_y1 - padded_y0)),
            },
            "frame": {"width": int(width), "height": int(height)},
        }

    def _result_from_observations(
        self,
        observations: list[dict],
        marker_size_mm: int,
        calibration_enabled: bool,
        marker_bounds: dict[str, int] | None,
        frame_size: dict[str, int] | None,
    ) -> BridgeCalibrationResult:
        self._update_movement_direction()
        self._update_roi_bounds(marker_bounds, frame_size, calibration_enabled)
        if observations:
            avg_distance = mean(obs["distance_m"] for obs in observations)
            marker_size_px = int(round(mean(obs["marker_size_px"] for obs in observations)))
            visible_ids = sorted(obs["id"] for obs in observations)
            message, last_calibrated = self._update_marker_map_from_observations(observations, calibration_enabled)
        else:
            visible_ids = []
            avg_distance = 0.0
            marker_size_px = 0
            message = "Маркеры не найдены"
            last_calibrated = {}

        if self._consistency_residuals:
            avg_residual = mean(self._consistency_residuals)
            quality = max(0.0, min(1.0, 1.0 - avg_residual / 0.08))
        else:
            quality = 0.5

        marker_positions = self._spatial_map.to_marker_positions_m()
        landmark_trust = self._spatial_map.landmark_trust()
        orientation_text = "normal" if self._axis_orientation_sign > 0 else "reversed"
        condition_met = len(visible_ids) >= 1 and marker_size_px > 0
        roi_preview = self._build_roi_preview()
        self._prune_stale_candidates(visible_ids)
        return BridgeCalibrationResult(
            xy_calib_marker_size_px=marker_size_px,
            xy_calib_new_marker_xpose=int(self._x_pose_m * 100),
            crane_x_m=round(self._x_pose_m, 3),
            trolley_y_m=round(avg_distance, 3),
            calib_message=message,
            condition_met=condition_met,
            marker_positions_m=marker_positions,
            visible_marker_ids=visible_ids,
            known_marker_count=self._spatial_map.known_count,
            calibration_quality=round(quality, 3),
            movement_direction=f"{self._movement_direction}; axis={orientation_text}",
            reference_distance_m=round(self._reference_distance_m, 3) if self._reference_distance_m else None,
            last_calibrated_markers_m={k: round(v, 4) for k, v in last_calibrated.items()},
            roi_preview=roi_preview,
            landmark_trust=landmark_trust,
        )

    def process_frame(
        self,
        frame_bytes: bytes,
        calibration_enabled: bool,
        marker_size_mm: int,
        zero_marker_offset_m: float = 0.0,
    ) -> BridgeCalibrationResult:
        self._apply_zero_marker_offset(zero_marker_offset_m)
        frame = self._decode_frame(frame_bytes)
        corners = None
        ids = None
        if frame is not None:
            observations, marker_bounds, frame_size, corners, ids = self._detect_markers(
                frame,
                marker_size_mm=marker_size_mm,
            )
        else:
            observations, marker_bounds, frame_size = [], None, None
        result = self._result_from_observations(
            observations=observations,
            marker_size_mm=marker_size_mm,
            calibration_enabled=calibration_enabled,
            marker_bounds=marker_bounds,
            frame_size=frame_size,
        )
        if frame is not None:
            self.last_overlay_jpeg = render_frame_overlay(frame, result.roi_preview, corners, ids)
        else:
            self.last_overlay_jpeg = b""
        return result


class MockHookCalibrationAlgorithm:
    """
    Placeholder for OpenCV ArUco hook algorithm.

    Replace this class with an implementation that:
    1) Detects hook marker;
    2) Calculates distance to marker;
    3) Produces correction offsets for X/Y deviation.
    """

    def __init__(self) -> None:
        self.last_overlay_jpeg: bytes = b""

    def _with_overlay(
        self,
        frame,
        corners,
        ids,
        target_index: int | None,
        result: HookCalibrationResult,
    ) -> HookCalibrationResult:
        if frame is not None:
            overlay_corners, overlay_ids = target_marker_overlay(corners, ids, target_index)
            self.last_overlay_jpeg = render_frame_overlay(frame, None, overlay_corners, overlay_ids)
        else:
            self.last_overlay_jpeg = b""
        return result

    def process_frame(
        self,
        frame_bytes: bytes,
        marker_size_mm: int,
        target_marker_id: int | None,
    ) -> HookCalibrationResult:
        if not CV2_AVAILABLE or not frame_bytes:
            self.last_overlay_jpeg = b""
            return HookCalibrationResult(
                deviation_x=0.0,
                deviation_y=0.0,
                distance=0.0,
                angle_deg=0.0,
                marker_id=None,
                resolution="---x---",
                calib_message="Ожидание кадра камеры",
            )

        try:
            np_buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
            frame = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
        except Exception:
            frame = None
        if frame is None:
            self.last_overlay_jpeg = b""
            return HookCalibrationResult(
                deviation_x=0.0,
                deviation_y=0.0,
                distance=0.0,
                angle_deg=0.0,
                marker_id=None,
                resolution="---x---",
                calib_message="Не удалось декодировать кадр",
            )

        height, width = frame.shape[:2]
        gray_scale_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = cv2.aruco.detectMarkers(
            gray_scale_frame,
            dictionary=ARUCO_DICT,
            parameters=ARUCO_PARAM,
        )
        if ids is None or len(ids) == 0:
            return self._with_overlay(
                frame,
                None,
                None,
                None,
                HookCalibrationResult(
                    deviation_x=0.0,
                    deviation_y=0.0,
                    distance=0.0,
                    angle_deg=0.0,
                    marker_id=None,
                    resolution=f"{width}x{height}",
                    calib_message="Маркер крюка не найден",
                ),
            )

        ids_list = ids.flatten().tolist()
        target_index = 0
        actual_marker_id = ids_list[0]
        if target_marker_id is not None and target_marker_id in ids_list:
            target_index = ids_list.index(target_marker_id)
            actual_marker_id = target_marker_id
        elif target_marker_id is not None and target_marker_id not in ids_list:
            return self._with_overlay(
                frame,
                corners,
                ids,
                None,
                HookCalibrationResult(
                    deviation_x=0.0,
                    deviation_y=0.0,
                    distance=0.0,
                    angle_deg=0.0,
                    marker_id=None,
                    resolution=f"{width}x{height}",
                    calib_message=f"Целевой маркер id={target_marker_id} не найден",
                ),
            )

        corner = corners[target_index]
        marker_length_m = max(0.001, float(marker_size_mm) / 1000.0)
        try:
            _, tvec, _ = cv2.aruco.estimatePoseSingleMarkers(
                corners=corner,
                markerLength=marker_length_m,
                cameraMatrix=CAMERA_MATRIX_1,
                distCoeffs=DIST_COEFFS_1,
            )
        except Exception:
            return self._with_overlay(
                frame,
                corners,
                ids,
                target_index,
                HookCalibrationResult(
                    deviation_x=0.0,
                    deviation_y=0.0,
                    distance=0.0,
                    angle_deg=0.0,
                    marker_id=actual_marker_id,
                    resolution=f"{width}x{height}",
                    calib_message="Не удалось оценить позу маркера",
                ),
            )

        tvec_xyz = tvec[0][0]
        x_m = float(tvec_xyz[0])
        y_m = float(tvec_xyz[1])
        z_m = float(tvec_xyz[2])
        lateral_m = math.sqrt(x_m * x_m + y_m * y_m)
        angle_rad = math.atan2(lateral_m, max(1e-6, z_m))
        angle_deg = math.degrees(angle_rad)
        corrected_distance_m = z_m / max(1e-6, math.cos(angle_rad))

        points = corner[0]
        marker_center_x_px = float(np.mean(points[:, 0]))
        marker_center_y_px = float(np.mean(points[:, 1]))
        deviation_x = marker_center_x_px - (width / 2.0)
        deviation_y = marker_center_y_px - (height / 2.0)

        return self._with_overlay(
            frame,
            corners,
            ids,
            target_index,
            HookCalibrationResult(
                deviation_x=round(deviation_x, 2),
                deviation_y=round(deviation_y, 2),
                distance=round(max(0.0, corrected_distance_m), 3),
                angle_deg=round(angle_deg, 2),
                marker_id=int(actual_marker_id),
                resolution=f"{width}x{height}",
                calib_message=f"Маркер id={actual_marker_id} обнаружен",
            ),
        )
