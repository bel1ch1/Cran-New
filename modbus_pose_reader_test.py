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
import struct
import time

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
    """
    Read holding registers with compatibility across pymodbus versions.
    """
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test reader for bridge pose Modbus registers")
    parser.add_argument("--host", default="127.0.0.1", help="Modbus server host")
    parser.add_argument("--port", type=int, default=5020, help="Modbus server port")
    parser.add_argument("--unit-id", type=int, default=1, help="Modbus unit/slave id")
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
            result = _read_holding_registers_compat(
                client=client,
                address=args.base_register,
                count=6,
                unit_id=args.unit_id,
            )
            if result.isError():
                print(f"[ERROR] Read failed: {result}")
            else:
                regs = result.registers
                if len(regs) < 6:
                    print(f"[ERROR] Not enough registers returned: {len(regs)}")
                else:
                    x_m = _registers_to_float(regs[0], regs[1])
                    y_m = _registers_to_float(regs[2], regs[3])
                    marker_id = int(regs[4])
                    valid = int(regs[5]) != 0
                    print(
                        f"X={x_m:.4f} m | Y={y_m:.4f} m | marker_id={marker_id} | "
                        f"valid={1 if valid else 0}"
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
