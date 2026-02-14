#!/usr/bin/env python3
"""
Hook marker pose runtime for Jetson Nano.

Reads `data/calibration_config.json` (hook_calibration), detects target ArUco marker,
computes distance and marker offsets, then writes values to shared Modbus TCP server.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from pymodbus.client import ModbusTcpClient


ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_50)
ARUCO_PARAMS = cv2.aruco.DetectorParameters()

# Default intrinsics (from project calibration).
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


@dataclass
class HookRuntimeConfig:
    marker_size_mm: int
    marker_id: int
    camera_id: int


@dataclass
class HookPoseResult:
    distance_m: float
    deviation_x_px: float
    deviation_y_px: float
    marker_id: int
    valid: bool


def _float_to_holding_registers(value: float) -> tuple[int, int]:
    packed = struct.pack(">f", float(value))
    return struct.unpack(">HH", packed)


def _build_default_pipeline(sensor_id: int) -> str:
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        "video/x-raw(memory:NVMM), width=1280, height=720, framerate=30/1 ! "
        "nvvidconv ! video/x-raw, format=BGRx ! "
        "videoconvert ! video/x-raw, format=BGR ! "
        "appsink drop=1"
    )


def _detect_markers(gray_frame: np.ndarray):
    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(ARUCO_DICT, ARUCO_PARAMS)
        return detector.detectMarkers(gray_frame)
    return cv2.aruco.detectMarkers(gray_frame, ARUCO_DICT, parameters=ARUCO_PARAMS)


def load_hook_runtime_config(config_path: Path) -> HookRuntimeConfig:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    hook = payload.get("hook_calibration", {})
    marker_size_mm = int(hook.get("marker_size_mm") or 35)
    marker_id = int(hook.get("marker_id") or 1)
    camera = hook.get("camera", {})
    camera_id = int(camera.get("camera_id", 1))
    return HookRuntimeConfig(marker_size_mm=marker_size_mm, marker_id=marker_id, camera_id=camera_id)


def compute_hook_pose(frame_bgr: np.ndarray, cfg: HookRuntimeConfig) -> HookPoseResult:
    height, width = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detect_markers(gray)
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
            cameraMatrix=CAMERA_MATRIX,
            distCoeffs=DIST_COEFFS,
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


def _write_registers_compat(
    client: ModbusTcpClient,
    address: int,
    values: list[int],
    unit_id: int,
) -> None:
    call_variants = [
        {"address": address, "values": values, "device_id": unit_id},
        {"address": address, "values": values, "slave": unit_id},
        {"address": address, "values": values, "unit": unit_id},
        {"address": address, "values": values},
    ]
    last_error: Exception | None = None
    for kwargs in call_variants:
        try:
            client.write_registers(**kwargs)
            return
        except TypeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to call write_registers")


def write_hook_pose_to_modbus(
    client: ModbusTcpClient,
    unit_id: int,
    base_register: int,
    pose: HookPoseResult,
) -> None:
    d_hi, d_lo = _float_to_holding_registers(pose.distance_m)
    dx_hi, dx_lo = _float_to_holding_registers(pose.deviation_x_px)
    dy_hi, dy_lo = _float_to_holding_registers(pose.deviation_y_px)
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
    _write_registers_compat(
        client=client,
        address=base_register,
        values=values,
        unit_id=unit_id,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hook marker pose publisher to shared Modbus TCP server")
    parser.add_argument("--config", type=Path, default=Path("data/calibration_config.json"))
    parser.add_argument("--fps", type=float, default=8.0, help="Processing frequency")
    parser.add_argument("--modbus-host", default="127.0.0.1", help="Shared Modbus server host")
    parser.add_argument("--modbus-port", type=int, default=5020, help="Shared Modbus server port")
    parser.add_argument("--modbus-unit-id", type=int, default=1)
    parser.add_argument("--modbus-base-register", type=int, default=200)
    parser.add_argument(
        "--use-gstreamer",
        action="store_true",
        help="Use default nvarguscamerasrc pipeline for Jetson CSI camera",
    )
    parser.add_argument("--camera-id", type=int, default=None, help="Override camera_id from config")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop = {"value": False}

    def _handle_stop(_sig, _frame) -> None:
        stop["value"] = True

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    cfg = load_hook_runtime_config(args.config)
    if args.camera_id is not None:
        cfg.camera_id = int(args.camera_id)
    period_s = 1.0 / max(0.5, float(args.fps))

    if args.use_gstreamer:
        source = _build_default_pipeline(cfg.camera_id)
        cap = cv2.VideoCapture(source, cv2.CAP_GSTREAMER)
    else:
        cap = cv2.VideoCapture(cfg.camera_id)

    if not cap.isOpened():
        print(f"[ERROR] Camera open failed (camera_id={cfg.camera_id})", file=sys.stderr)
        return 2

    client = ModbusTcpClient(host=args.modbus_host, port=args.modbus_port)
    if not client.connect():
        print(
            f"[ERROR] Shared Modbus connect failed ({args.modbus_host}:{args.modbus_port})",
            file=sys.stderr,
        )
        cap.release()
        return 3

    print(
        f"[INFO] Running. camera_id={cfg.camera_id}, target_marker={cfg.marker_id}, "
        f"modbus={args.modbus_host}:{args.modbus_port}, base_reg={args.modbus_base_register}"
    )

    config_mtime = args.config.stat().st_mtime
    try:
        while not stop["value"]:
            frame_start = time.time()
            ok, frame = cap.read()
            if ok and frame is not None:
                try:
                    current_mtime = args.config.stat().st_mtime
                    if current_mtime != config_mtime:
                        cfg = load_hook_runtime_config(args.config)
                        if args.camera_id is not None:
                            cfg.camera_id = int(args.camera_id)
                        config_mtime = current_mtime
                except Exception:
                    pass

                pose = compute_hook_pose(frame, cfg)
                write_hook_pose_to_modbus(
                    client=client,
                    unit_id=args.modbus_unit_id,
                    base_register=args.modbus_base_register,
                    pose=pose,
                )

                if pose.valid:
                    print(
                        f"[HOOK] marker={pose.marker_id} distance={pose.distance_m:.4f}m "
                        f"dx={pose.deviation_x_px:.2f}px dy={pose.deviation_y_px:.2f}px"
                    )
                else:
                    print(f"[HOOK] marker id={cfg.marker_id} not found")
            else:
                print("[WARN] Camera frame read failed")

            elapsed = time.time() - frame_start
            if elapsed < period_s:
                time.sleep(period_s - elapsed)
    finally:
        client.close()
        cap.release()
        print("[INFO] Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
