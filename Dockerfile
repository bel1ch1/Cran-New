FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Runtime packages for OpenCV/picamera2 integrations and process tooling.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    libcap2-bin \
    gcc \
    g++ \
    make \
    pkg-config \
    libc6-dev \
    libcap-dev \
    python3-dev \
    procps \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Raspberry Pi camera stack (libcamera + picamera2) for aarch64 builds on Pi OS.
RUN set -eux; \
    if [ "$(uname -m)" = "aarch64" ]; then \
      curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg; \
      echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.com/debian/ bookworm main" \
        > /etc/apt/sources.list.d/raspi.list; \
      apt-get update; \
      apt-get install -y --no-install-recommends \
        libcamera0.5 \
        libcamera-ipa \
        python3-libcamera \
        python3-picamera2 \
        gstreamer1.0-libcamera \
        libgstreamer1.0-0 \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good; \
      rm -rf /var/lib/apt/lists/*; \
    fi

# Apt installs libcamera/picamera2 for system Python; expose them to the venv.
ENV PYTHONPATH="/usr/lib/python3/dist-packages"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /app/
RUN uv venv --system-site-packages /app/.venv \
    && uv sync --frozen --no-dev --no-install-project --no-install-package picamera2

COPY . /app

# Create runtime directories upfront so shared bind mounts have known paths.
RUN mkdir -p /app/data/runtime

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
