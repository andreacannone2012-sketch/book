# Usa l'immagine ufficiale di Microsoft Playwright con Python
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copia e installa le dipendenze Python
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia il resto dell'applicazione
COPY . .
RUN mkdir -p /data/downloads

ENV DATA_DIR=/data \
    DOWNLOAD_WORKERS=2
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
