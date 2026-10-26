#!/usr/bin/env bash
set -euo pipefail

# GPU AMI의 가중치를 읽는다. 런타임에서 자동 다운로드하지 않는다.
for path in /opt/models/Qwen3-8B-FP8 "${WHISPER_MODEL_PATH}"; do
  if [[ ! -d "$path" ]]; then
    echo "모델 경로가 없습니다: $path" >&2
    exit 1
  fi
done

cd /opt/tritonserver/python/openai
python3 openai_frontend/main.py   --model-repository /models   --tokenizer /opt/models/Qwen3-8B-FP8   --model-control-mode explicit   --load-model qwen3_8b   --enable-kserve-frontends &
server_pid=$!
# 종료 신호를 서버에 전달하고 자식 프로세스를 회수한다.
cleanup() {
  kill -TERM "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# Qwen 로드 이후에 Whisper를 로드해 초기 GPU 메모리 경합을 줄인다.
python3 - <<'LOAD'
import time
import urllib.error
import urllib.request

deadline = time.monotonic() + 900
while True:
    try:
        with urllib.request.urlopen('http://127.0.0.1:9000/v1/models', timeout=2):
            break
    except (OSError, urllib.error.URLError):
        if time.monotonic() >= deadline:
            raise SystemExit('OpenAI frontend 준비 시간 초과')
        time.sleep(2)
request = urllib.request.Request(
    'http://127.0.0.1:9000/v1/models/whisper_stt/load', method='POST')
with urllib.request.urlopen(request, timeout=300):
    pass
LOAD
wait "$server_pid"
