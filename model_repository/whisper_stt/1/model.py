"""BE의 base64 AUDIO 입력을 Whisper에 전달하는 Triton Python backend."""
import base64
import binascii
import io
import os

import numpy as np
import triton_python_backend_utils as pb_utils
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


class TritonPythonModel:
    def initialize(self, args):
        self.model = WhisperModel(
            os.environ['WHISPER_MODEL_PATH'], device='cuda', compute_type='float16',
            local_files_only=True,
        )

    def execute(self, requests):
        responses = []
        for request in requests:
            try:
                tensor = pb_utils.get_input_tensor_by_name(request, 'AUDIO')
                if tensor is None or tensor.as_numpy().shape != (1,):
                    raise ValueError('INVALID_INPUT')
                encoded = tensor.as_numpy()[0]
                if len(encoded) > 2796204:  # 2 MiB 음성의 base64 길이 상한
                    raise ValueError('AUDIO_TOO_LARGE')
                audio_bytes = base64.b64decode(encoded, validate=True)
                if not 0 < len(audio_bytes) <= 2 * 1024 * 1024:
                    raise ValueError('INVALID_AUDIO')
                audio = decode_audio(io.BytesIO(audio_bytes), sampling_rate=16000)
                if not 8000 <= len(audio) <= 31 * 16000:
                    raise ValueError('INVALID_DURATION')
                segments, _ = self.model.transcribe(
                    audio, language='en', vad_filter=True, temperature=0,
                    condition_on_previous_text=False, beam_size=1,
                )
                text = ' '.join(segment.text.strip() for segment in segments).strip()
                output = pb_utils.Tensor('TEXT', np.array([text.encode('utf-8')], dtype=object))
                responses.append(pb_utils.InferenceResponse(output_tensors=[output]))
            except (ValueError, binascii.Error):
                responses.append(pb_utils.InferenceResponse(
                    error=pb_utils.TritonError('INVALID_AUDIO', pb_utils.TritonError.INVALID_ARG)))
            except Exception:
                # 예외에 음성·발화가 포함될 수 있으므로 상세 내용은 반환·로깅하지 않는다.
                responses.append(pb_utils.InferenceResponse(error=pb_utils.TritonError('STT_FAILED')))
        return responses
