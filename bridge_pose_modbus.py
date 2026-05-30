#!/usr/bin/env python3
"""
Bridge camera pose runtime.

Reads `data/calibration_config.json`, detects ArUco markers in ROI, computes:
- X: camera position along calibrated marker path (meters)
- Y: distance to selected marker (meters)

Landmark map keys in JSON are spatial slots (sorted by X), not ArUco IDs.
Runtime matches detections to landmarks by position (SpatialMarkerMap).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.services.bridge_pose_estimator import (
    BridgePoseEstimatorConfig,
    BridgePoseFilterState,
    BridgePoseResult,
    Roi,
    compute_bridge_pose,
    format_pose_debug,
)
from app.services.camera_config import (
    modbus_bridge_base_register,
    modbus_port,
    pose_debug,
    pose_smooth_alpha,
    pose_window_size,
    resolve_pose_fps,
    spatial_runtime_match_tolerance_m,
)
from app.services.camera_intrinsics import load_intrinsics_for_camera
from app.services.pose_modbus_common import (
    BRIDGE_POSE_REGISTER_COUNT,
    add_common_pose_args,
    apply_camera_id_override,
    pose_period_seconds,
    run_timed_pose_loop,
    start_modbus_server,
    write_bridge_pose_to_modbus_store,
    write_runtime_heartbeat,
)
from app.services.pose_runtime_common import PoseCameraSession, install_stop_handlers
from app.services.spatial_marker_map import SpatialMarkerMap, parse_bridge_axis_sign


@dataclass
class BridgeRuntimeConfig:
    marker_size_mm: int
    spatial_map: SpatialMarkerMap
    axis_sign: int
    reference_marker_id: int
    zero_offset_m: float
    roi: Roi
    camera_id: int
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray

    def to_estimator_config(self) -> BridgePoseEstimatorConfig:
        return BridgePoseEstimatorConfig(
            marker_size_mm=self.marker_size_mm,
            spatial_map=self.spatial_map,
            axis_sign=self.axis_sign,
            reference_marker_id=self.reference_marker_id,
            zero_offset_m=self.zero_offset_m,
            roi=self.roi,
            camera_matrix=self.camera_matrix,
            dist_coeffs=self.dist_coeffs,
            match_tolerance_m=spatial_runtime_match_tolerance_m(),
        )


def load_bridge_runtime_config(config_path: Path) -> BridgeRuntimeConfig:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    bridge = payload.get("bridge_calibration", {})

    marker_size_mm = int(bridge.get("marker_size_mm") or 35)
    axis_sign = parse_bridge_axis_sign(str(bridge.get("movement_direction") or "unknown"))

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
        spatial_map=spatial_map,
        axis_sign=axis_sign,
        reference_marker_id=reference_marker_id,
        zero_offset_m=zero_offset_m,
        roi=roi,
        camera_id=camera_id,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge camera pose publisher with built-in Modbus TCP server")
    add_common_pose_args(parser)
    parser.add_argument("--modbus-host", default="0.0.0.0", help="Bind address for internal Modbus server")
    parser.add_argument("--modbus-port", type=int, default=modbus_port(), help="Bind port for internal Modbus server")
    parser.add_argument("--modbus-base-register", type=int, default=modbus_bridge_base_register())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop = install_stop_handlers()
    processing_fps = resolve_pose_fps(args.fps)

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

    filter_state = BridgePoseFilterState()
    child_heartbeat = Path("data/runtime/bridge_pose_modbus.heartbeat")
    context, server_thread = start_modbus_server(
        host=args.modbus_host,
        port=args.modbus_port,
        unit_id=args.modbus_unit_id,
        min_register_count=args.modbus_base_register + BRIDGE_POSE_REGISTER_COUNT,
    )
    time.sleep(0.2)
    if not server_thread.is_alive():
        print(f"[ERROR] Modbus server failed to start on {args.modbus_host}:{args.modbus_port}", file=sys.stderr)
        camera.close()
        return 3

    smooth_alpha = pose_smooth_alpha()
    print(
        f"[INFO] Running. camera={camera.camera_device}, roi=({cfg.roi.x},{cfg.roi.y},{cfg.roi.w},{cfg.roi.h}), "
        f"modbus_server={args.modbus_host}:{args.modbus_port}, unit_id={args.modbus_unit_id}, "
        f"base_reg={args.modbus_base_register}, landmarks={cfg.spatial_map.known_count}, "
        f"spatial_match=1, fps={processing_fps:.1f}, smooth_alpha={smooth_alpha:.2f}, "
        f"window={pose_window_size()}, debug={'on' if pose_debug() else 'off'}"
    )

    def _loop_body() -> None:
        nonlocal cfg, filter_state
        reloaded = camera.reload_config_if_changed(load_bridge_runtime_config)
        if reloaded is not None:
            cfg = apply_camera_id_override(
                reloaded,
                camera_id=args.camera_id,
                config_path=args.config,
            )
            filter_state.reset()
            if args.camera_id is not None:
                camera.set_camera_id(cfg.camera_id)

        pose = BridgePoseResult(0.0, 0.0, -1, 0.0, False)
        frame = camera.read_frame_bgr()
        if frame is not None:
            try:
                pose = compute_bridge_pose(frame, cfg.to_estimator_config(), filter_state)
                if pose.valid:
                    debug_suffix = format_pose_debug(pose)
                    print(
                        f"[POSE] marker={pose.marker_id} X={pose.camera_x_m:.4f}m Y={pose.distance_m:.4f}m "
                        f"offset_px={pose.marker_offset_px:.1f}{debug_suffix}"
                    )
                elif pose_debug():
                    print("[POSE] no valid pose (no markers matched)")
            except Exception as exc:
                print(f"[WARN] Pose loop failed (Modbus server stays up): {exc}", file=sys.stderr)
        else:
            print(f"[WARN] Camera frame read/decode failed (device={camera.camera_device})")

        try:
            write_bridge_pose_to_modbus_store(
                context=context,
                unit_id=args.modbus_unit_id,
                base_register=args.modbus_base_register,
                pose=pose,
            )
        except Exception as exc:
            print(f"[WARN] Modbus publish failed: {exc}", file=sys.stderr)
        write_runtime_heartbeat(child_heartbeat)

    try:
        run_timed_pose_loop(stop=stop, period_s=pose_period_seconds(processing_fps), body=_loop_body)
    finally:
        camera.close()
        print("[INFO] Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
