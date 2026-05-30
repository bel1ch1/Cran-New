from __future__ import annotations

import struct

from pymodbus.client import ModbusTcpClient


def float_to_holding_registers(value: float) -> tuple[int, int]:
    packed = struct.pack(">f", float(value))
    hi, lo = struct.unpack(">HH", packed)
    return int(hi) & 0xFFFF, int(lo) & 0xFFFF


def write_registers_compat(
    client: ModbusTcpClient,
    *,
    address: int,
    values: list[int],
    unit_id: int,
) -> None:
    call_variants = [
        {"address": address, "values": values, "slave": unit_id},
        {"address": address, "values": values, "unit": unit_id},
        {"address": address, "values": values, "device_id": unit_id},
        {"address": address, "values": values},
    ]
    last_error: Exception | None = None
    for kwargs in call_variants:
        try:
            response = client.write_registers(**kwargs)
            if response.isError():
                continue
            return
        except TypeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to call write_registers")
