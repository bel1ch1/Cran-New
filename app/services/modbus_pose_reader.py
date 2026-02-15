from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from pymodbus.client import ModbusTcpClient


def _registers_to_float(hi: int, lo: int) -> float:
    packed = struct.pack(">HH", int(hi) & 0xFFFF, int(lo) & 0xFFFF)
    return struct.unpack(">f", packed)[0]


def _read_holding_registers_compat(
    client: ModbusTcpClient,
    address: int,
    count: int,
    unit_id: int,
):
    call_variants = [
        {"address": address, "count": count, "device_id": unit_id},
        {"address": address, "count": count, "slave": unit_id},
        {"address": address, "count": count, "unit": unit_id},
        {"address": address, "count": count},
    ]
    last_error: Exception | None = None
    for kwargs in call_variants:
        try:
            return client.read_holding_registers(**kwargs)
        except TypeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to call read_holding_registers")


@dataclass
class ModbusPoseReaderConfig:
    host: str
    port: int
    unit_id: int
    bridge_base_register: int
    hook_base_register: int


def read_pose_values(config: ModbusPoseReaderConfig) -> dict[str, Any]:
    client = ModbusTcpClient(host=config.host, port=config.port)
    connected = client.connect()
    if not connected:
        return {
            "connected": False,
            "error": f"Cannot connect to Modbus server {config.host}:{config.port}",
            "bridge": {"valid": False},
            "hook": {"valid": False},
        }

    try:
        bridge_resp = _read_holding_registers_compat(
            client=client,
            address=config.bridge_base_register,
            count=6,
            unit_id=config.unit_id,
        )
        hook_resp = _read_holding_registers_compat(
            client=client,
            address=config.hook_base_register,
            count=8,
            unit_id=config.unit_id,
        )
    except Exception as exc:
        client.close()
        return {
            "connected": False,
            "error": f"Modbus read failed: {exc}",
            "bridge": {"valid": False},
            "hook": {"valid": False},
        }

    client.close()

    if bridge_resp.isError() or hook_resp.isError():
        return {
            "connected": True,
            "error": f"Read error bridge={bridge_resp} hook={hook_resp}",
            "bridge": {"valid": False},
            "hook": {"valid": False},
        }

    bridge_regs = bridge_resp.registers or []
    hook_regs = hook_resp.registers or []
    if len(bridge_regs) < 6 or len(hook_regs) < 8:
        return {
            "connected": True,
            "error": f"Not enough registers bridge={len(bridge_regs)} hook={len(hook_regs)}",
            "bridge": {"valid": False},
            "hook": {"valid": False},
        }

    bridge_x_m = _registers_to_float(bridge_regs[0], bridge_regs[1])
    bridge_y_m = _registers_to_float(bridge_regs[2], bridge_regs[3])
    bridge_marker_id = int(bridge_regs[4])
    bridge_valid = int(bridge_regs[5]) != 0

    hook_distance_m = _registers_to_float(hook_regs[0], hook_regs[1])
    hook_dx_px = _registers_to_float(hook_regs[2], hook_regs[3])
    hook_dy_px = _registers_to_float(hook_regs[4], hook_regs[5])
    hook_marker_id = int(hook_regs[6])
    hook_valid = int(hook_regs[7]) != 0

    return {
        "connected": True,
        "error": None,
        "bridge": {
            "x_m": round(bridge_x_m, 4),
            "y_m": round(bridge_y_m, 4),
            "marker_id": bridge_marker_id,
            "valid": bridge_valid,
        },
        "hook": {
            "distance_m": round(hook_distance_m, 4),
            "deviation_x_px": round(hook_dx_px, 2),
            "deviation_y_px": round(hook_dy_px, 2),
            "marker_id": hook_marker_id,
            "valid": hook_valid,
        },
    }
