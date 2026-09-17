# syntax=docker/dockerfile:1
FROM runpod/base:1.0.2-cuda1280-ubuntu2204

ENV SYSTEM_VERSION_COMPAT=0 \
    OMP_NUM_THREADS=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-serverless.txt /
RUN python3.10 -m pip install --upgrade pip \
    && python3.10 -m pip install --no-cache-dir -r /requirements-serverless.txt

COPY . /facefusion
WORKDIR /facefusion

CMD ["python3.10", "-u", "handler.py"]
