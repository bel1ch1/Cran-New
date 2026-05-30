#!/usr/bin/env python3
"""
Hook marker pose runtime.

Reads `data/calibration_config.json` (hook_calibration), detects target ArUco marker,
computes distance and marker offsets, then writes values to shared Modbus TCP server.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from pymodbus.client import ModbusTcpClient

from app.services.camera_config import modbus_hook_base_register, modbus_port, resolve_pose_fps
from app.services.camera_intrinsics import load_intrinsics_for_camera
from app.services.pose_modbus_common import (
    add_common_pose_args,
    apply_camera_id_override,
    pose_period_seconds,
    run_timed_pose_loop,
    wait_for_modbus_tcp,
    write_runtime_heartbeat,
)
from app.services.pose_runtime_common import PoseCameraSession, detect_markers, install_stop_handlers
from app.services.pymodbus_compat import float_to_holding_registers, write_registers_compat


@dataclass
class HookRuntimeConfig:
    marker_size_mm: int
    marker_id: int
    camera_id: int
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray


@dataclass
class HookPoseResult:
    distance_m: float
    deviation_x_px: float
    deviation_y_px: float
    marker_id: int
    valid: bool


def load_hook_runtime_config(config_path: Path) -> HookRuntimeConfig:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    hook = payload.get("hook_calibration", {})
    marker_size_mm = int(hook.get("marker_size_mm") or 35)
    marker_id = int(hook.get("marker_id") or 1)
    camera = hook.get("camera", {})
    camera_id = int(camera.get("camera_id", 1))
    camera_matrix, dist_coeffs = load_intrinsics_for_camera(camera_id=camera_id, config_path=config_path)
    return HookRuntimeConfig(
        marker_size_mm=marker_size_mm,
        marker_id=marker_id,
        camera_id=camera_id,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
    )


def compute_hook_pose(frame_bgr: np.ndarray, cfg: HookRuntimeConfig) -> HookPoseResult:
    height, width = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detect_markers(gray)
    if ids is None or len(ids) == 0:
        return HookPoseResult(0.0, 0.0, 0.0, cfg.marker_id, False)

    ids_list = ids.flatten().tolist()
    if cfg.marker_id not in ids_list:
        return HookPoseResult(0.0, 0.0, 0.0, cfg.marker_id, False)

    target_idx = ids_list.index(cfg.marker_id)
    corner = corners[target_idx]
    marker_length_m = max(0.001, float(cfg.marker_size_mm) / 1000.0)
    try:
        _, tvec, _ = cv2.aruco.estimatePoseSingleMarkers(
            corners=corner,
            markerLength=marker_length_m,
            cameraMatrix=cfg.camera_matrix,
            distCoeffs=cfg.dist_coeffs,
        )
    except Exception:
        return HookPoseResult(0.0, 0.0, 0.0, cfg.marker_id, False)

    tvec_xyz = tvec[0][0]
    x_m = float(tvec_xyz[0])
    y_m = float(tvec_xyz[1])
    z_m = float(tvec_xyz[2])

    lateral_m = math.sqrt(x_m * x_m + y_m * y_m)
    angle_rad = math.atan2(lateral_m, max(1e-6, z_m))
    corrected_distance_m = z_m / max(1e-6, math.cos(angle_rad))

    points = corner[0]
    marker_center_x_px = float(np.mean(points[:, 0]))
    marker_center_y_px = float(np.mean(points[:, 1]))
    deviation_x_px = marker_center_x_px - (width / 2.0)
    deviation_y_px = marker_center_y_px - (height / 2.0)

    return HookPoseResult(
        distance_m=max(0.0, corrected_distance_m),
        deviation_x_px=deviation_x_px,
        deviation_y_px=deviation_y_px,
        marker_id=cfg.marker_id,
        valid=True,
    )


def write_hook_pose_to_modbus(
    client: ModbusTcpClient,
    unit_id: int,
    base_register: int,
    pose: HookPoseResult,
) -> None:
    d_hi, d_lo = float_to_holding_registers(pose.distance_m)
    dx_hi, dx_lo = float_to_holding_registers(pose.deviation_x_px)
    dy_hi, dy_lo = float_to_holding_registers(pose.deviation_y_px)
    values = [
        d_hi,
        d_lo,
        dx_hi,
        dx_lo,
        dy_hi,
        dy_lo,
        int(max(0, pose.marker_id)),
        1 if pose.valid else 0,
    ]
    write_registers_compat(
        client=client,
        address=base_register,
        values=values,
        unit_id=unit_id,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hook marker pose publisher to shared Modbus TCP server")
    add_common_pose_args(parser)
    parser.add_argument("--modbus-host", default="127.0.0.1", help="Shared Modbus server host")
    parser.add_argument("--modbus-port", type=int, default=modbus_port(), help="Shared Modbus server port")
    parser.add_argument("--modbus-base-register", type=int, default=modbus_hook_base_register())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop = install_stop_handlers()
    processing_fps = resolve_pose_fps(args.fps)

    cfg = apply_camera_id_override(
        load_hook_runtime_config(args.config),
        camera_id=args.camera_id,
        config_path=args.config,
    )

    camera = PoseCameraSession(
        camera_id=cfg.camera_id,
        use_gstreamer=args.use_gstreamer,
        config_path=args.config,
        camera_id_override=args.camera_id,
        role="hook",
    )

    if not wait_for_modbus_tcp(args.modbus_host, args.modbus_port, timeout_s=120.0):
        print(
            f"[ERROR] Shared Modbus server not ready ({args.modbus_host}:{args.modbus_port})",
            file=sys.stderr,
        )
        camera.close()
        return 3

    client = ModbusTcpClient(host=args.modbus_host, port=args.modbus_port)
    if not client.connect():
        print(
            f"[ERROR] Shared Modbus connect failed ({args.modbus_host}:{args.modbus_port})",
            file=sys.stderr,
        )
        camera.close()
        return 3

    print(
        f"[INFO] Running. camera={camera.camera_device}, target_marker={cfg.marker_id}, "
        f"modbus={args.modbus_host}:{args.modbus_port}, base_reg={args.modbus_base_register}"
    )
    child_heartbeat = Path("data/runtime/hook_pose_modbus.heartbeat")

    def _loop_body() -> None:
        nonlocal cfg
        reloaded = camera.reload_config_if_changed(load_hook_runtime_config)
        if reloaded is not None:
            cfg = apply_camera_id_override(
                reloaded,
                camera_id=args.camera_id,
                config_path=args.config,
            )
            if args.camera_id is not None:
                camera.set_camera_id(cfg.camera_id)

        frame = camera.read_frame()
        if frame is not None:
            pose = compute_hook_pose(frame, cfg)
            try:
                write_hook_pose_to_modbus(
                    client=client,
                    unit_id=args.modbus_unit_id,
                    base_register=args.modbus_base_register,
                    pose=pose,
                )
            except Exception as exc:
                print(f"[WARN] Modbus write failed: {exc}", file=sys.stderr)
                if not client.connect():
                    print("[WARN] Modbus reconnect failed", file=sys.stderr)
                return

            if pose.valid:
                print(
                    f"[HOOK] marker={pose.marker_id} distance={pose.distance_m:.4f}m "
                    f"dx={pose.deviation_x_px:.2f}px dy={pose.deviation_y_px:.2f}px"
                )
            else:
                print(f"[HOOK] marker id={cfg.marker_id} not found")
        else:
            print(f"[WARN] Camera frame read/decode failed (device={camera.camera_device})")
        write_runtime_heartbeat(child_heartbeat)

    try:
        run_timed_pose_loop(stop=stop, period_s=pose_period_seconds(processing_fps), body=_loop_body)
    finally:
        client.close()
        camera.close()
        print("[INFO] Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
