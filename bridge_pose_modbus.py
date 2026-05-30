#!/usr/bin/env python3
"""
Bridge camera pose runtime.

Reads `data/calibration_config.json`, detects ArUco markers in ROI, computes:
- X: camera position along calibrated marker path (meters)
- Y: distance to selected marker (meters)

Writes values to Modbus TCP holding registers so external systems can read them.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.services.camera_intrinsics import load_intrinsics_for_camera
from app.services.pose_modbus_common import (
    add_common_pose_args,
    apply_camera_id_override,
    pose_period_seconds,
    refresh_config_after_reload,
    run_timed_pose_loop,
    start_modbus_server,
    write_bridge_pose_to_modbus_store,
    write_runtime_heartbeat,
)
from app.services.pose_runtime_common import PoseCameraSession, detect_markers, install_stop_handlers
from app.services.spatial_marker_map import (
    RUNTIME_MATCH_TOLERANCE_M,
    SpatialMarkerMap,
    parse_bridge_axis_sign,
)


@dataclass
class Roi:
    x: int
    y: int
    w: int
    h: int


@dataclass
class BridgeRuntimeConfig:
    marker_size_mm: int
    movement_direction: str
    spatial_map: SpatialMarkerMap
    axis_sign: int
    reference_marker_id: int
    zero_offset_m: float
    roi: Roi
    camera_id: int
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray


@dataclass
class PoseResult:
    camera_x_m: float
    distance_m: float
    marker_id: int
    marker_offset_px: float
    valid: bool


def load_bridge_runtime_config(config_path: Path) -> BridgeRuntimeConfig:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    bridge = payload.get("bridge_calibration", {})

    marker_size_mm = int(bridge.get("marker_size_mm") or 35)
    movement_direction = str(bridge.get("movement_direction") or "unknown")
    axis_sign = parse_bridge_axis_sign(movement_direction)

    marker_positions_raw = bridge.get("marker_positions_m", {})
    marker_positions_m: dict[str, float] = {}
    for key, value in marker_positions_raw.items():
        try:
            marker_positions_m[str(key)] = float(value)
        except (TypeError, ValueError):
            continue

    reference_marker_id = int(bridge.get("reference_marker_id", 0))
    zero_offset_m = float(bridge.get("zero_marker_offset_m", 0.0))
    spatial_map = SpatialMarkerMap.from_marker_positions_m(
        marker_positions_m,
        reference_marker_id=reference_marker_id,
        zero_offset=zero_offset_m,
    )

    roi_raw = bridge.get("roi", {})
    roi = Roi(
        x=max(0, int(roi_raw.get("x", 0))),
        y=max(0, int(roi_raw.get("y", 0))),
        w=max(1, int(roi_raw.get("w", 1))),
        h=max(1, int(roi_raw.get("h", 1))),
    )

    camera = bridge.get("camera", {})
    camera_id = int(camera.get("camera_id", 0))
    camera_matrix, dist_coeffs = load_intrinsics_for_camera(camera_id=camera_id, config_path=config_path)

    return BridgeRuntimeConfig(
        marker_size_mm=marker_size_mm,
        movement_direction=movement_direction,
        spatial_map=spatial_map,
        axis_sign=axis_sign,
        reference_marker_id=reference_marker_id,
        zero_offset_m=zero_offset_m,
        roi=roi,
        camera_id=camera_id,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
    )


def compute_camera_pose(frame_bgr: np.ndarray, cfg: BridgeRuntimeConfig) -> PoseResult:
    frame_h, frame_w = frame_bgr.shape[:2]
    roi_x = min(max(cfg.roi.x, 0), frame_w - 1)
    roi_y = min(max(cfg.roi.y, 0), frame_h - 1)
    roi_w = min(cfg.roi.w, frame_w - roi_x)
    roi_h = min(cfg.roi.h, frame_h - roi_y)

    roi_frame = frame_bgr[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
    if roi_frame.size == 0:
        return PoseResult(0.0, 0.0, -1, 0.0, False)

    gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detect_markers(gray)
    if ids is None or len(ids) == 0:
        return PoseResult(0.0, 0.0, -1, 0.0, False)

    frame_center_x_px = frame_w / 2.0
    marker_length_m = max(0.001, float(cfg.marker_size_mm) / 1000.0)
    detections: list[dict[str, float | int]] = []

    for idx, marker_id_raw in enumerate(ids.flatten().tolist()):
        marker_id = int(marker_id_raw)
        roi_corner = corners[idx]
        global_corner = roi_corner.copy()
        global_corner[:, :, 0] += float(roi_x)
        global_corner[:, :, 1] += float(roi_y)
        try:
            _, tvec, _ = cv2.aruco.estimatePoseSingleMarkers(
                corners=global_corner,
                markerLength=marker_length_m,
                cameraMatrix=cfg.camera_matrix,
                distCoeffs=cfg.dist_coeffs,
            )
        except Exception:
            continue

        tvec_xyz = tvec[0][0]
        z_m = float(tvec_xyz[2])
        marker_center_x = float(np.mean(global_corner[0][:, 0]))
        marker_offset_px = marker_center_x - frame_center_x_px
        rel_x_m = float((marker_offset_px / float(cfg.camera_matrix[0, 0])) * z_m)
        distance_m = float(math.sqrt(float(np.dot(tvec_xyz, tvec_xyz))))
        detections.append(
            {
                "marker_id": marker_id,
                "rel_x_m": rel_x_m,
                "distance_m": distance_m,
                "marker_offset_px": marker_offset_px,
            }
        )

    if not detections:
        return PoseResult(0.0, 0.0, -1, 0.0, False)

    camera_x_estimates: list[float] = []
    for det in detections:
        rel_x_m = float(det["rel_x_m"])
        marker_id = int(det["marker_id"])
        if marker_id == cfg.reference_marker_id:
            camera_x_estimates.append(cfg.zero_offset_m - (cfg.axis_sign * rel_x_m))

    hint_x = float(np.median(camera_x_estimates)) if camera_x_estimates else None

    for det in detections:
        rel_x_m = float(det["rel_x_m"])
        marker_id = int(det["marker_id"])
        if marker_id == cfg.reference_marker_id:
            continue
        if hint_x is not None:
            landmark_x = cfg.spatial_map.match_landmark_for_detection(
                rel_x_m,
                hint_x,
                cfg.axis_sign,
                tolerance=RUNTIME_MATCH_TOLERANCE_M,
            )
            if landmark_x is not None:
                camera_x_estimates.append(landmark_x - (cfg.axis_sign * rel_x_m))
                continue
        for landmark_x in cfg.spatial_map.trusted_x_positions():
            candidate = landmark_x - (cfg.axis_sign * rel_x_m)
            matched = cfg.spatial_map.match_landmark_for_detection(
                rel_x_m,
                candidate,
                cfg.axis_sign,
                tolerance=RUNTIME_MATCH_TOLERANCE_M,
            )
            if matched is not None:
                camera_x_estimates.append(candidate)
                break

    if not camera_x_estimates:
        return PoseResult(0.0, 0.0, -1, 0.0, False)

    center_det = min(detections, key=lambda item: abs(float(item["marker_offset_px"])))
    camera_x_m = max(0.0, float(np.median(camera_x_estimates)))

    return PoseResult(
        camera_x_m=camera_x_m,
        distance_m=max(0.0, float(center_det["distance_m"])),
        marker_id=int(center_det["marker_id"]),
        marker_offset_px=float(center_det["marker_offset_px"]),
        valid=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge camera pose publisher with built-in Modbus TCP server")
    add_common_pose_args(parser)
    parser.add_argument("--modbus-host", default="0.0.0.0", help="Bind address for internal Modbus server")
    parser.add_argument("--modbus-port", type=int, default=5020, help="Bind port for internal Modbus server")
    parser.add_argument("--modbus-base-register", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop = install_stop_handlers()

    cfg = apply_camera_id_override(
        load_bridge_runtime_config(args.config),
        camera_id=args.camera_id,
        config_path=args.config,
    )

    camera = PoseCameraSession(
        camera_id=cfg.camera_id,
        use_gstreamer=args.use_gstreamer,
        config_path=args.config,
        camera_id_override=args.camera_id,
        role="bridge",
    )

    min_register_count = args.modbus_base_register + 6
    child_heartbeat = Path("data/runtime/bridge_pose_modbus.heartbeat")
    context, server_thread = start_modbus_server(
        host=args.modbus_host,
        port=args.modbus_port,
        unit_id=args.modbus_unit_id,
        min_register_count=min_register_count,
    )
    time.sleep(0.2)
    if not server_thread.is_alive():
        print(f"[ERROR] Modbus server failed to start on {args.modbus_host}:{args.modbus_port}", file=sys.stderr)
        camera.close()
        return 3

    print(
        f"[INFO] Running. camera={camera.camera_device}, roi=({cfg.roi.x},{cfg.roi.y},{cfg.roi.w},{cfg.roi.h}), "
        f"modbus_server={args.modbus_host}:{args.modbus_port}, unit_id={args.modbus_unit_id}, "
        f"base_reg={args.modbus_base_register}"
    )

    def _loop_body() -> None:
        nonlocal cfg
        reloaded = camera.reload_config_if_changed(load_bridge_runtime_config)
        if reloaded is not None:
            cfg = refresh_config_after_reload(
                reloaded,
                camera_id_override=args.camera_id,
                config_path=args.config,
            )
            if args.camera_id is not None:
                camera.set_camera_id(cfg.camera_id)

        frame = camera.read_frame()
        if frame is not None:
            pose = compute_camera_pose(frame, cfg)
            write_bridge_pose_to_modbus_store(
                context=context,
                unit_id=args.modbus_unit_id,
                base_register=args.modbus_base_register,
                pose=pose,
            )
            if pose.valid:
                print(
                    f"[POSE] marker={pose.marker_id} X={pose.camera_x_m:.4f}m Y={pose.distance_m:.4f}m "
                    f"offset_px={pose.marker_offset_px:.1f}"
                )
            else:
                print("[POSE] no known marker in ROI")
        else:
            print(f"[WARN] Camera frame read/decode failed (device={camera.camera_device})")
        write_runtime_heartbeat(child_heartbeat)

    try:
        run_timed_pose_loop(stop=stop, period_s=pose_period_seconds(args.fps), body=_loop_body)
    finally:
        camera.close()
        print("[INFO] Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
