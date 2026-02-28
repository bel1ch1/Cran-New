# Быстрая настройка для Raspberry Pi 5 + IMX219

## Шаг 1: Настройка системы

### Отредактируйте /boot/firmware/config.txt

```bash
sudo nano /boot/firmware/config.txt
```

Добавьте или измените следующие строки:

```
camera_auto_detect=0
dtoverlay=imx219,cam0
dtoverlay=imx219
```

**Важно:** После изменений обязательно перезагрузите систему!

```bash
sudo reboot
```

## Шаг 2: Установка зависимостей

### Вариант A: Picamera2 (РЕКОМЕНДУЕТСЯ)

```bash
# Установка системных пакетов
sudo apt update
sudo apt install -y python3-picamera2 python3-pip

# Установка Python зависимостей
cd /path/to/Cran-New
pip install -r requirements.txt
```

### Вариант B: GStreamer + libcamera

```bash
# Установка системных пакетов
sudo apt update
sudo apt install -y gstreamer1.0-libcamera gstreamer1.0-tools python3-pip

# Установка Python зависимостей
cd /path/to/Cran-New
pip install -r requirements.txt
```

## Шаг 3: Диагностика камер

Запустите диагностический скрипт:

```bash
python test_rpi_cameras.py
```

Скрипт покажет:
- Какие камеры обнаружены
- Какие методы доступа работают
- Рекомендуемый бэкенд для использования

## Шаг 4: Запуск приложения

### Метод 1: Picamera2 (рекомендуется)

```bash
export CRAN_CAMERA_BACKEND=rpi5_picamera2
export CRAN_BRIDGE_CAMERA_DEVICE="0"
export CRAN_HOOK_CAMERA_DEVICE="1"
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Метод 2: libcamera через GStreamer

```bash
export CRAN_CAMERA_BACKEND=rpi5_libcamera
export CRAN_BRIDGE_CAMERA_DEVICE="0"
export CRAN_HOOK_CAMERA_DEVICE="1"
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Метод 3: V4L2 (прямой доступ)

```bash
export CRAN_CAMERA_BACKEND=rpi5_v4l2
export CRAN_BRIDGE_CAMERA_DEVICE="0"
export CRAN_HOOK_CAMERA_DEVICE="1"
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Шаг 5: Проверка

Откройте в браузере:

```
http://<IP-адрес-Raspberry-Pi>:8000
```

Логин: `admin`  
Пароль: `admin`

## Решение проблем

### Камеры не обнаруживаются

```bash
# Проверьте список камер
rpicam-vid --list-cameras

# Проверьте устройства V4L2
ls -la /dev/video*

# Проверьте логи ядра
dmesg | grep imx219
```

### Черный экран / нет изображения

1. Попробуйте другой бэкенд (см. Шаг 4)
2. Уменьшите разрешение:
   ```bash
   export CRAN_RPI5_CAMERA_WIDTH=640
   export CRAN_RPI5_CAMERA_HEIGHT=480
   ```
3. Запустите с отладкой:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --log-level debug
   ```

### Ошибка "Permission denied"

```bash
# Добавьте пользователя в группу video
sudo usermod -a -G video $USER

# Перелогиньтесь или выполните
newgrp video
```

### Камера занята другим процессом

```bash
# Найдите процессы, использующие камеру
sudo lsof | grep video

# Или
sudo fuser /dev/video0
```

## Автозапуск при загрузке (опционально)

Создайте systemd сервис:

```bash
sudo nano /etc/systemd/system/cran-calibration.service
```

Содержимое:

```ini
[Unit]
Description=CRAN Calibration Service
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Cran-New
Environment="CRAN_CAMERA_BACKEND=rpi5_picamera2"
Environment="CRAN_BRIDGE_CAMERA_DEVICE=0"
Environment="CRAN_HOOK_CAMERA_DEVICE=1"
ExecStart=/usr/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Активируйте сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cran-calibration.service
sudo systemctl start cran-calibration.service

# Проверьте статус
sudo systemctl status cran-calibration.service
```

## Производительность

Для лучшей производительности:

1. Используйте `rpi5_picamera2` бэкенд
2. Установите разумное разрешение (1920x1080 или 1280x720)
3. Ограничьте framerate (10 fps обычно достаточно):
   ```bash
   export CRAN_RPI5_CAMERA_FRAMERATE=10/1
   ```

## Полезные команды

```bash
# Проверка температуры CPU
vcgencmd measure_temp

# Проверка памяти GPU
vcgencmd get_mem gpu

# Информация о камере
rpicam-hello --list-cameras

# Тестовый снимок
rpicam-still -o test.jpg

# Тестовое видео (5 секунд)
rpicam-vid -t 5000 -o test.h264
```
