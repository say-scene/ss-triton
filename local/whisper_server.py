"""Mac 로컬 검증용 Whisper 서버. Triton 서버 자체가 아닌 v2 JSON 계약의 로컬 어댑터."""
import base64
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

MAX_AUDIO = 2 * 1024 * 1024
MODEL_NAME = os.getenv('WHISPER_MODEL', 'large-v3-turbo')
MODEL = None
LOCK = threading.Lock()

def transcribe(audio_bytes):
    audio = decode_audio(io.BytesIO(audio_bytes), sampling_rate=16000)
    if len(audio) < 8000 or len(audio) > 31 * 16000:
        raise ValueError('INVALID_DURATION')
    segments, _ = MODEL.transcribe(audio, language='en', vad_filter=True, temperature=0,
                                  condition_on_previous_text=False, beam_size=1)
    return ' '.join(s.text.strip() for s in segments).strip()

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
        self.respond(200 if self.path == '/health' else 404, {'ready': MODEL is not None})
    def do_POST(self):
        if self.path != '/v2/models/whisper_stt/infer':
            self.respond(404, {'code':'NOT_FOUND'}); return
        if not LOCK.acquire(blocking=False):
            self.respond(503, {'code':'STT_BUSY'}); return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 3 * 1024 * 1024:
                self.respond(413, {'code':'AUDIO_TOO_LARGE'}); return
            self.connection.settimeout(15)
            body = json.loads(self.rfile.read(length))
            tensor = body['inputs'][0]
            if tensor['name'] != 'AUDIO' or tensor['datatype'] != 'BYTES' or tensor['shape'] != [1]:
                raise ValueError('INVALID_INPUT')
            audio = base64.b64decode(tensor['data'][0], validate=True)
            if not 0 < len(audio) <= MAX_AUDIO:
                self.respond(413, {'code':'AUDIO_TOO_LARGE'}); return
            text = transcribe(audio)
            self.respond(200, {'outputs':[{'name':'TEXT','shape':[1],'datatype':'BYTES','data':[text]}]})
        except (ValueError, KeyError, IndexError, TypeError):
            self.respond(422, {'code':'INVALID_AUDIO'})
        except Exception:
            self.respond(422, {'code':'STT_FAILED'})
        finally:
            LOCK.release()
    def log_message(self, *args):
        pass  # 음성 및 발화 텍스트를 로그에 남기지 않는다.

if __name__ == '__main__':
    print('Whisper 모델 준비 중: ' + MODEL_NAME, flush=True)
    MODEL = WhisperModel(MODEL_NAME, device='cpu', compute_type='int8', cpu_threads=4)
    print('Whisper 준비 완료: http://127.0.0.1:8001', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8001), Handler).serve_forever()
