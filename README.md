# ss-triton

> **이름은 ss-triton이지만 내용은 CPU Whisper STT 서버다.**
> GPU quota 미승인으로 Triton(Qwen3-8B FP8 + Whisper GPU) 구성을 걷어냈다. 원본은 tag `triton-gpu-v1`.
> 레포·ECR·IAM·CI·ss-k8s 경로(`apps/ss-triton`)를 그대로 쓰려고 이름만 유지한다. LLM은 vLLM 공식 이미지(ss-k8s `apps/llm`)로 따로 서빙한다.

## 구성

| 파일 | 역할 |
|---|---|
| `whisper_server.py` | faster-whisper(CPU, int8) + KServe v2 형식 HTTP 서버. ss-be `SttController` 계약 `POST /v2/models/whisper_stt/infer` |
| `Dockerfile` | `python:3.12-slim` 기반 이미지. 모델은 기동 시 HF에서 받는다 |
| `.github/workflows/ecr-push.yml` | main push 시 `ss-triton:<SHA7>` ECR push → ss-k8s `apps/ss-triton` 태그 갱신 |

## 엔드포인트 (포트 8001)

| 경로 | 내용 |
|---|---|
| `POST /v2/models/whisper_stt/infer` | 입력 `AUDIO`(base64, BYTES, shape [1]) → 출력 `TEXT` |
| `GET /health` | 모델 로드 후 200 |
| `GET /metrics` | Prometheus 지표 `stt_requests_total`, `stt_queue_wait_seconds`, `stt_inference_seconds`, `stt_audio_seconds` |

## 환경변수

| 변수 | 이미지 기본값 | 설명 |
|---|---|---|
| `WHISPER_MODEL` | `small.en` | 비교 시 `large-v3-turbo` 등으로 바꾼다 |
| `WHISPER_CPU_THREADS` | `2` | CPU limit과 맞춘다 |
| `STT_CONCURRENCY` | `1` | 동시 계산 수(faster-whisper `num_workers`) |
| `STT_QUEUE_TIMEOUT` | `10` | 슬롯 대기 상한(초). 넘으면 503 `STT_BUSY` |
| `STT_HOST` | `0.0.0.0` | 스크립트 직접 실행 시 기본값은 `127.0.0.1` |

## 로컬 실행

```bash
docker build -t ss-stt:local .
docker run --rm --cpus 2 -p 8001:8001 ss-stt:local
```

컨테이너 없이 실행하면 기본 모델은 `large-v3-turbo`, 주소는 `127.0.0.1:8001`이다.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python whisper_server.py
```
