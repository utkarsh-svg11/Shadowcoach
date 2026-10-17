FROM python:3.11-slim-bookworm

# ffmpeg for audio, libssl/libasound for the Azure Speech SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libssl3 libasound2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static

ENV DATA_DIR=/data
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
