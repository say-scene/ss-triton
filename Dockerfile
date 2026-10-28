# CPU 전용 Whisper STT 이미지. 빌드: docker build -t ss-stt .
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY whisper_server.py .

# 모델은 기동 시 HF에서 받는다. 모델 비교를 위해 이미지에 굽지 않는다.
ENV STT_HOST=0.0.0.0 \
    WHISPER_MODEL=small.en \
    WHISPER_CPU_THREADS=2 \
    HF_HOME=/cache/huggingface \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --no-create-home stt && mkdir -p /cache && chown stt /cache
USER 10001
EXPOSE 8001
CMD ["python", "whisper_server.py"]
