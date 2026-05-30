# Modbus TCP: протокол pose-данных

## Топология

- **Сервер:** `bridge-supervisor` (`bridge_pose_modbus.py`), порт `CRAN_MODBUS_PORT` (5020).
- **Клиенты:**
  - `hook-supervisor` — запись hook-регистров;
  - `calibration-app` — чтение для `/statistics`;
  - `pose-influx-writer` — опрос для InfluxDB;
  - внешний PLC / SCADA.

Unit ID: `CRAN_MODBUS_UNIT_ID` (по умолчанию **1**).

## Кодирование float32

Два последовательных **holding register** (FC03), big-endian float32:

- Реализация: `app/services/pymodbus_compat.py` (`ModbusClientMixin.convert_to_registers`).
- pymodbus 3.6: параметр **`slave=`** (не `device_id=`).

## Bridge pose — base `CRAN_MODBUS_BRIDGE_BASE_REGISTER` (100)

| Offset | Тип | Поле |
|--------|-----|------|
| +0, +1 | float32 BE | `camera_x_m` |
| +2, +3 | float32 BE | `distance_m` |
| +4 | uint16 | `marker_id` |
| +5 | uint16 | `valid` (1 = valid) |

Константа: `BRIDGE_POSE_REGISTER_COUNT = 6`.

## Hook pose — base `CRAN_MODBUS_HOOK_BASE_REGISTER` (200)

| Offset | Тип | Поле |
|--------|-----|------|
| +0, +1 | float32 BE | `distance_m` |
| +2, +3 | float32 BE | `deviation_x_px` |
| +4, +5 | float32 BE | `deviation_y_px` |
| +6 | uint16 | `marker_id` |
| +7 | uint16 | `valid` |

Константа: `HOOK_POSE_REGISTER_COUNT = 8`.

## Проверка с хоста

```bash
python modbus_pose_reader_test.py --host 127.0.0.1 --port 5020 --unit-id 1 --base-register 100 --once
```

Из контейнера calibration-app Modbus host: `bridge-supervisor` (Docker network).

## API UI

```
GET /statistics/modbus-pose   # live snapshot (auth)
GET /statistics/modbus-history  # InfluxDB, fallback на live-only
```

## Поведение при invalid pose

- Bridge пишет `valid=0`, координаты могут быть 0 или **удержание последнего valid** (если `CRAN_POSE_HOLD_LAST=1`).
- Dashboard показывает `—` для метрик при `valid=false`.
