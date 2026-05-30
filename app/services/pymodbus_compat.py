from __future__ import annotations

import math
import struct

from pymodbus.client import ModbusTcpClient

try:
    from pymodbus.client.mixin import ModbusClientMixin
except ImportError:
    ModbusClientMixin = None


def _finite_float(value: float | int) -> float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    return number


def float_to_holding_registers(value: float | int) -> tuple[int, int]:
    """Encode float32 as two big-endian uint16 holding registers (pymodbus 3.x compatible)."""
    if ModbusClientMixin is not None:
        registers = ModbusClientMixin.convert_to_registers(
            _finite_float(value),
            ModbusClientMixin.DATATYPE.FLOAT32,
        )
        return int(registers[0]) & 0xFFFF, int(registers[1]) & 0xFFFF

    packed = struct.pack(">f", _finite_float(value))
    hi, lo = struct.unpack(">HH", packed)
    return int(hi) & 0xFFFF, int(lo) & 0xFFFF


def holding_registers_to_float(hi: int, lo: int) -> float:
    """Decode two big-endian uint16 holding registers to float32."""
    hi_u = int(hi) & 0xFFFF
    lo_u = int(lo) & 0xFFFF
    if ModbusClientMixin is not None:
        value = ModbusClientMixin.convert_from_registers(
            [hi_u, lo_u],
            ModbusClientMixin.DATATYPE.FLOAT32,
        )
        number = float(value)
        return number if math.isfinite(number) else 0.0

    packed = struct.pack(">HH", hi_u, lo_u)
    number = float(struct.unpack(">f", packed)[0])
    return number if math.isfinite(number) else 0.0


def coerce_register_list(values: list[int | float | bool]) -> list[int]:
    """Normalize values for pymodbus datastore setValues (uint16 list)."""
    return [int(value) & 0xFFFF for value in values]


def write_registers_compat(
    client: ModbusTcpClient,
    *,
    address: int,
    values: list[int],
    unit_id: int,
) -> None:
    payload = coerce_register_list(values)
    call_variants = [
        {"address": address, "values": payload, "slave": unit_id},
        {"address": address, "values": payload, "device_id": unit_id},
        {"address": address, "values": payload},
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


def read_holding_registers_compat(
    client: ModbusTcpClient,
    *,
    address: int,
    count: int,
    unit_id: int,
):
    call_variants = [
        {"address": address, "count": count, "slave": unit_id},
        {"address": address, "count": count, "device_id": unit_id},
        {"address": address, "count": count},
    ]
    last_error: Exception | None = None
    for kwargs in call_variants:
        try:
            response = client.read_holding_registers(**kwargs)
            if response.isError():
                continue
            return response
        except TypeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to call read_holding_registers")
