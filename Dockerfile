FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    docker \
    websockets \
    sentry-sdk

WORKDIR /app

COPY . .

CMD ["python", "script.py"]
