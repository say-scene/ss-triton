# CPU 전용 Whisper STT 이미지. 빌드: docker build -t ss-stt .
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# small.en(약 486MB)을 이미지에 넣는다. 코드보다 먼저 두어 코드만 바뀌면 이 레이어를 재사용한다.
# WHISPER_MODEL에 이 경로를 주면 기동 시 다운로드 없이 로드한다. 이름(예: large-v3-turbo)을 주면 기존처럼 HF에서 받는다.
RUN python -c "from faster_whisper import download_model; download_model('small.en', output_dir='/models/small.en')"
COPY whisper_server.py .

ENV STT_HOST=0.0.0.0 \
    WHISPER_MODEL=/models/small.en \
    WHISPER_CPU_THREADS=2 \
    HF_HOME=/cache/huggingface \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --no-create-home stt && mkdir -p /cache && chown stt /cache
USER 10001
EXPOSE 8001
CMD ["python", "whisper_server.py"]
