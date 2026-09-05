#!/bin/sh
# Launcher installed on the remote as ~/bin/indextts2-batch — the default --remote-cmd of
# scripts/synthesize_tts.py. It exists only to pin the venv and the model dir, so the ssh
# command line stays a single word and the caller never has to know either path.
set -e
VENV="${INDEXTTS_ONNX_VENV:-$HOME/.virtualenvs/indextts-onnx}"
WORKER="${INDEXTTS_ONNX_WORKER:-$HOME/lecturevideo-tts/bin/indextts2_onnx_batch.py}"
# Prefer the models on the big disk when they are there, and among those prefer a dir
# carrying re-exported (non-int8) GPT-2 graphs — those are the ones the CUDA EP can
# actually run. See scripts/remote/use_gpt2_variant.sh. Falls back to the worker's own
# default of ~/.cache/indextts-onnx/models.
if [ -z "$INDEXTTS_ONNX_MODELS" ]; then
    for d in /data/lecturevideo-tts/models-fp16              /data/lecturevideo-tts/models-fp32              /data/lecturevideo-tts/models; do
        if [ -d "$d" ]; then
            INDEXTTS_ONNX_MODELS="$d"
            export INDEXTTS_ONNX_MODELS
            break
        fi
    done
fi
exec "$VENV/bin/python" "$WORKER" "$@"
