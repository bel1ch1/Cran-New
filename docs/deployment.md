# Развёртывание CRAN на устройстве

Пошаговый гайд для Raspberry Pi 5 (или совместимого aarch64-хоста с libcamera). Краткая версия — в [README](../README.md#развёртывание).

## 1. Требования

### Аппаратура

- Raspberry Pi 5 (рекомендуется) или Linux aarch64 с libcamera
- 2× CSI камеры (мост + крюк), типично IMX219 → `/dev/video0`, `/dev/video1`
- Сеть (Ethernet / Wi‑Fi)
- SD/eMMC с **Raspberry Pi OS Bookworm 64-bit**

### ПО на хосте

```bash
sudo apt-get update
sudo apt-get install -y git docker.io docker-compose-plugin

sudo usermod -aG docker,video "$USER"
newgrp docker

docker --version
docker compose version
```

## 2. Камеры

```bash
rpicam-hello --list-cameras
```

В `/boot/firmware/config.txt`:

```ini
camera_auto_detect=1
```

Для IMX219 **не** используйте `dtoverlay=imx708`. После правок — `sudo reboot`.

Проверка:

```bash
ls -l /dev/video0 /dev/video1
```

## 3. Клонирование и конфиг

```bash
git clone <URL-репозитория> /home/cran/Cran-New
cd /home/cran/Cran-New

cp data/calibration_config.example.json data/calibration_config.json
cp .env.example .env
```

Отредактируйте `.env`:

- смените `CRAN_AUTH_PASSWORD` и `CRAN_SESSION_SECRET`;
- проверьте пути камер;
- при необходимости настройте pose/spatial (см. [pose-filtering-and-tuning.md](methodology/pose-filtering-and-tuning.md)).

## 4. Запуск стека

```bash
docker compose up -d --build
docker compose ps
```

Ожидаются **5 healthy** сервисов:

| Контейнер | Порт (хост) | Роль |
|-----------|-------------|------|
| `cran_calibration_app` | 8000 | Web UI |
| `cran_bridge_supervisor` | 5020 | Pose моста + Modbus server |
| `cran_hook_supervisor` | — | Pose крюка |
| `cran_influxdb` | 8086 | История |
| `cran_pose_influx_writer` | — | Modbus → Influx |

Логи:

```bash
docker compose logs -f calibration-app
docker compose logs -f bridge-supervisor
docker compose logs -f hook-supervisor
```

## 5. Проверка

### Web UI

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/login
```

Браузер: `http://<IP>:8000` — логин из `.env`.

### Modbus

```bash
python3 modbus_pose_reader_test.py --host 127.0.0.1 --port 5020 --unit-id 1 --base-register 100 --once
```

До XY-калибровки `valid=0` — нормально.

### Конфликт порта 5020

```bash
sudo ss -tlnp | grep 5020
```

В `.env`: `CRAN_MODBUS_PUBLISHED_PORT=15020`, затем `docker compose up -d`.

## 6. Первичная калибровка

1. **XY:** `/xy-settings` → размер маркера, ID опорного ArUco, сдвиг нуля.
2. `/xy-calib-1920x1080` → «Начать калибровку» → движение вдоль пути → «Завершить».
3. Сохранить карту на `/calibration-complete`.
4. **Z:** `/z-settings` → ID и размер маркера крюка → `/z-calib`.

Методология: [xy-spatial-calibration.md](methodology/xy-spatial-calibration.md).

После сохранения конфига supervisor-ы подхватят pose автоматически (lock снимается).

## 7. Статистика и InfluxDB

Страница `/statistics` — live Modbus + графики из InfluxDB.

Токен и bucket задаются в `.env` (`CRAN_INFLUX_*`). При первом `docker compose up` Influx инициализируется из переменных `CRAN_INFLUX_USERNAME/PASSWORD/TOKEN`.

## 8. Автозапуск

```bash
sudo systemctl enable docker
```

Контейнеры: `restart: always`. Опционально — [аппаратный watchdog](../ops/watchdog/README.md).

## 9. Обновление версии

```bash
cd /home/cran/Cran-New
git pull
docker compose build
docker compose up -d
```

`data/calibration_config.json` и `.env` сохраняются на хосте (volume `./data`).

## 10. Локальная разработка без Docker

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

Pose (отдельные терминалы):

```bash
uv run python run_bridge_pose_supervisor.py -- --modbus-host 0.0.0.0 --modbus-port 5020
uv run python run_hook_pose_supervisor.py -- --modbus-host 127.0.0.1 --modbus-port 5020
```

## 11. Типовые проблемы

| Симптом | Решение |
|---------|---------|
| Камеры не видны в контейнере | `privileged: true`, группа `video`, `rpicam-hello` на хосте |
| Чёрный экран в калибровке | Проверить lock: pose должен остановиться; логи `calibration-app` |
| `Device or resource busy` | Закройте калибровку или остановите pose: `docker compose stop bridge-supervisor hook-supervisor` |
| Pose не стартует | Выполните XY+Z калибровку, проверьте `data/calibration_config.json` |
| Modbus нули на `/statistics` | Маркеры не в кадре или `valid=0`; см. логи `bridge-supervisor` |
| WebSocket падает при калибровке | Проверьте логи на Python-ошибки в `spatial_marker_map` / runtime init |

## 12. Структура данных на диске

```
data/
  calibration_config.json    # рабочий конфиг (не в git)
  calibration_config.example.json
  runtime/
    calibration.lock         # активная калибровка
    *.heartbeat, *.pid       # supervisor / pose (не в git)
```
