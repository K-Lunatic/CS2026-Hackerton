#!/bin/zsh
set -e
ROOT="${0:A:h}"

if command -v python3 >/dev/null 2>&1; then
  exec "$(command -v python3)" "$@"
fi

UV=""
if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
  UV="$HOME/.local/bin/uv"
fi

if [[ -z "$UV" ]]; then
  echo "터틀넥이 필요한 Python 실행 환경을 한 번 준비할게요…"
  TEMP_SCRIPT="$(mktemp)"
  trap 'rm -f "$TEMP_SCRIPT"' EXIT
  curl -fsSL https://astral.sh/uv/install.sh -o "$TEMP_SCRIPT"
  sh "$TEMP_SCRIPT"
  UV="$HOME/.local/bin/uv"
fi

if [[ ! -x "$UV" ]]; then
  echo "Python 실행 환경을 준비하지 못했어요. 인터넷 연결을 확인한 뒤 다시 실행해 주세요."
  exit 1
fi

exec "$UV" run --python 3.11 python "$@"
