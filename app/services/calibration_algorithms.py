import math
from collections import defaultdict, deque
from dataclasses import dataclass
from statistics import mean, pstdev
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
    ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_50)
    ARUCO_PARAM = cv2.aruco.DetectorParameters()
    DIST_COEFFS = np.array(
        [[-5.77943360e-02, 1.25239405e00, 2.25441807e-03, 4.35415442e-03, -3.44130987e00]],
        dtype=np.float32,
    )
    CAMERA_MATRIX = np.array(
        [
            [661.62411664, 0.0, 345.05463892],
            [0.0, 663.37101748, 215.94757467],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )


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

        if roi_preview and "padded" in roi_preview:
            padded = roi_preview["padded"]
            x = int(padded.get("x", 0))
            y = int(padded.get("y", 0))
            w = int(padded.get("w", 0))
            h = int(padded.get("h", 0))
            if w > 0 and h > 0:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (50, 220, 50), 2)
                cv2.putText(
                    frame,
                    "ROI",
                    (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (50, 220, 50),
                    2,
                    cv2.LINE_AA,
                )

        encoded_ok, encoded = cv2.imencode(".jpg", frame)
        if not encoded_ok:
            return frame_bytes
        return encoded.tobytes()
    except Exception:
        return frame_bytes


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
    Placeholder for OpenCV ArUco bridge algorithm.

    Replace this class with an implementation that:
    1) Detects bridge calibration markers;
    2) Calculates crane X and trolley Y in meters;
    3) Produces values for JSON config persistence.
    """

    def __init__(self) -> None:
        self._x_pose_m = 0.0
        self._known_positions_m: dict[int, float] = {1: 0.0}
        self._candidate_abs_x: dict[int, list[float]] = defaultdict(list)
        self._pair_residuals: list[float] = []
        self._reference_distance_m: float | None = None
        self._roi_raw_bounds: dict[str, int] | None = None
        self._roi_frame_size: dict[str, int] | None = None
        self._last_center_marker_id: int | None = None
        self._direction_score: int = 0
        self._movement_direction: str = "unknown"
        self._zero_marker_offset_m: float = 0.0
        self._orientation_votes: deque[int] = deque(maxlen=40)
        self._axis_orientation_sign: int = 1

    def _apply_zero_marker_offset(self, zero_marker_offset_m: float) -> None:
        target = float(zero_marker_offset_m)
        current = self._known_positions_m.get(0, self._zero_marker_offset_m)
        delta = target - current
        if abs(delta) <= 1e-9:
            self._known_positions_m[0] = target
            self._zero_marker_offset_m = target
            return
        for marker_id in list(self._known_positions_m.keys()):
            self._known_positions_m[marker_id] = round(self._known_positions_m[marker_id] + delta, 6)
        self._zero_marker_offset_m = target

    def _update_movement_direction(self, observations: list[dict]) -> None:
        if not observations:
            return
        center_obs = min(observations, key=lambda obs: abs(obs["x_rel_m"]))
        center_id = int(center_obs["id"])
        if self._last_center_marker_id is not None:
            if center_id > self._last_center_marker_id:
                self._direction_score = min(self._direction_score + 1, 8)
            elif center_id < self._last_center_marker_id:
                self._direction_score = max(self._direction_score - 1, -8)

        self._last_center_marker_id = center_id
        if self._direction_score >= 2:
            self._movement_direction = "left_to_right"
        elif self._direction_score <= -2:
            self._movement_direction = "right_to_left"
        else:
            self._movement_direction = "unknown"

    def _update_axis_orientation(self, observations: list[dict]) -> None:
        if len(observations) < 2:
            return
        known_obs = [obs for obs in observations if obs["id"] in self._known_positions_m]
        if len(known_obs) < 2:
            return

        known_obs.sort(key=lambda item: item["id"])
        for idx in range(len(known_obs) - 1):
            a = known_obs[idx]
            b = known_obs[idx + 1]
            known_delta = self._known_positions_m[b["id"]] - self._known_positions_m[a["id"]]
            observed_delta = b["x_rel_m"] - a["x_rel_m"]
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

    def _monotonic_ok(self, marker_id: int, candidate_x: float) -> bool:
        prev_id = marker_id - 1
        next_id = marker_id + 1
        if prev_id in self._known_positions_m and candidate_x <= self._known_positions_m[prev_id]:
            return False
        if next_id in self._known_positions_m and candidate_x >= self._known_positions_m[next_id]:
            return False
        return True

    def _try_confirm_marker(self, marker_id: int) -> bool:
        values = self._candidate_abs_x.get(marker_id, [])
        if len(values) < 7:
            return False
        sigma = pstdev(values) if len(values) > 1 else 0.0
        candidate_x = mean(values)
        if sigma > 0.08:
            return False
        if not self._monotonic_ok(marker_id, candidate_x):
            return False
        self._known_positions_m[marker_id] = round(candidate_x, 4)
        self._candidate_abs_x[marker_id].clear()
        return True

    def _decode_frame(self, frame_bytes: bytes):
        if not CV2_AVAILABLE:
            return None
        if not frame_bytes:
            return None
        try:
            np_buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
            return cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
        except Exception:
            return None

    def _extract_marker_size_px(self, corner) -> float:
        points = corner[0]
        side_lengths = []
        for idx in range(4):
            p1 = points[idx]
            p2 = points[(idx + 1) % 4]
            side_lengths.append(float(np.linalg.norm(p2 - p1)))
        return mean(side_lengths)

    def _detect_markers(self, frame, marker_size_mm: int) -> tuple[list[dict], dict[str, int] | None, dict[str, int] | None]:
        if not CV2_AVAILABLE or frame is None:
            return [], None, None
        frame_height, frame_width = frame.shape[:2]
        gray_scale_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = cv2.aruco.detectMarkers(
            gray_scale_frame,
            dictionary=ARUCO_DICT,
            parameters=ARUCO_PARAM,
        )
        if ids is None or len(ids) == 0:
            return [], None, {"width": int(frame_width), "height": int(frame_height)}

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
                    cameraMatrix=CAMERA_MATRIX,
                    distCoeffs=DIST_COEFFS,
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
        return sorted(observations, key=lambda item: item["id"]), bounds, frame_size

    def _weight_from_distance(self, distance_m: float) -> float:
        return 1.0 / (0.05 + distance_m * distance_m)

    def _distance_is_acceptable(self, pair_distance_m: float) -> bool:
        if not self._reference_distance_m or pair_distance_m <= 0.0:
            return True
        relative = abs(pair_distance_m - self._reference_distance_m) / self._reference_distance_m
        return relative <= 0.55

    def _update_marker_map_from_observations(
        self,
        observations: list[dict],
        calibration_enabled: bool,
    ) -> tuple[str, dict[str, float]]:
        if not observations:
            return "Маркеры не найдены", {}

        self._update_axis_orientation(observations)
        known_obs = [obs for obs in observations if obs["id"] in self._known_positions_m]
        obs_by_id = {obs["id"]: obs for obs in observations}

        # Capture reference distance from anchor marker id=0.
        anchor_obs = obs_by_id.get(0)
        if anchor_obs is not None:
            self._reference_distance_m = anchor_obs["distance_m"]

        # Estimate camera absolute X from any known marker in frame.
        if known_obs:
            estimates = []
            for obs in known_obs:
                known_x = self._known_positions_m[obs["id"]]
                camera_x = known_x - (self._axis_orientation_sign * obs["x_rel_m"])
                estimates.append(camera_x)
            self._x_pose_m = mean(estimates)

        if not calibration_enabled:
            return "Мониторинг маркеров", {}

        if known_obs:
            used_pairs = 0
            rejected_by_distance = 0
            for known in known_obs:
                known_id = int(known["id"])
                next_id = known_id + 1
                next_obs = obs_by_id.get(next_id)
                if next_obs is None:
                    continue
                pair_distance = (known["distance_m"] + next_obs["distance_m"]) / 2.0
                if not self._distance_is_acceptable(pair_distance):
                    rejected_by_distance += 1
                    continue
                corrected_delta = abs(next_obs["x_rel_m"] - known["x_rel_m"])
                if corrected_delta <= 1e-4:
                    continue
                abs_est = self._known_positions_m[known_id] + corrected_delta
                if self._monotonic_ok(next_id, abs_est):
                    self._candidate_abs_x[next_id].append(float(abs_est))
                    self._candidate_abs_x[next_id] = self._candidate_abs_x[next_id][-30:]
                    used_pairs += 1

            candidate_ids = [marker_id for marker_id in self._candidate_abs_x.keys() if marker_id not in self._known_positions_m]
            confirmed = [marker_id for marker_id in candidate_ids if self._try_confirm_marker(marker_id)]
            if confirmed:
                confirmed_map = {str(mid): self._known_positions_m[mid] for mid in sorted(set(confirmed))}
                markers_text = ", ".join(f"id={mid} -> {x_val:.3f} м" for mid, x_val in confirmed_map.items())
                return f"Откалиброваны маркеры: {markers_text}", confirmed_map
            if used_pairs == 0 and rejected_by_distance > 0:
                if self._reference_distance_m is not None:
                    return (
                        f"Пара отброшена по дистанции. Держите камеру около {self._reference_distance_m:.2f} м",
                        {},
                    )
                return "Пара отброшена по дистанции", {}
            return "Накопление наблюдений для пар id и id+1", {}

        if len(known_obs) >= 2:
            a = known_obs[0]
            b = known_obs[-1]
            known_delta = self._known_positions_m[b["id"]] - self._known_positions_m[a["id"]]
            pair_distance = (a["distance_m"] + b["distance_m"]) / 2.0
            if not self._distance_is_acceptable(pair_distance):
                return "Пара известных маркеров вне рабочего диапазона дистанции", {}
            observed_delta = abs(b["x_rel_m"] - a["x_rel_m"])
            self._pair_residuals.append(abs(observed_delta - known_delta))
            self._pair_residuals = self._pair_residuals[-80:]
            return "Контроль согласованности известной пары", {}

        return "Ожидание кадра с парой известный id и новый id+1", {}

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
        self._update_movement_direction(observations)
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

        if self._pair_residuals:
            avg_residual = mean(self._pair_residuals)
            quality = max(0.0, min(1.0, 1.0 - avg_residual / 0.3))
        else:
            quality = 0.5

        marker_positions = {str(mid): round(pos, 4) for mid, pos in sorted(self._known_positions_m.items())}
        orientation_text = "normal" if self._axis_orientation_sign > 0 else "reversed"
        condition_met = len(visible_ids) >= 2 and marker_size_px > 0
        roi_preview = self._build_roi_preview()
        return BridgeCalibrationResult(
            xy_calib_marker_size_px=marker_size_px,
            xy_calib_new_marker_xpose=int(self._x_pose_m * 100),
            crane_x_m=round(self._x_pose_m, 3),
            trolley_y_m=round(avg_distance, 3),
            calib_message=message,
            condition_met=condition_met,
            marker_positions_m=marker_positions,
            visible_marker_ids=visible_ids,
            known_marker_count=len(self._known_positions_m),
            calibration_quality=round(quality, 3),
            movement_direction=f"{self._movement_direction}; axis={orientation_text}",
            reference_distance_m=round(self._reference_distance_m, 3) if self._reference_distance_m else None,
            last_calibrated_markers_m={k: round(v, 4) for k, v in last_calibrated.items()},
            roi_preview=roi_preview,
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
        if frame is not None:
            observations, marker_bounds, frame_size = self._detect_markers(frame, marker_size_mm=marker_size_mm)
        else:
            observations, marker_bounds, frame_size = [], None, None
        return self._result_from_observations(
            observations=observations,
            marker_size_mm=marker_size_mm,
            calibration_enabled=calibration_enabled,
            marker_bounds=marker_bounds,
            frame_size=frame_size,
        )


class MockHookCalibrationAlgorithm:
    """
    Placeholder for OpenCV ArUco hook algorithm.

    Replace this class with an implementation that:
    1) Detects hook marker;
    2) Calculates distance to marker;
    3) Produces correction offsets for X/Y deviation.
    """

    def process_frame(
        self,
        frame_bytes: bytes,
        marker_size_mm: int,
        target_marker_id: int | None,
    ) -> HookCalibrationResult:
        if not CV2_AVAILABLE or not frame_bytes:
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
            return HookCalibrationResult(
                deviation_x=0.0,
                deviation_y=0.0,
                distance=0.0,
                angle_deg=0.0,
                marker_id=None,
                resolution=f"{width}x{height}",
                calib_message="Маркер крюка не найден",
            )

        ids_list = ids.flatten().tolist()
        target_index = 0
        actual_marker_id = ids_list[0]
        if target_marker_id is not None and target_marker_id in ids_list:
            target_index = ids_list.index(target_marker_id)
            actual_marker_id = target_marker_id
        elif target_marker_id is not None and target_marker_id not in ids_list:
            return HookCalibrationResult(
                deviation_x=0.0,
                deviation_y=0.0,
                distance=0.0,
                angle_deg=0.0,
                marker_id=None,
                resolution=f"{width}x{height}",
                calib_message=f"Целевой маркер id={target_marker_id} не найден",
            )

        corner = corners[target_index]
        marker_length_m = max(0.001, float(marker_size_mm) / 1000.0)
        try:
            _, tvec, _ = cv2.aruco.estimatePoseSingleMarkers(
                corners=corner,
                markerLength=marker_length_m,
                cameraMatrix=CAMERA_MATRIX,
                distCoeffs=DIST_COEFFS,
            )
        except Exception:
            return HookCalibrationResult(
                deviation_x=0.0,
                deviation_y=0.0,
                distance=0.0,
                angle_deg=0.0,
                marker_id=actual_marker_id,
                resolution=f"{width}x{height}",
                calib_message="Не удалось оценить позу маркера",
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

        return HookCalibrationResult(
            deviation_x=round(deviation_x, 2),
            deviation_y=round(deviation_y, 2),
            distance=round(max(0.0, corrected_distance_m), 3),
            angle_deg=round(angle_deg, 2),
            marker_id=int(actual_marker_id),
            resolution=f"{width}x{height}",
            calib_message=f"Маркер id={actual_marker_id} обнаружен",
        )
