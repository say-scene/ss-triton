# 첫 검증은 CUDA 13.4.1 기반 26.09로 진행한다. build arg로 버전을 바꿀 수 있다.
ARG TRITON_BASE_IMAGE=nvcr.io/nvidia/tritonserver:26.09-vllm-python-py3
FROM ${TRITON_BASE_IMAGE}

# 기존 Mac 검증 서버와 같은 직접 의존성 버전. CUDA/cuDNN 호환은 GPU에서 검증한다.
RUN python3 -m pip install --no-cache-dir faster-whisper==1.2.1 av==16.0.1
# KServe v2 HTTP와 OpenAI frontend를 함께 사용하기 위한 바인딩이 필요하다.
RUN python3 -c "import tritonserver, tritonfrontend"

COPY model_repository /models
COPY entrypoint.sh /entrypoint.sh
COPY healthcheck.py /healthcheck.py
RUN chmod +x /entrypoint.sh

ENV WHISPER_MODEL_PATH=/opt/models/faster-whisper-large-v3-turbo
EXPOSE 9000 8000 8002
ENTRYPOINT ["/entrypoint.sh"]
