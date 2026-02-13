# CRAN FastAPI Calibration App

FastAPI-приложение для промышленной калибровки на основе OpenCV ArUco.

## Запуск

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Доступ

- Логин: `admin`
- Пароль: `admin`

Можно переопределить через переменные окружения:

- `CRAN_AUTH_USER`
- `CRAN_AUTH_PASSWORD`
- `CRAN_SESSION_SECRET`
- `CRAN_USE_JETSON_CAMERAS` (`true/false`)
- `CRAN_BRIDGE_CAMERA_DEVICE` (по умолчанию `0`, то есть `cv2.VideoCapture(0)`)
- `CRAN_HOOK_CAMERA_DEVICE` (по умолчанию `0`, можно поставить `1` для второй камеры)
- `CRAN_BRIDGE_CAMERA_PIPELINE` (опционально, GStreamer)
- `CRAN_HOOK_CAMERA_PIPELINE` (опционально, GStreamer)

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
На обычном ПК кадры берутся стандартно через `cv2.VideoCapture(<device>)`.

## Точки внедрения алгоритмов

- Алгоритм моста/тележки: `MockBridgeCalibrationAlgorithm` в `app/services/calibration_algorithms.py`.
- Алгоритм крюка: `MockHookCalibrationAlgorithm` в `app/services/calibration_algorithms.py`.
- Источник кадров Jetson: `JetsonCameraFrameProvider` в `app/services/jetson_camera_provider.py`.

Текущая реализация моста включает накопление парных/тройных наблюдений маркеров, подтверждение нового `id` по статистике и контроль монотонности `id`.

## Настройки XY

- `GET /xy-marker-settings` - получить текущий размер маркера из конфигурации.
- `POST /xy-marker-settings` - сохранить размер маркера (`marker_size`) в конфиг.

