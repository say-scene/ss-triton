# ss-triton

> **이름은 ss-triton이지만 내용은 CPU Whisper STT 서버다.**
> GPU quota 미승인으로 Triton(Qwen3-8B FP8 + Whisper GPU) 구성을 걷어냈다. 원본은 tag `triton-gpu-v1`.
> 레포·ECR·IAM·CI·ss-k8s 경로(`apps/ss-triton`)를 그대로 쓰려고 이름만 유지한다. LLM은 로컬 GPU 워커의 vLLM으로 분리했다([ss-k8s `apps/ss-vllm`](https://github.com/say-scene/ss-k8s/tree/main/apps/ss-vllm)).

## 구성

| 파일 | 역할 |
|---|---|
| `whisper_server.py` | faster-whisper(CPU, int8) + KServe v2 형식 HTTP 서버. ss-be `SttController` 계약 `POST /v2/models/whisper_stt/infer` |
| `Dockerfile` | `python:3.12-slim` 기반 이미지. 빌드 시 `small.en`을 `/models/small.en`에 포함 |
| `requirements.txt` | faster-whisper·PyAV·Prometheus client 의존성 |
| `.github/workflows/ecr-push.yml` | main push 시 `ss-triton:<SHA7>` ECR push → ss-k8s `apps/ss-triton` 태그 갱신 |

## 엔드포인트 (포트 8001)

| 경로 | 내용 |
|---|---|
| `POST /v2/models/whisper_stt/infer` | 입력 `AUDIO`(base64, BYTES, shape [1]) → 출력 `TEXT` |
| `GET /health` | 모델 로드 후 200 |
| `GET /metrics` | Prometheus 지표 `stt_requests_total`, `stt_queue_wait_seconds`, `stt_inference_seconds`, `stt_audio_seconds` |

Python `ThreadingHTTPServer`로 KServe v2 형식의 계약만 제공한다. Triton runtime이나 OpenAI LLM API는 실행하지 않는다.

- 원본 오디오는 **2MiB 이하**, 디코딩한 길이는 **0.5~31초**를 허용한다. FE 녹음 상한은 30초다.
- 영어 전사, VAD, temperature 0, `condition_on_previous_text=False`, beam size 1을 사용한다.
- 대기 상한 초과는 `503 STT_BUSY`, 잘못된 오디오는 `422 INVALID_AUDIO`, 크기 초과는 `413 AUDIO_TOO_LARGE`다.
- 오디오·발화 원문을 저장하거나 로그에 남기지 않는다. 모델명·음성 길이·처리시간·RTF만 출력한다.
- 인증·TLS 서버가 아니므로 내부 Service로 연결한다. 브라우저는 ss-be의 `/api/v1/stt`를 사용한다.

## 환경변수

| 변수 | 이미지 기본값 | 설명 |
|---|---|---|
| `WHISPER_MODEL` | `/models/small.en` | 이미지에 넣은 `small.en` 경로. 이름(예: `large-v3-turbo`)을 주면 기동 시 HF에서 받는다 |
| `WHISPER_CPU_THREADS` | `2` | CPU limit과 맞춘다 |
| `STT_CONCURRENCY` | `1` | 동시 계산 수(faster-whisper `num_workers`) |
| `STT_QUEUE_TIMEOUT` | `10` | 슬롯 대기 상한(초). 넘으면 503 `STT_BUSY` |
| `STT_HOST` | `0.0.0.0` | 스크립트 직접 실행 시 기본값은 `127.0.0.1` |
| `HF_HOME` | `/cache/huggingface` | 모델 이름으로 다운로드할 때 사용하는 캐시 |

이미지 기본 모델은 기동 시 다운로드 없이 로드한다. `WHISPER_MODEL`에 다른 모델 이름을 주면 Hugging Face 연결과 다운로드가 필요하다. `/metrics`의 처리시간·대기시간과 `result`별 요청 수는 관측 및 KEDA 확장의 입력이다.

## 로컬 실행

```bash
docker build -t ss-stt:local .
docker run --rm --cpus 2 -p 8001:8001 ss-stt:local
```

Docker가 필요하며 첫 빌드에서 의존성과 모델을 다운로드한다. 컨테이너 없이 실행하려면 Python 3.12 환경을 준비한다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
WHISPER_MODEL=small.en WHISPER_CPU_THREADS=2 .venv/bin/python whisper_server.py
```

위 명령은 이미지와 같은 모델을 선택한다. 환경변수 없이 직접 실행하면 기본 모델은 `large-v3-turbo`, CPU threads는 4, 주소는 `127.0.0.1:8001`이다. 모델 로드가 끝난 후 포트를 연다.

## 배포

- main push 시 CI가 Python 구문을 검사하고 `linux/amd64` 이미지를 ECR `ss-triton:<SHA7>`로 push한다. GPU는 빌드에 필요하지 않다.
- 이후 GitHub App이 ss-k8s의 `apps/ss-triton/kustomization.yaml` 이미지 태그를 갱신한다. Argo CD Sync와 Pod rollout은 별도 단계다.
- 현재 매니페스트는 전용 Karpenter `t3.xlarge` 노드에 Pod당 CPU `3400m`·메모리 `2Gi`를 배정한다. KEDA가 처리·대기 부하와 busy 비율을 보고 1~2개로 확장한다.
- 코드 구문만 확인하려면 `python3 -m py_compile whisper_server.py`를 실행한다. 실제 전사 품질과 지연은 모델을 로드한 환경에서 별도로 확인한다.

[현재 PRD·아키텍처](https://github.com/say-scene/ss-docs/blob/main/english-speaking-prd-architecture-v1.md) · [배포 매니페스트](https://github.com/say-scene/ss-k8s/tree/main/apps/ss-triton) · [CPU STT 운영 기록](https://github.com/say-scene/ss-docs/blob/main/manual/stt-cpu-whisper-setup-v1.md)
