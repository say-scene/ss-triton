"""CPU Whisper STT 서버. Triton 대신 KServe v2 JSON 계약만 맞춘 어댑터.
컨테이너(Dockerfile)와 Mac 로컬 실행 양쪽에서 쓴다."""
import base64
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from faster_whisper import WhisperModel
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from faster_whisper.audio import decode_audio

MAX_AUDIO = 2 * 1024 * 1024
MODEL_NAME = os.getenv('WHISPER_MODEL', 'large-v3-turbo')
# 컨테이너는 0.0.0.0, CPU limit과 같은 스레드 수를 env로 준다.
HOST = os.getenv('STT_HOST', '127.0.0.1')
CPU_THREADS = int(os.getenv('WHISPER_CPU_THREADS', '4'))
# 동시에 계산하는 요청 수. 넘치는 요청은 QUEUE_TIMEOUT초까지 기다린 뒤 503을 받는다.
CONCURRENCY = int(os.getenv('STT_CONCURRENCY', '1'))
QUEUE_TIMEOUT = float(os.getenv('STT_QUEUE_TIMEOUT', '10'))
MODEL = None
SLOTS = threading.BoundedSemaphore(CONCURRENCY)

REQUESTS = Counter('stt_requests_total', 'STT 요청 결과', ['result'])
QUEUE_WAIT = Histogram('stt_queue_wait_seconds', '처리 슬롯 대기 시간',
                       buckets=(0.05, 0.25, 0.5, 1, 2, 3, 5, 10))
INFERENCE = Histogram('stt_inference_seconds', 'Whisper 처리 시간',
                      buckets=(0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30, 60))
AUDIO = Histogram('stt_audio_seconds', '입력 음성 길이', buckets=(1, 2, 3, 5, 8, 13, 20, 31))

def transcribe(audio_bytes):
    audio = decode_audio(io.BytesIO(audio_bytes), sampling_rate=16000)
    if len(audio) < 8000 or len(audio) > 31 * 16000:
        raise ValueError('INVALID_DURATION')
    started = time.perf_counter()
    segments, _ = MODEL.transcribe(audio, language='en', vad_filter=True, temperature=0,
                                  condition_on_previous_text=False, beam_size=1)
    text = ' '.join(s.text.strip() for s in segments).strip()
    # 모델 비교용 처리 시간. 발화 텍스트는 남기지 않는다.
    audio_s, elapsed_s = len(audio) / 16000, time.perf_counter() - started
    AUDIO.observe(audio_s)
    INFERENCE.observe(elapsed_s)
    print(f'stt model={MODEL_NAME} audio_s={audio_s:.2f} elapsed_s={elapsed_s:.2f} rtf={elapsed_s / audio_s:.2f}', flush=True)
    return text

class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def do_GET(self):
        if self.path == '/metrics':
            payload = generate_latest()
            self.send_response(200)
            self.send_header('Content-Type', CONTENT_TYPE_LATEST)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.respond(200 if self.path == '/health' else 404, {'ready': MODEL is not None})
    def do_POST(self):
        if self.path != '/v2/models/whisper_stt/infer':
            self.respond(404, {'code':'NOT_FOUND'}); return
        waited = time.perf_counter()
        if not SLOTS.acquire(timeout=QUEUE_TIMEOUT):
            REQUESTS.labels('busy').inc()
            self.respond(503, {'code':'STT_BUSY'}); return
        QUEUE_WAIT.observe(time.perf_counter() - waited)
        result = 'failed'
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 3 * 1024 * 1024:
                result = 'invalid'
                self.respond(413, {'code':'AUDIO_TOO_LARGE'}); return
            self.connection.settimeout(15)
            body = json.loads(self.rfile.read(length))
            tensor = body['inputs'][0]
            if tensor['name'] != 'AUDIO' or tensor['datatype'] != 'BYTES' or tensor['shape'] != [1]:
                raise ValueError('INVALID_INPUT')
            audio = base64.b64decode(tensor['data'][0], validate=True)
            if not 0 < len(audio) <= MAX_AUDIO:
                result = 'invalid'
                self.respond(413, {'code':'AUDIO_TOO_LARGE'}); return
            text = transcribe(audio)
            result = 'ok'
            self.respond(200, {'outputs':[{'name':'TEXT','shape':[1],'datatype':'BYTES','data':[text]}]})
        except (ValueError, KeyError, IndexError, TypeError):
            result = 'invalid'
            self.respond(422, {'code':'INVALID_AUDIO'})
        except Exception:
            self.respond(422, {'code':'STT_FAILED'})
        finally:
            REQUESTS.labels(result).inc()
            SLOTS.release()
    def log_message(self, *args):
        pass  # 음성 및 발화 텍스트를 로그에 남기지 않는다.

if __name__ == '__main__':
    print('Whisper 모델 준비 중: ' + MODEL_NAME, flush=True)
    MODEL = WhisperModel(MODEL_NAME, device='cpu', compute_type='int8',
                         cpu_threads=CPU_THREADS, num_workers=CONCURRENCY)
    print(f'Whisper 준비 완료: http://{HOST}:8001 (threads={CPU_THREADS}, concurrency={CONCURRENCY}, '
          f'queue_timeout={QUEUE_TIMEOUT}s)', flush=True)
    ThreadingHTTPServer((HOST, 8001), Handler).serve_forever()
