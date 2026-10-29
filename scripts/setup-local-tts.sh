#!/usr/bin/env bash
set -euo pipefail
tts_project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tts_uv="${UV_BIN:-$(command -v uv || true)}"
if [ -z "$tts_uv" ]; then
  echo "请先安装 uv，再重新运行此脚本。"
  exit 1
fi
if [ ! -x "$tts_project_root/.tts-venv/bin/python" ]; then
  "$tts_uv" venv --python 3.11 "$tts_project_root/.tts-venv"
fi
"$tts_uv" pip install --python "$tts_project_root/.tts-venv/bin/python" \
  torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cpu
"$tts_uv" pip install --python "$tts_project_root/.tts-venv/bin/python" qwen-tts==0.1.1
"$tts_project_root/.tts-venv/bin/python" -c 'from qwen_tts import Qwen3TTSModel; import torch; print("Qwen3-TTS CPU runtime:", torch.__version__)'
printf 'qwen-tts==0.1.1\n' > "$tts_project_root/.tts-venv/.qwen-ready"
echo "本地语音环境已准备好。在设置 → 语言配音 → 本地下载模型中下载模型。"
