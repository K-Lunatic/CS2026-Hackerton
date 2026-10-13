#!/usr/bin/env python3
"""Explicit one-time download of pinned local ASR/VAD models, never school data."""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from providers.moodle_session import _default_ssl_context

MODELS = (
    ('ggml-small-q5_1.bin', 'ggerganov/whisper.cpp', '5359861c739e955e79d9a303bcbc70fb988958b1',
     190085487, 'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb'),
    ('ggml-silero-v6.2.0.bin', 'ggml-org/whisper-vad', '9ffd54a1e1ee413ddf265af9913beaf518d1639b',
     885098, '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'),
)


def prepare(folder, spec):
    name, repo, revision, size, expected = spec
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / name
    if target.is_file() and target.stat().st_size == size:
        digest = hashlib.sha256()
        with target.open('rb') as source:
            for block in iter(lambda: source.read(1_048_576), b''):
                digest.update(block)
        if digest.hexdigest() == expected:
            print(f'{name}은 이미 준비되어 있어요.', flush=True)
            return target
    temporary = None
    try:
        url = f'https://huggingface.co/{repo}/resolve/{revision}/{name}'
        digest, received = hashlib.sha256(), 0
        with urlopen(url, context=_default_ssl_context(), timeout=30) as response, tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
            temporary = Path(output.name)
            for block in iter(lambda: response.read(1_048_576), b''):
                received += len(block)
                if received > size:
                    raise ValueError('모델 크기가 달라 설치하지 않았어요.')
                digest.update(block)
                output.write(block)
                if received // 20_000_000 != (received - len(block)) // 20_000_000:
                    print(f'전사 모델 준비 중 · {received // 1_000_000}/{size // 1_000_000}MB', flush=True)
            output.flush()
            os.fsync(output.fileno())
        if received != size or digest.hexdigest() != expected:
            raise ValueError('모델 검증값이 달라 설치하지 않았어요.')
        os.replace(temporary, target)
        print(f'{name} 준비와 검증을 마쳤어요.', flush=True)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='로컬 한국어 전사 모델 최초 준비')
    parser.add_argument('--models-dir', type=Path)
    args = parser.parse_args()
    db = Path(os.environ.get('UNIVERSITY_AGENT_DB', Path.home() / '.university-agent/university.db')).expanduser()
    folder = (args.models_dir or db.parent / 'models').expanduser()
    try:
        for spec in MODELS:
            prepare(folder, spec)
    except (OSError, ValueError):
        raise SystemExit('전사 모델을 준비하지 못했어요. 연결과 저장 권한을 확인해 주세요. 기존 정상 모델은 보존했어요.') from None


if __name__ == '__main__':
    main()
