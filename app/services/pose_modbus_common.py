"""Shared CLI, Modbus server, and camera env helpers for pose runtimes."""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext

try:
    from pymodbus.datastore import ModbusSlaveContext
except ImportError:
    from pymodbus.datastore import ModbusDeviceContext as ModbusSlaveContext
from pymodbus.server import StartTcpServer

from app.services.camera_config import (
    DEFAULT_POSE_FPS,
    modbus_bridge_base_register,
    modbus_port,
    modbus_unit_id,
)
from app.services.camera_intrinsics import load_intrinsics_for_camera
from app.services.pymodbus_compat import (
    coerce_register_list,
    float_to_holding_registers,
    holding_registers_to_float,
)

ConfigT = TypeVar("ConfigT")

BRIDGE_POSE_REGISTER_COUNT = 6
HOOK_POSE_REGISTER_COUNT = 8


def add_common_pose_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=Path("data/calibration_config.json"))
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_POSE_FPS,
        help="Processing frequency (overridden by CRAN_POSE_FPS when set)",
    )
    parser.add_argument("--modbus-unit-id", type=int, default=modbus_unit_id())
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


def decode_bridge_pose_registers(regs: list[int]) -> dict[str, Any]:
    if len(regs) < BRIDGE_POSE_REGISTER_COUNT:
        raise ValueError(f"Expected at least {BRIDGE_POSE_REGISTER_COUNT} bridge registers")
    return {
        "x_m": holding_registers_to_float(regs[0], regs[1]),
        "y_m": holding_registers_to_float(regs[2], regs[3]),
        "marker_id": int(regs[4]) & 0xFFFF,
        "valid": (int(regs[5]) & 0xFFFF) != 0,
    }


def decode_hook_pose_registers(regs: list[int]) -> dict[str, Any]:
    if len(regs) < HOOK_POSE_REGISTER_COUNT:
        raise ValueError(f"Expected at least {HOOK_POSE_REGISTER_COUNT} hook registers")
    return {
        "distance_m": holding_registers_to_float(regs[0], regs[1]),
        "deviation_x_px": holding_registers_to_float(regs[2], regs[3]),
        "deviation_y_px": holding_registers_to_float(regs[4], regs[5]),
        "marker_id": int(regs[6]) & 0xFFFF,
        "valid": (int(regs[7]) & 0xFFFF) != 0,
    }


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
    x_hi, x_lo = float_to_holding_registers(getattr(pose, "camera_x_m", 0.0))
    y_hi, y_lo = float_to_holding_registers(getattr(pose, "distance_m", 0.0))
    marker_id = int(getattr(pose, "marker_id", -1) or -1)
    valid_flag = 1 if bool(getattr(pose, "valid", False)) else 0
    values = coerce_register_list(
        [
            x_hi,
            x_lo,
            y_hi,
            y_lo,
            max(0, marker_id),
            valid_flag,
        ]
    )

    slave_id = int(unit_id)
    try:
        slave_context = context[slave_id]
    except Exception as exc:
        raise RuntimeError(f"Modbus slave context {slave_id} is not available") from exc

    # fc_as_hex=3 -> holding registers (Holding Register / FC03 map).
    slave_context.setValues(3, int(base_register), values)


def write_runtime_heartbeat(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass


def pose_period_seconds(fps: float) -> float:
    return 1.0 / max(0.5, float(fps))


def wait_for_modbus_tcp(
    host: str,
    port: int,
    *,
    timeout_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> bool:
    deadline = time.time() + max(0.5, float(timeout_s))
    while time.time() < deadline:
        try:
            with socket.create_connection((host, int(port)), timeout=1.0):
                return True
        except OSError:
            time.sleep(max(0.1, float(poll_interval_s)))
    return False


def run_timed_pose_loop(
    *,
    stop: dict[str, bool],
    period_s: float,
    body: Callable[[], None],
) -> None:
    while not stop["value"]:
        frame_start = time.time()
        body()
        elapsed = time.time() - frame_start
        if elapsed < period_s:
            time.sleep(period_s - elapsed)
