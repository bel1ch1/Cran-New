# CRAN FastAPI Calibration App

FastAPI-приложение для промышленной калибровки крана на основе OpenCV ArUco.

## Запуск на пустом устройстве

Пошаговая инструкция для нового Raspberry Pi / Linux-хоста без предустановленной системы CRAN.

### 1. Что понадобится

**Аппаратура (типовая конфигурация):**
- Raspberry Pi 5 (или совместимый aarch64-хост с libcamera)
- 2× камеры Raspberry Pi Camera Module (CSI): мост и крюк
- SD-карта / eMMC с Raspberry Pi OS (Bookworm, 64-bit)
- Сеть (Ethernet или Wi‑Fi)

**ПО на хосте:**
- Docker Engine + Docker Compose plugin
- Git
- Доступ к камерам через `libcamera` / `picamera2`

> Для Jetson Nano используйте GStreamer-пайплайны (см. раздел «Переменные окружения») и запуск без picamera2.

### 2. Подготовка операционной системы

```bash
sudo apt-get update
sudo apt-get install -y git docker.io docker-compose-plugin

# Пользователь в группах docker и video (нужен перелогин)
sudo usermod -aG docker,video "$USER"
newgrp docker
```

Проверка Docker:

```bash
docker --version
docker compose version
```

### 3. Настройка камер (Raspberry Pi)

Подключите обе CSI-камеры и включите автодetect:

```bash
# Проверка, что камеры видны на хосте
rpicam-hello --list-cameras
```

В `/boot/firmware/config.txt` должно быть:

```ini
camera_auto_detect=1
```

Для Camera Module v2.1 (IMX219) **не** задавайте `dtoverlay=imx708` — это ломает захват.

После изменения конфига:

```bash
sudo reboot
```

После перезагрузки убедитесь, что устройства появились:

```bash
ls -l /dev/video*
# Ожидается /dev/video0 (мост), /dev/video1 (крюк)
```

### 4. Получение кода

```bash
git clone <URL-репозитория> /home/cran/Cran-New
cd /home/cran/Cran-New
```

Каталог `data/` создаётся автоматически при первом запуске. Файл `data/calibration_config.json` будет сгенерирован с дефолтными значениями, если его нет.

### 5. Файл `.env`

Создайте `.env` в корне проекта:

```bash
cat > .env <<'EOF'
# Веб-интерфейс
CRAN_APP_PORT=8000
CRAN_AUTH_USER=admin
CRAN_AUTH_PASSWORD=admin
CRAN_SESSION_SECRET=change-this-in-production

# Камеры (Raspberry Pi + picamera2)
CRAN_CAMERA_BACKEND=picamera2
CRAN_BRIDGE_CAMERA_DEVICE=/dev/video0
CRAN_HOOK_CAMERA_DEVICE=/dev/video1

# Modbus (порт на хосте)
CRAN_MODBUS_PORT=5020
CRAN_MODBUS_PUBLISHED_PORT=5020
CRAN_MODBUS_UNIT_ID=1
CRAN_MODBUS_BRIDGE_BASE_REGISTER=100
CRAN_MODBUS_HOOK_BASE_REGISTER=200

# Опционально: InfluxDB для истории на /statistics
# CRAN_INFLUX_URL=http://127.0.0.1:8086
# CRAN_INFLUX_ORG=cran
# CRAN_INFLUX_BUCKET=cran
# CRAN_INFLUX_TOKEN=your-token
EOF
```

> **Важно:** смените `CRAN_AUTH_PASSWORD` и `CRAN_SESSION_SECRET` перед эксплуатацией.

### 6. Первый запуск Docker Compose

```bash
cd /home/cran/Cran-New
docker compose up -d --build
docker compose ps
```

Ожидаются три контейнера в статусе `running` / `healthy`:

| Контейнер | Назначение |
|-----------|------------|
| `cran_calibration_app` | Web UI + API + калибровка |
| `cran_bridge_supervisor` | Pose-мост + Modbus TCP сервер |
| `cran_hook_supervisor` | Pose-крюк (клиент Modbus) |

Просмотр логов:

```bash
docker compose logs -f calibration-app
docker compose logs -f bridge-supervisor
docker compose logs -f hook-supervisor
```

### 7. Проверка после старта

**Web UI:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/login
# Ожидается 200
```

Откройте в браузере: `http://<IP-устройства>:8000`  
Логин / пароль — из `.env` (по умолчанию `admin` / `admin`).

**Modbus (после XY-калибровки):**

```bash
python3 modbus_pose_reader_test.py --host 127.0.0.1 --port 5020 --unit-id 1 --base-register 100
```

На пустом конфиге pose-скрипты могут не публиковать валидные данные, пока не выполнена калибровка — это нормально.

**Конфликт порта 5020:**

```bash
sudo ss -tlnp | grep 5020
# При занятом порте измените CRAN_MODBUS_PUBLISHED_PORT в .env, например 15020
docker compose up -d
```

### 8. Первичная калибровка (новое устройство)

1. **XY (мост):** `/xy-settings` → задайте размер маркера, **ID опорного ArUco** и сдвиг нулевой точки → «Начать калибровку».
2. Запустите видеопоток, затем «Начать калибровку». Двигайте мост/тележку вдоль пути — карта точек строится по координатам (система доверия).
3. «Завершить калибровку» — данные сохраняются в `data/calibration_config.json`.
4. **Z (крюк):** `/z-settings` → размер и ID маркера → `/z-calib`.
5. После сохранения конфига supervisor-контейнеры автоматически подхватят pose-режим.

Подробнее о логике XY-калибровки — в разделе «Калибровка XY».

### 9. Автозапуск после перезагрузки

Контейнеры используют `restart: always` — после reboot хоста Docker поднимет стек сам, если Docker включён:

```bash
sudo systemctl enable docker
```

Опционально — аппаратный watchdog (перезагрузка хоста при зависании контейнеров): см. `ops/watchdog/README.md`.

### 10. Локальный запуск без Docker (разработка)

На Raspberry Pi для `picamera2` нужен venv с системными пакетами:

```bash
sudo apt-get install -y python3-libcamera python3-picamera2
curl -LsSf https://astral.sh/uv/install.sh | sh

cd /home/cran/Cran-New
uv venv --system-site-packages .venv
uv sync --frozen --no-dev --no-install-package picamera2

export PYTHONPATH=/usr/lib/python3/dist-packages
export CRAN_CAMERA_BACKEND=picamera2
export CRAN_BRIDGE_CAMERA_DEVICE=/dev/video0
export CRAN_HOOK_CAMERA_DEVICE=/dev/video1

uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Pose-скрипты в отдельных терминалах:

```bash
uv run python run_bridge_pose_supervisor.py -- --modbus-host 0.0.0.0 --modbus-port 5020 --modbus-base-register 100
uv run python run_hook_pose_supervisor.py -- --modbus-host 127.0.0.1 --modbus-port 5020 --modbus-base-register 200
```

### 11. Типовые проблемы на чистом устройстве

| Симптом | Что проверить |
|---------|----------------|
| Камеры не видны в контейнере | `privileged: true`, группа `video`, `rpicam-hello --list-cameras` на хосте |
| Чёрный экран в калибровке | `CRAN_CAMERA_BACKEND=picamera2`, правильные `/dev/video0` и `/dev/video1` |
| `imx708` / wrong sensor | `camera_auto_detect=1` в config.txt, перезагрузка |
| Порт 5020 занят | `CRAN_MODBUS_PUBLISHED_PORT=15020` |
| Pose не стартует | Конфиг не откалиброван — выполните XY и Z калибровку |
| Нет доступа к Docker | `sudo usermod -aG docker $USER` и перелогин |

---

## Быстрый запуск (если окружение уже настроено)

```bash
docker compose up -d --build
docker compose ps
```

Остановка:

```bash
docker compose down
```

## Запуск в Docker Compose (multi-container)

В репозитории:

- `Dockerfile` (общий образ для app и supervisor-сервисов);
- `docker-compose.yml` с сервисами:
  - `calibration-app`,
  - `bridge-supervisor`,
  - `hook-supervisor`.

Все сервисы работают с `restart: always`, поэтому контейнеры автоматически поднимаются после падений/перезагрузки Docker daemon.

## Доступ

- Логин: `admin`
- Пароль: `admin`

Можно переопределить через переменные окружения:

- `CRAN_APP_PORT` (порт Web UI на хосте, по умолчанию `8000`)
- `CRAN_AUTH_USER`
- `CRAN_AUTH_PASSWORD`
- `CRAN_SESSION_SECRET`
- `CRAN_CAMERA_BACKEND` (`picamera2`, `v4l2`, `gstreamer`, `jetson`, `auto`; для Pi 5 — `picamera2`)
- `CRAN_USE_JETSON_CAMERAS` (`true/false`, legacy)
- `CRAN_BRIDGE_CAMERA_DEVICE` (по умолчанию `/dev/video0` в Docker, `0` в bare-metal)
- `CRAN_HOOK_CAMERA_DEVICE` (по умолчанию `/dev/video1` в Docker, `1` в bare-metal)
- `CRAN_BRIDGE_CAMERA_PIPELINE` (опционально, кастомный GStreamer pipeline)
- `CRAN_HOOK_CAMERA_PIPELINE` (опционально, кастомный GStreamer pipeline)
- `CRAN_MODBUS_HOST` (по умолчанию `127.0.0.1`)
- `CRAN_MODBUS_PORT` (по умолчанию `5020`)
- `CRAN_MODBUS_PUBLISHED_PORT` (порт на хосте для публикации Modbus из контейнера; можно изменить при конфликте, например `15020`)
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
- `app/services/spatial_marker_map.py` - карта точек XY и система доверия.
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

Текущая реализация моста строит **карту точек по координатам X** (не по ArUco ID): опорный маркер задаёт ноль, остальные точки подтверждаются системой доверия (повторные наблюдения в одной позиции). См. `app/services/spatial_marker_map.py`.

## Калибровка XY

1. `/xy-settings` — размер маркера, **ID опорного ArUco**, сдвиг нулевой точки (м).
2. `/xy-calib-1920x1080` — видеопоток, «Начать калибровку», движение вдоль пути, «Завершить калибровку».
3. `/calibration-complete` — просмотр и сохранение карты точек в `data/calibration_config.json`.

API:

- `GET /xy-marker-settings` — текущие настройки моста.
- `POST /xy-marker-settings` — сохранить `marker_size`, `reference_marker_id`, `zero_marker_offset_m`.
- `GET /calibration-data` — данные для страницы результатов.
- `POST /save-calibration` — запись карты в JSON.

WebSocket: `ws://<host>/ws/calibration/bridge`

## Standalone программа #1 (Jetson + Modbus)

В корне проекта добавлен скрипт `bridge_pose_modbus.py`. Скрипт:

- читает `data/calibration_config.json` (блок `bridge_calibration`);
- использует `roi` для ускоренного поиска ArUco-маркеров;
- сопоставляет детекции с **подтверждёнными точками карты по координате X** (не по ArUco ID);
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
/home/cran/cran/venv/bin/python run_bridge_pose_supervisor.py -- --use-gstreamer --modbus-host 0.0.0.0 --modbus-port 5020 --modbus-base-register 100
/home/cran/cran/venv/bin/python run_hook_pose_supervisor.py -- --use-gstreamer --modbus-host 127.0.0.1 --modbus-port 5020 --modbus-base-register 200
```

Supervisor автоматически перезапускает дочерний процесс после любого завершения.
PID-файлы пишутся в `data/runtime/`.

Дополнительно:

- поддерживается lock-файл калибровки (по умолчанию `data/runtime/calibration.lock`);
- при активном lock supervisor останавливает дочерний pose-процесс и ждет снятия lock;
- после снятия lock дочерний процесс запускается снова автоматически;
- supervisor пишет heartbeat-файл:
  - `data/runtime/bridge_pose_supervisor.heartbeat`,
  - `data/runtime/hook_pose_supervisor.heartbeat`.
- по умолчанию supervisor использует Python из `/home/cran/cran/venv/bin/python` (если найден).

## Освобождение камер для калибровки

При первом вызове `tick()` в `BridgeCalibrationRuntime` и `HookCalibrationRuntime`
приложение вызывает `stop_pose_supervisor_scripts()` и создает lock-файл.
Supervisor-контейнеры реагируют на lock: останавливают child-процессы и освобождают камеры.

После завершения калибровки (закрытие runtime/websocket) вызывается
`ensure_pose_supervisor_scripts_running()`, lock удаляется, и supervisor-контейнеры
снова поднимают child-процессы.

При старте FastAPI-приложения lock также синхронизируется автоматически:
если в системе остался старый lock после аварийного прерывания калибровки,
он будет снят, и штатные программы продолжат работу по умолчанию.
Перед снятием lock выполняется проверка валидности конфигурации через
runtime-loader штатных скриптов (`bridge` + `hook`); если конфиг не готов,
штатный режим не включается до исправления калибровочных данных.

В Docker-режиме используется тот же контракт функций, но через lock-механизм:

- `stop_pose_supervisor_scripts()` создает lock-файл;
- `ensure_pose_supervisor_scripts_running()` удаляет lock-файл;
- supervisor-контейнеры сами реагируют на lock и освобождают камеры.

Это исключает конкуренцию за камеры во время активной калибровки.

## Переменные окружения для recovery и camera arbitration

- `CRAN_SUPERVISOR_LOCK_FILE` (путь к lock-файлу, общий volume для app/supervisors);
- `CRAN_SUPERVISOR_LOCK_POLL_INTERVAL` (частота опроса lock);
- `CRAN_SUPERVISOR_HEARTBEAT_INTERVAL` (период записи heartbeat);
- `CRAN_SUPERVISOR_RESTART_BACKOFF_MAX` (верхняя граница backoff рестартов);
- `CRAN_BRIDGE_RESTART_DELAY`, `CRAN_HOOK_RESTART_DELAY` (базовая задержка рестарта);
- `CRAN_POSE_RELEASE_TIMEOUT_S` (сколько ждать освобождения камер после установки lock, по умолчанию `5.0`);
- `CRAN_BRIDGE_DEVICE_PATH`, `CRAN_HOOK_DEVICE_PATH` (устройства камеры для Compose, например `/dev/video0`, `/dev/video1`).

## Проверка стабильности после внедрения

1. Поднимите стек: `docker compose up -d --build`.
2. Убедитесь, что сервисы живы: `docker compose ps`.
3. Проверьте перезапуск child-процесса:
   - `docker compose exec bridge-supervisor pkill -f bridge_pose_modbus.py`
   - `docker compose exec hook-supervisor pkill -f hook_pose_modbus.py`
   - supervisor должен автоматически поднять child снова.
4. Откройте WebSocket калибровки из UI:
   - должен появиться `data/runtime/calibration.lock`;
   - pose child-процессы должны быть остановлены (камеры освобождены).
5. Завершите калибровку:
   - lock-файл удаляется;
   - child-процессы автоматически возобновляются.

## Аппаратный Linux watchdog (reboot хоста при зависании)

Для защиты от зависаний на уровне устройства добавлены файлы:

- `ops/watchdog/docker_health_guard.sh`
- `ops/watchdog/watchdog.conf`
- `ops/watchdog/README.md`

Сценарий: watchdog daemon периодически вызывает `docker_health_guard.sh`.
Если контейнеры `cran_calibration_app`, `cran_bridge_supervisor`, `cran_hook_supervisor`
долго не `healthy`, скрипт перестает подтверждать "здоровье", и аппаратный watchdog
перезагружает хост.

## Статистика Modbus в UI

Страница `/statistics` теперь показывает live-значения из общего Modbus:

- `Bridge`: `X`, `Y`, `marker_id`, `valid`;
- `Hook`: `distance`, `deviation_x_px`, `deviation_y_px`, `marker_id`, `valid`.

Данные запрашиваются через API `GET /statistics/modbus-pose` (требуется авторизация).
Исторические точки для графиков запрашиваются через `GET /statistics/modbus-history`.

Если InfluxDB не настроен или недоступен, страница работает в fallback-режиме:
- карточки значений продолжают обновляться из Modbus;
- графики строятся по live-потоку без исторической выборки.

## Переменные окружения (полный список)

См. также раздел «Доступ» выше и `.env` в инструкции «Запуск на пустом устройстве».

- `CRAN_CAMERA_BACKEND` — `picamera2`, `v4l2`, `gstreamer`, `jetson`, `auto`
- `CRAN_MIN_TRUST_HITS`, `CRAN_MAX_TRUST_SIGMA_M` — параметры системы доверия XY-калибровки
