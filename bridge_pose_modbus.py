#!/usr/bin/env python3
"""
Bridge camera pose runtime for Jetson Nano.

Reads `data/calibration_config.json`, detects ArUco markers in ROI, computes:
- X: camera position along calibrated marker path (meters)
- Y: distance to selected marker (meters)

Writes values to Modbus TCP holding registers so external systems can read them.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
try:
    from pymodbus.datastore import ModbusSlaveContext
except ImportError:
    # pymodbus>=3 uses ModbusDeviceContext instead of ModbusSlaveContext.
    from pymodbus.datastore import ModbusDeviceContext as ModbusSlaveContext
from pymodbus.server import StartTcpServer


ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_50)
ARUCO_PARAMS = cv2.aruco.DetectorParameters()

# Default intrinsics (from current project calibration).
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
class Roi:
    x: int
    y: int
    w: int
    h: int


@dataclass
class BridgeRuntimeConfig:
    marker_size_mm: int
    movement_direction: str
    marker_positions_m: dict[int, float]
    roi: Roi
    camera_id: int


@dataclass
class PoseResult:
    camera_x_m: float
    distance_m: float
    marker_id: int
    marker_offset_px: float
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


def load_bridge_runtime_config(config_path: Path) -> BridgeRuntimeConfig:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    bridge = payload.get("bridge_calibration", {})

    marker_size_mm = int(bridge.get("marker_size_mm") or 35)
    movement_direction = str(bridge.get("movement_direction") or "unknown")
    marker_positions_raw = bridge.get("marker_positions_m", {})
    marker_positions_m: dict[int, float] = {}
    for key, value in marker_positions_raw.items():
        try:
            marker_positions_m[int(key)] = float(value)
        except (TypeError, ValueError):
            continue
    if not marker_positions_m:
        raise ValueError("marker_positions_m is empty in calibration config")

    roi_raw = bridge.get("roi", {})
    roi = Roi(
        x=max(0, int(roi_raw.get("x", 0))),
        y=max(0, int(roi_raw.get("y", 0))),
        w=max(1, int(roi_raw.get("w", 1))),
        h=max(1, int(roi_raw.get("h", 1))),
    )

    camera = bridge.get("camera", {})
    camera_id = int(camera.get("camera_id", 0))

    return BridgeRuntimeConfig(
        marker_size_mm=marker_size_mm,
        movement_direction=movement_direction,
        marker_positions_m=marker_positions_m,
        roi=roi,
        camera_id=camera_id,
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
    corners, ids, _ = _detect_markers(gray)
    if ids is None or len(ids) == 0:
        return PoseResult(0.0, 0.0, -1, 0.0, False)

    frame_center_x_px = frame_w / 2.0
    marker_length_m = max(0.001, float(cfg.marker_size_mm) / 1000.0)
    best: PoseResult | None = None

    for idx, marker_id_raw in enumerate(ids.flatten().tolist()):
        marker_id = int(marker_id_raw)
        if marker_id not in cfg.marker_positions_m:
            continue

        roi_corner = corners[idx]
        global_corner = roi_corner.copy()
        global_corner[:, :, 0] += float(roi_x)
        global_corner[:, :, 1] += float(roi_y)
        try:
            _, tvec, _ = cv2.aruco.estimatePoseSingleMarkers(
                corners=global_corner,
                markerLength=marker_length_m,
                cameraMatrix=CAMERA_MATRIX,
                distCoeffs=DIST_COEFFS,
            )
        except Exception:
            continue

        tvec_xyz = tvec[0][0]
        z_m = float(tvec_xyz[2])
        marker_center_x = float(np.mean(global_corner[0][:, 0]))
        marker_offset_px = marker_center_x - frame_center_x_px
        rel_x_m = float((marker_offset_px / float(CAMERA_MATRIX[0, 0])) * z_m)

        marker_x_m = cfg.marker_positions_m[marker_id]
        if cfg.movement_direction == "right_to_left":
            camera_x_m = marker_x_m + rel_x_m
        else:
            camera_x_m = marker_x_m - rel_x_m

        distance_m = float(math.sqrt(float(np.dot(tvec_xyz, tvec_xyz))))
        candidate = PoseResult(
            camera_x_m=max(0.0, camera_x_m),
            distance_m=max(0.0, distance_m),
            marker_id=marker_id,
            marker_offset_px=marker_offset_px,
            valid=True,
        )

        # Prefer the known marker closest to optical center.
        if best is None or abs(candidate.marker_offset_px) < abs(best.marker_offset_px):
            best = candidate

    if best is None:
        return PoseResult(0.0, 0.0, -1, 0.0, False)
    return best


def write_pose_to_modbus_store(
    context: ModbusServerContext,
    unit_id: int,
    base_register: int,
    pose: PoseResult,
) -> None:
    x_hi, x_lo = _float_to_holding_registers(pose.camera_x_m)
    y_hi, y_lo = _float_to_holding_registers(pose.distance_m)
    values = [
        x_hi,
        x_lo,
        y_hi,
        y_lo,
        int(max(0, pose.marker_id)),
        1 if pose.valid else 0,
    ]
    try:
        context[int(unit_id)].setValues(3, base_register, values)
        return
    except Exception:
        pass
    # Fallback for server contexts configured in single mode internally.
    context[0].setValues(3, base_register, values)


def start_modbus_server(
    host: str,
    port: int,
    unit_id: int,
    min_register_count: int,
) -> tuple[ModbusServerContext, threading.Thread]:
    total_registers = max(256, min_register_count + 64)
    try:
        store = ModbusSlaveContext(
            hr=ModbusSequentialDataBlock(0, [0] * total_registers),
            zero_mode=True,
        )
    except TypeError:
        # pymodbus variants where zero_mode arg is absent.
        store = ModbusSlaveContext(
            hr=ModbusSequentialDataBlock(0, [0] * total_registers),
        )

    try:
        context = ModbusServerContext(slaves={int(unit_id): store}, single=False)
    except TypeError:
        # pymodbus>=3 may use "devices" instead of "slaves".
        context = ModbusServerContext(devices={int(unit_id): store}, single=False)

    def _run_server() -> None:
        StartTcpServer(context=context, address=(host, port))

    thread = threading.Thread(target=_run_server, name="modbus-tcp-server", daemon=True)
    thread.start()
    return context, thread


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge camera pose publisher with built-in Modbus TCP server")
    parser.add_argument("--config", type=Path, default=Path("data/calibration_config.json"))
    parser.add_argument("--fps", type=float, default=8.0, help="Processing frequency")
    parser.add_argument("--modbus-host", default="0.0.0.0", help="Bind address for internal Modbus server")
    parser.add_argument("--modbus-port", type=int, default=5020, help="Bind port for internal Modbus server")
    parser.add_argument("--modbus-unit-id", type=int, default=1)
    parser.add_argument("--modbus-base-register", type=int, default=100)
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

    cfg = load_bridge_runtime_config(args.config)
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

    min_register_count = args.modbus_base_register + 6
    context, server_thread = start_modbus_server(
        host=args.modbus_host,
        port=args.modbus_port,
        unit_id=args.modbus_unit_id,
        min_register_count=min_register_count,
    )
    time.sleep(0.2)
    if not server_thread.is_alive():
        print(f"[ERROR] Modbus server failed to start on {args.modbus_host}:{args.modbus_port}", file=sys.stderr)
        cap.release()
        return 3

    print(
        f"[INFO] Running. camera_id={cfg.camera_id}, roi=({cfg.roi.x},{cfg.roi.y},{cfg.roi.w},{cfg.roi.h}), "
        f"modbus_server={args.modbus_host}:{args.modbus_port}, unit_id={args.modbus_unit_id}, "
        f"base_reg={args.modbus_base_register}"
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
                        cfg = load_bridge_runtime_config(args.config)
                        if args.camera_id is not None:
                            cfg.camera_id = int(args.camera_id)
                        config_mtime = current_mtime
                except Exception:
                    pass

                pose = compute_camera_pose(frame, cfg)
                write_pose_to_modbus_store(
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
                print("[WARN] Camera frame read failed")

            elapsed = time.time() - frame_start
            if elapsed < period_s:
                time.sleep(period_s - elapsed)
    finally:
        cap.release()
        print("[INFO] Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
