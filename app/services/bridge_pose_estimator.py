"""Bridge XY pose estimation: spatial landmark matching + robust temporal fusion."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from statistics import median

import cv2
import numpy as np

from app.services.camera_config import (
    pose_debug,
    pose_hold_last_valid,
    pose_max_step_m,
    pose_outlier_m,
    pose_smooth_alpha,
    pose_use_solvepnp,
    pose_use_subpix,
    pose_window_size,
    spatial_runtime_match_tolerance_m,
)
from app.services.pose_runtime_common import detect_markers
from app.services.spatial_marker_map import (
    SpatialMarkerMap,
    collect_camera_x_estimates,
    fuse_camera_x_estimate,
)

_SOLVEPNP_IPPE_SQUARE = getattr(cv2, "SOLVEPNP_IPPE_SQUARE", None)
_SUBPIX_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)


@dataclass(frozen=True)
class Roi:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class BridgePoseEstimatorConfig:
    marker_size_mm: int
    spatial_map: SpatialMarkerMap
    axis_sign: int
    reference_marker_id: int
    zero_offset_m: float
    roi: Roi
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    match_tolerance_m: float = field(default_factory=spatial_runtime_match_tolerance_m)


@dataclass
class MarkerDetection:
    marker_id: int
    rel_x_m: float
    distance_m: float
    marker_offset_px: float
    landmark_x_m: float | None = None


@dataclass
class BridgePoseResult:
    camera_x_m: float
    distance_m: float
    marker_id: int
    marker_offset_px: float
    valid: bool
    debug_estimates: list[float] = field(default_factory=list)
    debug_spread_m: float | None = None


@dataclass
class BridgePoseFilterState:
    last_camera_x_m: float | None = None
    smoothed_camera_x_m: float | None = None
    smoothed_distance_m: float | None = None
    raw_window: deque[float] = field(default_factory=deque)
    last_valid: BridgePoseResult | None = None

    def reset(self) -> None:
        self.last_camera_x_m = None
        self.smoothed_camera_x_m = None
        self.smoothed_distance_m = None
        self.raw_window.clear()
        self.last_valid = None


def _held_result(
    state: BridgePoseFilterState,
    *,
    debug_estimates: list[float] | None = None,
    debug_spread_m: float | None = None,
) -> BridgePoseResult | None:
    if not pose_hold_last_valid() or state.last_valid is None:
        return None
    last = state.last_valid
    return BridgePoseResult(
        camera_x_m=last.camera_x_m,
        distance_m=last.distance_m,
        marker_id=last.marker_id,
        marker_offset_px=last.marker_offset_px,
        valid=True,
        debug_estimates=list(debug_estimates or []),
        debug_spread_m=debug_spread_m,
    )


def _invalid_pose() -> BridgePoseResult:
    return BridgePoseResult(0.0, 0.0, -1, 0.0, False)


def _refine_corners(gray_frame: np.ndarray, corners) -> None:
    if not pose_use_subpix() or corners is None or len(corners) == 0:
        return
    try:
        for idx, corner in enumerate(corners):
            points = np.asarray(corner, dtype=np.float32).reshape(-1, 1, 2)
            if points.shape[0] < 4:
                continue
            cv2.cornerSubPix(gray_frame, points, (5, 5), (-1, -1), _SUBPIX_CRITERIA)
            corners[idx] = points.reshape(corner.shape)
    except Exception:
        return


def _marker_object_points(marker_length_m: float) -> np.ndarray:
    half = marker_length_m / 2.0
    return np.array(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float32,
    )


def _estimate_marker_pose(
    corner,
    *,
    marker_length_m: float,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> tuple[np.ndarray, float] | None:
    image_points = corner.reshape(-1, 2).astype(np.float32)
    if pose_use_solvepnp() and _SOLVEPNP_IPPE_SQUARE is not None:
        try:
            ok, _rvec, tvec = cv2.solvePnP(
                _marker_object_points(marker_length_m),
                image_points,
                camera_matrix,
                dist_coeffs,
                flags=_SOLVEPNP_IPPE_SQUARE,
            )
            if ok:
                xyz = tvec.reshape(-1)
                return xyz, float(math.sqrt(float(np.dot(xyz, xyz))))
        except Exception:
            pass

    try:
        _rvecs, tvecs, _ = cv2.aruco.estimatePoseSingleMarkers(
            corners=corner,
            markerLength=marker_length_m,
            cameraMatrix=camera_matrix,
            distCoeffs=dist_coeffs,
        )
    except Exception:
        return None

    xyz = tvecs[0][0]
    return xyz, float(math.sqrt(float(np.dot(xyz, xyz))))


def detect_markers_in_roi(
    frame_bgr: np.ndarray,
    cfg: BridgePoseEstimatorConfig,
) -> list[MarkerDetection]:
    frame_h, frame_w = frame_bgr.shape[:2]
    roi_x = min(max(cfg.roi.x, 0), frame_w - 1)
    roi_y = min(max(cfg.roi.y, 0), frame_h - 1)
    roi_w = min(cfg.roi.w, frame_w - roi_x)
    roi_h = min(cfg.roi.h, frame_h - roi_y)

    roi_frame = frame_bgr[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
    if roi_frame.size == 0:
        return []

    gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detect_markers(gray)
    if ids is None or len(ids) == 0:
        return []

    _refine_corners(gray, corners)

    frame_center_x_px = frame_w / 2.0
    marker_length_m = max(0.001, float(cfg.marker_size_mm) / 1000.0)
    detections: list[MarkerDetection] = []

    for idx, marker_id_raw in enumerate(ids.flatten().tolist()):
        roi_corner = corners[idx]
        global_corner = roi_corner.copy()
        global_corner[:, :, 0] += float(roi_x)
        global_corner[:, :, 1] += float(roi_y)

        pose = _estimate_marker_pose(
            global_corner,
            marker_length_m=marker_length_m,
            camera_matrix=cfg.camera_matrix,
            dist_coeffs=cfg.dist_coeffs,
        )
        if pose is None:
            continue

        tvec_xyz, distance_m = pose
        marker_center_x = float(np.mean(global_corner[0][:, 0]))
        detections.append(
            MarkerDetection(
                marker_id=int(marker_id_raw),
                rel_x_m=float(tvec_xyz[0]),
                distance_m=distance_m,
                marker_offset_px=marker_center_x - frame_center_x_px,
            )
        )

    return detections


def _collect_spatial_estimates(
    detections: list[MarkerDetection],
    cfg: BridgePoseEstimatorConfig,
    *,
    hint_x_m: float | None,
) -> list[tuple[float, float]]:
    observations = [
        {
            "marker_id": det.marker_id,
            "rel_x_m": det.rel_x_m,
            "distance_m": det.distance_m,
        }
        for det in detections
    ]
    return collect_camera_x_estimates(
        observations,
        cfg.spatial_map,
        axis_sign=cfg.axis_sign,
        hint_x_m=hint_x_m,
        match_tolerance_m=cfg.match_tolerance_m,
    )


def _apply_window_and_gate(state: BridgePoseFilterState, raw_x_m: float) -> float | None:
    max_step_m = pose_max_step_m()
    if (
        max_step_m > 0.0
        and state.last_camera_x_m is not None
        and abs(raw_x_m - state.last_camera_x_m) > max_step_m
    ):
        return None

    window_size = pose_window_size()
    if window_size <= 1:
        return raw_x_m

    state.raw_window.append(raw_x_m)
    while len(state.raw_window) > window_size:
        state.raw_window.popleft()

    if len(state.raw_window) >= window_size:
        return float(median(state.raw_window))
    return raw_x_m


def _apply_ema(state: BridgePoseFilterState, camera_x_m: float, distance_m: float) -> tuple[float, float]:
    alpha = pose_smooth_alpha()
    if alpha <= 0.0 or state.smoothed_camera_x_m is None:
        state.smoothed_camera_x_m = camera_x_m
        state.smoothed_distance_m = distance_m
    else:
        state.smoothed_camera_x_m = alpha * camera_x_m + (1.0 - alpha) * state.smoothed_camera_x_m
        state.smoothed_distance_m = alpha * distance_m + (1.0 - alpha) * state.smoothed_distance_m
    return state.smoothed_camera_x_m, state.smoothed_distance_m


def compute_bridge_pose(
    frame_bgr: np.ndarray,
    cfg: BridgePoseEstimatorConfig,
    state: BridgePoseFilterState,
) -> BridgePoseResult:
    detections = detect_markers_in_roi(frame_bgr, cfg)
    if not detections:
        if pose_debug():
            print("[POSE-DEBUG] no ArUco detections with valid pose in ROI")
        return _held_result(state) or _invalid_pose()

    weighted_estimates = _collect_spatial_estimates(
        detections,
        cfg,
        hint_x_m=state.last_camera_x_m,
    )
    if not weighted_estimates:
        if pose_debug():
            ids = [det.marker_id for det in detections]
            print(f"[POSE-DEBUG] {len(detections)} detection(s) ids={ids}, spatial match=0")
        return _held_result(state) or _invalid_pose()

    values = [value for value, _ in weighted_estimates]
    spread_m = max(values) - min(values) if len(values) > 1 else 0.0

    outlier_m = pose_outlier_m()
    if outlier_m > 0.0 and len(values) > 1:
        center = float(median(values))
        filtered_pairs = [
            (value, weight)
            for value, weight in weighted_estimates
            if abs(value - center) <= outlier_m
        ]
        if not filtered_pairs:
            filtered_pairs = list(weighted_estimates)
    else:
        filtered_pairs = list(weighted_estimates)

    fused = fuse_camera_x_estimate(filtered_pairs)
    if fused is None:
        return _held_result(state, debug_estimates=values, debug_spread_m=spread_m) or _invalid_pose()
    raw_x_m = float(fused)

    gated_x_m = _apply_window_and_gate(state, raw_x_m)
    if gated_x_m is None:
        held = _held_result(state, debug_estimates=values, debug_spread_m=spread_m)
        if held is not None:
            return held
        gated_x_m = state.last_camera_x_m if state.last_camera_x_m is not None else raw_x_m

    center_det = min(detections, key=lambda item: abs(item.marker_offset_px))
    camera_x_m, distance_m = _apply_ema(state, gated_x_m, center_det.distance_m)
    state.last_camera_x_m = gated_x_m

    result = BridgePoseResult(
        camera_x_m=max(0.0, float(camera_x_m)),
        distance_m=max(0.0, float(distance_m)),
        marker_id=center_det.marker_id,
        marker_offset_px=center_det.marker_offset_px,
        valid=True,
        debug_estimates=values,
        debug_spread_m=spread_m,
    )
    state.last_valid = result
    return result


def format_pose_debug(result: BridgePoseResult) -> str:
    if not pose_debug() or not result.valid:
        return ""
    estimates = ", ".join(f"{value:.4f}" for value in result.debug_estimates)
    spread = f"{result.debug_spread_m * 1000:.1f}mm" if result.debug_spread_m is not None else "n/a"
    return f" spread={spread} estimates=[{estimates}]"
