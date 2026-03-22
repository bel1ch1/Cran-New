FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

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
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY . /app

# Create runtime directories upfront so shared bind mounts have known paths.
RUN mkdir -p /app/data/runtime

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
