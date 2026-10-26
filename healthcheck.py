"""두 모델의 로드 완료를 검사한다. 발화·추론 요청은 보내지 않는다."""
import sys
import urllib.request


def main():
    try:
        for model in ('qwen3_8b', 'whisper_stt'):
            with urllib.request.urlopen(
                f'http://127.0.0.1:8000/v2/models/{model}/ready', timeout=2
            ) as response:
                if response.status != 200:
                    return 1
    except OSError:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
