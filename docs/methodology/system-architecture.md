# Архитектура системы

## Контуры

| Контур | Камера | Runtime | Modbus |
|--------|--------|---------|--------|
| **Мост / тележка (XY)** | CSI `/dev/video0` | `bridge_pose_modbus.py` | Сервер TCP `:5020`, регистры **100+** |
| **Крюк (Z)** | CSI `/dev/video1` | `hook_pose_modbus.py` | Клиент → тот же сервер, регистры **200+** |

Оба pose-скрипта запускаются через supervisor-обёртки (`run_*_pose_supervisor.py`) в Docker.

## Docker Compose (5 сервисов)

```
┌─────────────────────┐     WebSocket      ┌──────────────────────┐
│  calibration-app    │◄── калибровка ────►│  bridge-supervisor   │
│  :8000 UI + API     │                    │  Modbus server :5020 │
└─────────┬───────────┘                    └──────────┬───────────┘
          │ read Modbus                               │ write HR
          ▼                                           ▼
┌─────────────────────┐                    ┌──────────────────────┐
│  hook-supervisor    │── Modbus client ──►│  (общий datastore)   │
└─────────────────────┘                    └──────────────────────┘
          │
          ▼
┌─────────────────────┐     Flux write     ┌──────────────────────┐
│  pose-influx-writer │───────────────────►│  influxdb :8086      │
└─────────────────────┘                    └──────────────────────┘
```

| Сервис | Назначение |
|--------|------------|
| `calibration-app` | Web UI, API, WebSocket-калибровка, `/statistics` |
| `bridge-supervisor` | Pose моста + **единственный** Modbus TCP сервер |
| `hook-supervisor` | Pose крюка, запись в Modbus как клиент |
| `influxdb` | История pose для графиков на `/statistics` |
| `pose-influx-writer` | Опрос Modbus → запись в InfluxDB |

## Конфигурация

Единый файл **`data/calibration_config.json`** (не в git):

- `bridge_calibration` — карта landmarks, ROI, intrinsics камеры 0
- `hook_calibration` — ID и размер маркера крюка, intrinsics камеры 1
- `camera_intrinsics` — матрица камеры и дисторсия

Шаблон: `data/calibration_config.example.json`.

## Арbitration камер

При открытии WebSocket-калибровки:

1. Создаётся lock `data/runtime/calibration.lock`
2. Supervisor-контейнеры останавливают pose child-процессы
3. Камера освобождается для `calibration-app`

После закрытия калибровки lock снимается, pose-режим возобновляется.

## Ключевые модули кода

| Модуль | Роль |
|--------|------|
| `app/services/spatial_marker_map.py` | Карта landmarks, калибровка и runtime-match |
| `app/services/bridge_pose_estimator.py` | Детекция ArUco, fusion, фильтры pose моста |
| `app/services/calibration_algorithms.py` | Алгоритмы UI-калибровки bridge/hook |
| `app/services/pose_modbus_common.py` | Modbus server, encode/decode регистров |
| `app/services/camera_config.py` | Env-настройки pose и spatial |
