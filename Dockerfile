# syntax=docker/dockerfile:1
FROM runpod/base:1.3.1-cuda1281-ubuntu2204

ENV SYSTEM_VERSION_COMPAT=0 \
    OMP_NUM_THREADS=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf $(which python3) /usr/local/bin/python

COPY requirements-serverless.txt /
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r /requirements-serverless.txt

COPY . /facefusion
WORKDIR /facefusion

CMD ["python", "-u", "handler.py"]
