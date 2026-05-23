"""Shared CLI, Modbus server, and camera env helpers for pose runtimes."""

from __future__ import annotations

import argparse
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext

try:
    from pymodbus.datastore import ModbusSlaveContext
except ImportError:
    from pymodbus.datastore import ModbusDeviceContext as ModbusSlaveContext
from pymodbus.server import StartTcpServer

from app.services.camera_intrinsics import load_intrinsics_for_camera

ConfigT = TypeVar("ConfigT")


def add_common_pose_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=Path("data/calibration_config.json"))
    parser.add_argument("--fps", type=float, default=8.0, help="Processing frequency")
    parser.add_argument("--modbus-unit-id", type=int, default=1)
    parser.add_argument(
        "--use-gstreamer",
        action="store_true",
        help="Use Jetson nvarguscamerasrc pipeline (not for Raspberry Pi libcamera)",
    )
    parser.add_argument("--camera-id", type=int, default=None, help="Override camera_id from config")


def resolve_pose_camera_device(role: str | None, camera_id: int) -> str:
    """Prefer CRAN_*_CAMERA_DEVICE env, fall back to libcamera cam-index."""
    env_key = {
        "bridge": "CRAN_BRIDGE_CAMERA_DEVICE",
        "hook": "CRAN_HOOK_CAMERA_DEVICE",
    }.get(role or "")
    if env_key:
        configured = os.getenv(env_key, "").strip()
        if configured:
            return configured
    return str(camera_id)


def apply_camera_id_override(
    cfg: ConfigT,
    *,
    camera_id: int | None,
    config_path: Path,
) -> ConfigT:
    if camera_id is None:
        return cfg
    cfg.camera_id = int(camera_id)
    cfg.camera_matrix, cfg.dist_coeffs = load_intrinsics_for_camera(
        camera_id=cfg.camera_id,
        config_path=config_path,
    )
    return cfg


def refresh_config_after_reload(
    cfg: ConfigT,
    *,
    camera_id_override: int | None,
    config_path: Path,
) -> ConfigT:
    return apply_camera_id_override(cfg, camera_id=camera_id_override, config_path=config_path)


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
        store = ModbusSlaveContext(
            hr=ModbusSequentialDataBlock(0, [0] * total_registers),
        )

    try:
        context = ModbusServerContext(slaves={int(unit_id): store}, single=False)
    except TypeError:
        context = ModbusServerContext(devices={int(unit_id): store}, single=False)

    def _run_server() -> None:
        StartTcpServer(context=context, address=(host, port))

    thread = threading.Thread(target=_run_server, name="modbus-tcp-server", daemon=True)
    thread.start()
    return context, thread


def write_bridge_pose_to_modbus_store(
    context: ModbusServerContext,
    unit_id: int,
    base_register: int,
    pose,
) -> None:
    from app.services.pymodbus_compat import float_to_holding_registers

    x_hi, x_lo = float_to_holding_registers(pose.camera_x_m)
    y_hi, y_lo = float_to_holding_registers(pose.distance_m)
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
    context[0].setValues(3, base_register, values)


def pose_period_seconds(fps: float) -> float:
    return 1.0 / max(0.5, float(fps))


def run_timed_pose_loop(
    *,
    stop: dict[str, bool],
    period_s: float,
    body: Callable[[], None],
) -> None:
    import time

    while not stop["value"]:
        frame_start = time.time()
        body()
        elapsed = time.time() - frame_start
        if elapsed < period_s:
            time.sleep(period_s - elapsed)
