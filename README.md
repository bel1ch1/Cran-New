# CRAN FastAPI Calibration App

FastAPI-приложение для промышленной калибровки на основе OpenCV ArUco.

## Запуск

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

По умолчанию приложение слушает `http://127.0.0.1:8000`. Для доступа по сети: `uvicorn main:app --host 0.0.0.0 --port 8000`.

### Запуск на Raspberry Pi 5 с двумя камерами (IMX219)

1. Убедитесь, что в `/boot/firmware/config.txt` задано: `camera_auto_detect=0`, `dtoverlay=imx219,cam0`, `dtoverlay=imx219`. Перезагрузка после правок обязательна.

2. Узнайте пути камер:
   ```bash
   rpicam-vid --list-cameras
   ```
   В выводе будут два пути в скобках (например для камер 0 и 1).

3. Задайте переменные окружения и запустите:
   ```bash
   export CRAN_CAMERA_BACKEND=rpi5_libcamera
   export CRAN_BRIDGE_CAMERA_DEVICE="/base/axi/pcie@1000120000/rp1/i2c@80000/imx219@10"
   export CRAN_HOOK_CAMERA_DEVICE="/base/axi/pcie@1000120000/rp1/i2c@88000/imx219@10"
   pip install -r requirements.txt
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```
   Пути подставьте из своего вывода `--list-cameras` (камера 0 — мост, камера 1 — крюк, или наоборот по вашему выбору).

4. В браузере откройте `http://<IP-адрес-Pi>:8000`, войдите (admin/admin) и откройте разделы калибровки моста и крюка — там будут потоки с камер.

Опционально: `CRAN_RPI5_CAMERA_WIDTH`, `CRAN_RPI5_CAMERA_HEIGHT`, `CRAN_RPI5_CAMERA_FRAMERATE` (по умолчанию 1920, 1080, 10/1).

## Доступ

- Логин: `admin`
- Пароль: `admin`

Можно переопределить через переменные окружения:

- `CRAN_AUTH_USER`
- `CRAN_AUTH_PASSWORD`
- `CRAN_SESSION_SECRET`
- `CRAN_USE_JETSON_CAMERAS` (`true/false`)
- `CRAN_BRIDGE_CAMERA_DEVICE` (по умолчанию `0`, `sensor-id` для моста)
- `CRAN_HOOK_CAMERA_DEVICE` (по умолчанию `1`, `sensor-id` для крюка)
- `CRAN_BRIDGE_CAMERA_PIPELINE` (опционально, кастомный GStreamer pipeline)
- `CRAN_HOOK_CAMERA_PIPELINE` (опционально, кастомный GStreamer pipeline)
- `CRAN_MODBUS_HOST` (по умолчанию `127.0.0.1`)
- `CRAN_MODBUS_PORT` (по умолчанию `5020`)
- `CRAN_MODBUS_UNIT_ID` (по умолчанию `1`)
- `CRAN_MODBUS_BRIDGE_BASE_REGISTER` (по умолчанию `100`)
- `CRAN_MODBUS_HOOK_BASE_REGISTER` (по умолчанию `200`)
- `CRAN_INFLUX_URL` (например `http://127.0.0.1:8086`)
- `CRAN_INFLUX_ORG`
- `CRAN_INFLUX_BUCKET`
- `CRAN_INFLUX_TOKEN`
- `CRAN_INFLUX_MEASUREMENT` (по умолчанию `crane_pose`)
- `CRAN_INFLUX_FIELD_BRIDGE_X` (по умолчанию `bridge_x_m`)
- `CRAN_INFLUX_FIELD_BRIDGE_Y` (по умолчанию `bridge_y_m`)
- `CRAN_INFLUX_FIELD_HOOK_DISTANCE` (по умолчанию `hook_distance_m`)

## Архитектура

- `app/main.py` - инициализация приложения, middleware, роутеры.
- `app/routers/auth.py` - аутентификация и выход.
- `app/routers/pages.py` - UI-страницы (меню, разделы калибровки, статистика, управление).
- `app/routers/api.py` - API настроек, сохранение JSON и WebSocket калибровки.
- `app/services/config_store.py` - работа с конфигурационным JSON.
- `app/services/control_service.py` - обработка команд управления.
- `app/services/calibration_algorithms.py` - интерфейсы и точки внедрения OpenCV ArUco алгоритмов.
- `app/services/calibration_runtime.py` - независимые runtime-процессы калибровки моста и крюка.
- `app/services/jetson_camera_provider.py` - точка подключения камеры Jetson Nano Dev Kit.
- `app/core/settings.py` - конфигурация приложения и пути.
- `templates/` - HTML шаблоны в промышленном стиле.
- `data/calibration_config.json` - итоговый конфиг калибровки.

## WebSocket контуры камер

- Мост/тележка: `ws://<host>/ws/calibration/bridge`
- Крюк: `ws://<host>/ws/calibration/hook`

Контуры независимы: пользователь может калибровать мост и крюк раздельно, данные по каждому контуру записываются в отдельные секции `calibration_config.json`.
Для Jetson используются две разные камеры: отдельная для `bridge` и отдельная для `hook`.
По умолчанию инициализация камер выполняется через GStreamer pipeline:

`nvarguscamerasrc sensor-id=<id> ! video/x-raw(memory:NVMM), width=1920, height=1080, framerate=29/1 ! nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink drop=1`

## Точки внедрения алгоритмов

- Алгоритм моста/тележки: `MockBridgeCalibrationAlgorithm` в `app/services/calibration_algorithms.py`.
- Алгоритм крюка: `MockHookCalibrationAlgorithm` в `app/services/calibration_algorithms.py`.
- Источник кадров Jetson: `JetsonCameraFrameProvider` в `app/services/jetson_camera_provider.py`.

Текущая реализация моста включает накопление парных/тройных наблюдений маркеров, подтверждение нового `id` по статистике и контроль монотонности `id`.

## Настройки XY

- `GET /xy-marker-settings` - получить текущий размер маркера из конфигурации.
- `POST /xy-marker-settings` - сохранить размер маркера (`marker_size`) в конфиг.

## Standalone программа #1 (Jetson + Modbus)

В корне проекта добавлен скрипт `bridge_pose_modbus.py`. Скрипт:

- читает `data/calibration_config.json` (блок `bridge_calibration`);
- использует `roi` для ускоренного поиска ArUco-маркеров;
- использует только маркеры, чьи `id` есть в `marker_positions_m`;
- вычисляет:
  - `X` - положение камеры по пути (метры),
  - `Y` - дистанцию до маркера (метры);
- учитывает `movement_direction` (`left_to_right` / `right_to_left`);
- поднимает свой Modbus TCP сервер и публикует данные в holding-регистры.

Запуск:

```bash
python bridge_pose_modbus.py --use-gstreamer --modbus-host 0.0.0.0 --modbus-port 5020 --modbus-base-register 100
```

Полезные параметры:

- `--config` путь до calibration JSON;
- `--camera-id` переопределяет `camera_id` из JSON;
- `--fps` частота обработки (по умолчанию `8`);
- `--modbus-unit-id` slave/unit id (по умолчанию `1`).

Проверка чтения (из второго терминала):

```bash
python modbus_pose_reader_test.py --host 127.0.0.1 --port 5020 --unit-id 1 --base-register 100
```

Карта регистров (начиная с `--modbus-base-register`):

- `+0..+1`: `X` как `float32` (Big Endian, два 16-bit регистра),
- `+2..+3`: `Y` как `float32` (Big Endian, два 16-bit регистра),
- `+4`: `marker_id`,
- `+5`: флаг валидности (`1` если найден подходящий маркер, иначе `0`).

## Standalone программа #2 (Hook + общий Modbus)

Добавлен скрипт `hook_pose_modbus.py`.

Что делает:

- читает `data/calibration_config.json` (блок `hook_calibration`);
- берет `marker_id`, `marker_size_mm`, `camera.camera_id`;
- ищет целевой ArUco-маркер;
- считает дистанцию до маркера с учетом отклонения от оси камеры;
- считает отклонения маркера от центра кадра по X/Y в пикселях;
- пишет данные в тот же общий Modbus TCP сервер.

Важно: используем **один сервер** для обоих контуров.

- `bridge_pose_modbus.py` поднимает общий Modbus сервер;
- `hook_pose_modbus.py` подключается к нему как клиент и пишет в свой диапазон регистров.

Рекомендуемый запуск:

1) Терминал 1 (общий сервер + bridge):

```bash
python bridge_pose_modbus.py --use-gstreamer --modbus-host 0.0.0.0 --modbus-port 5020 --modbus-base-register 100
```

2) Терминал 2 (hook в тот же сервер):

```bash
python hook_pose_modbus.py --use-gstreamer --modbus-host 127.0.0.1 --modbus-port 5020 --modbus-base-register 200
```

Карта регистров hook (от `--modbus-base-register`, по умолчанию `200`):

- `+0..+1`: `distance_m` как `float32` (Big Endian),
- `+2..+3`: `deviation_x_px` как `float32`,
- `+4..+5`: `deviation_y_px` как `float32`,
- `+6`: `marker_id`,
- `+7`: флаг валидности (`1`/`0`).

## Автоперезапуск скриптов

Для запуска с автоперезапуском добавлены supervisor-скрипты:

- `run_bridge_pose_supervisor.py` (следит за `bridge_pose_modbus.py`)
- `run_hook_pose_supervisor.py` (следит за `hook_pose_modbus.py`)

Примеры запуска:

```bash
python run_bridge_pose_supervisor.py -- --use-gstreamer --modbus-host 0.0.0.0 --modbus-port 5020 --modbus-base-register 100
python run_hook_pose_supervisor.py -- --use-gstreamer --modbus-host 127.0.0.1 --modbus-port 5020 --modbus-base-register 200
```

Supervisor автоматически перезапускает дочерний процесс после любого завершения.
PID-файлы пишутся в `data/runtime/`.

## Освобождение камер для калибровки

При первом вызове `tick()` в `BridgeCalibrationRuntime` и `HookCalibrationRuntime` приложение вызывает
`stop_pose_supervisor_scripts()` и останавливает:

- `bridge_pose_supervisor.pid`
- `hook_pose_supervisor.pid`
- `bridge_pose_modbus.pid`
- `hook_pose_modbus.pid`

Это сделано, чтобы процессы калибровки FastAPI могли безопасно захватить камеры.

После завершения калибровки (закрытие runtime/websocket) supervisor-скрипты запускаются автоматически снова.
Если одновременно запущены bridge и hook калибровки, автозапуск выполняется только после завершения обеих.

## Статистика Modbus в UI

Страница `/statistics` теперь показывает live-значения из общего Modbus:

- `Bridge`: `X`, `Y`, `marker_id`, `valid`;
- `Hook`: `distance`, `deviation_x_px`, `deviation_y_px`, `marker_id`, `valid`.

Данные запрашиваются через API `GET /statistics/modbus-pose` (требуется авторизация).
Исторические точки для графиков запрашиваются через `GET /statistics/modbus-history`.

Если InfluxDB не настроен или недоступен, страница работает в fallback-режиме:
- карточки значений продолжают обновляться из Modbus;
- графики строятся по live-потоку без исторической выборки.

