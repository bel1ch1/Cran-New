#!/usr/bin/env python3
"""
Simple Modbus TCP test reader for bridge_pose_modbus.py output.

Reads register map:
- base + 0..1: X (float32, big-endian)
- base + 2..3: Y (float32, big-endian)
- base + 4: marker_id (uint16)
- base + 5: valid flag (uint16)
"""

from __future__ import annotations

import argparse
import time

from pymodbus.client import ModbusTcpClient

from app.services.camera_config import modbus_port, modbus_unit_id
from app.services.pose_modbus_common import BRIDGE_POSE_REGISTER_COUNT, decode_bridge_pose_registers
from app.services.pymodbus_compat import read_holding_registers_compat


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test reader for bridge pose Modbus registers")
    parser.add_argument("--host", default="127.0.0.1", help="Modbus server host")
    parser.add_argument("--port", type=int, default=modbus_port(), help="Modbus server port")
    parser.add_argument("--unit-id", type=int, default=modbus_unit_id(), help="Modbus unit/slave id")
    parser.add_argument("--base-register", type=int, default=100, help="Start address of pose registers")
    parser.add_argument("--interval", type=float, default=0.5, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Read only once and exit")
    parser.add_argument("--raw", action="store_true", help="Print raw register values")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = ModbusTcpClient(host=args.host, port=args.port)
    if not client.connect():
        print(f"[ERROR] Cannot connect to Modbus server {args.host}:{args.port}")
        return 2

    print(
        f"[INFO] Connected to {args.host}:{args.port}, unit_id={args.unit_id}, "
        f"base_register={args.base_register}"
    )

    try:
        while True:
            result = read_holding_registers_compat(
                client=client,
                address=args.base_register,
                count=BRIDGE_POSE_REGISTER_COUNT,
                unit_id=args.unit_id,
            )
            if result.isError():
                print(f"[ERROR] Read failed: {result}")
            else:
                regs = result.registers
                if len(regs) < BRIDGE_POSE_REGISTER_COUNT:
                    print(f"[ERROR] Not enough registers returned: {len(regs)}")
                else:
                    pose = decode_bridge_pose_registers(regs)
                    print(
                        f"X={pose['x_m']:.4f} m | Y={pose['y_m']:.4f} m | "
                        f"marker_id={pose['marker_id']} | valid={1 if pose['valid'] else 0}"
                    )
                    if args.raw:
                        print(f"  raw_registers={regs}")

            if args.once:
                break
            time.sleep(max(0.05, args.interval))
    finally:
        client.close()
        print("[INFO] Disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
