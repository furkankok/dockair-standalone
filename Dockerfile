# Temel imaj olarak hafif bir Python imajı kullanıyoruz
FROM python:3.12-slim

# Gerekli sistem paketlerini yüklüyoruz
RUN apt-get update && apt-get install -y \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Gerekli Python paketlerini yüklüyoruz
RUN pip install --no-cache-dir \
    docker \
    websockets \
    sentry-sdk

# Çalışma dizinine geçiyoruz
WORKDIR /app

# Script dosyasını konteynıra kopyalıyoruz
COPY . .

# Script'i çalıştırıyoruz
CMD ["python", "script.py"]
