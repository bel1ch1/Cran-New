from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymodbus.client import ModbusTcpClient

from app.services.pose_modbus_common import (
    BRIDGE_POSE_REGISTER_COUNT,
    HOOK_POSE_REGISTER_COUNT,
    decode_bridge_pose_registers,
    decode_hook_pose_registers,
)
from app.services.pymodbus_compat import read_holding_registers_compat


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
        bridge_resp = read_holding_registers_compat(
            client=client,
            address=config.bridge_base_register,
            count=BRIDGE_POSE_REGISTER_COUNT,
            unit_id=config.unit_id,
        )
        hook_resp = read_holding_registers_compat(
            client=client,
            address=config.hook_base_register,
            count=HOOK_POSE_REGISTER_COUNT,
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
    if len(bridge_regs) < BRIDGE_POSE_REGISTER_COUNT or len(hook_regs) < HOOK_POSE_REGISTER_COUNT:
        return {
            "connected": True,
            "error": f"Not enough registers bridge={len(bridge_regs)} hook={len(hook_regs)}",
            "bridge": {"valid": False},
            "hook": {"valid": False},
        }

    bridge = decode_bridge_pose_registers(bridge_regs)
    hook = decode_hook_pose_registers(hook_regs)

    return {
        "connected": True,
        "error": None,
        "bridge": {
            "x_m": round(float(bridge["x_m"]), 4),
            "y_m": round(float(bridge["y_m"]), 4),
            "marker_id": bridge["marker_id"],
            "valid": bridge["valid"],
        },
        "hook": {
            "distance_m": round(float(hook["distance_m"]), 4),
            "deviation_x_px": round(float(hook["deviation_x_px"]), 2),
            "deviation_y_px": round(float(hook["deviation_y_px"]), 2),
            "marker_id": hook["marker_id"],
            "valid": hook["valid"],
        },
    }
