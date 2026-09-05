#!/usr/bin/env bash
# Provision the one-off export environment and produce fp16 GPT-2 ONNX graphs for the
# remote narration worker. See references/remote-tts.md for why this is worth doing:
# the published GPT-2 graphs are int8 and ONNX Runtime's CUDA EP cannot run them, so the
# autoregressive loop — ~94% of a cue — is stranded on the CPU.
#
# PyTorch is installed here and nowhere else. The inference venv stays torch-free.
#
#   bash setup_gpt2_fp16.sh [ROOT]        # default ROOT=/data/lecturevideo-tts
#
# Needs ~30 GB under ROOT and a CUDA GPU. Idempotent: each step skips if already done.
set -euo pipefail

ROOT="${1:-/data/lecturevideo-tts}"
IDX_TAG="v2.0.0"                       # the version these checkpoints and the exporters target
PY="3.11"                              # satisfies index-tts (numba 0.58 needs <=3.11) and
                                       # indextts-onnx (requires >=3.11) at the same time
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXPORT_DIR="$ROOT/export"
CKPT_DIR="$ROOT/checkpoints"
ONNX_DIR="$ROOT/onnx-fp16"
MODELS_DIR="$ROOT/models"
VENV="$EXPORT_DIR/index-tts/.venv"   # uv sync puts it here

if [ ! -w "$(dirname "$ROOT")" ] && [ ! -d "$ROOT" ]; then
    echo "ERROR: cannot create $ROOT — ask an admin for:" >&2
    echo "  sudo mkdir -p $ROOT && sudo chown \$USER:\$USER $ROOT" >&2
    exit 1
fi
mkdir -p "$EXPORT_DIR" "$CKPT_DIR" "$ONNX_DIR" "$MODELS_DIR"

export PATH="$HOME/.local/bin:$PATH"
# Keep uv's wheel cache (several GB of CUDA wheels) on the big disk, not on /.
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/uv-cache}"
# Same for HuggingFace: IndexTTS2.__init__ pulls ~8 GB of auxiliary weights (w2v-BERT,
# MaskGCT, CAMPPlus, BigVGAN). This has to be set *before* python starts — huggingface_hub
# freezes the cache path into module constants at import, so setting it from inside the
# export script is already too late and everything lands in ~/.cache/huggingface.
export HF_HOME="${HF_HOME:-$ROOT/hf-cache}"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

echo "== 1/4 index-tts $IDX_TAG =="
[ -d "$EXPORT_DIR/index-tts" ] || \
    git clone -q --depth 1 --branch "$IDX_TAG" https://github.com/index-tts/index-tts "$EXPORT_DIR/index-tts"

echo "== 2/4 export venv =="
# Probe an actual import, not $VENV/bin/python: uv creates that symlink up front, so a
# half-finished sync would look complete and the next run would skip straight past it.
if ! "$VENV/bin/python" -c "import indextts, indextts_onnx.export, onnxconverter_common" 2>/dev/null; then
    # `uv sync`, not `uv pip install`: only the project interface honours index-tts's
    # [tool.uv.sources], which routes torch to the CUDA build. A PyPI-resolved torch can
    # land CPU-only, and fp16 tracing needs CUDA.
    #
    # Retried, and with a stall timeout: this pulls ~7 GB of wheels and a single connection
    # that goes quiet will otherwise hang uv indefinitely (observed once for over an hour,
    # 71s of CPU across it). Everything already fetched is in UV_CACHE_DIR, so a retry
    # resumes rather than restarts.
    export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-60}"
    for attempt in 1 2 3 4 5; do
        if (cd "$EXPORT_DIR/index-tts" && uv sync --python "$PY"); then
            break
        fi
        echo "-- uv sync attempt $attempt failed; retrying" >&2
        sleep 5
    done
    # The exporters go in afterwards with --no-deps so indextts-onnx cannot drag
    # index-tts's pins (numpy 1.26, numba 0.58, transformers 4.52.1) back out.
    VIRTUAL_ENV="$VENV" uv pip install --no-deps indextts-onnx
    # onnxruntime is not used for the export itself, but indextts_onnx/__init__.py imports
    # IndexTTSInfer, so even `import indextts_onnx.export` needs it present.
    VIRTUAL_ENV="$VENV" uv pip install onnx onnxconverter-common onnxruntime
    # onnx drags protobuf past 4.x, which refuses to load the _pb2 modules that
    # index-tts's pinned tensorboard 2.9 shipped (descript-audiotools imports
    # torch.utils.tensorboard on the way to infer_v2). A current tensorboard has
    # regenerated protos and fixes it without forcing the pure-python protobuf
    # backend, which would make saving multi-GB ONNX painfully slow.
    VIRTUAL_ENV="$VENV" uv pip install -U tensorboard
    # …but that upgrade pulls numpy 2.x, and index-tts's matplotlib is built against
    # 1.x. Put index-tts's own pin back, last, so it wins.
    VIRTUAL_ENV="$VENV" uv pip install "numpy==1.26.2"
fi

echo "== 3/4 checkpoints =="
if [ ! -f "$CKPT_DIR/gpt.pth" ]; then
    "$VENV/bin/python" - "$CKPT_DIR" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download("IndexTeam/IndexTTS-2", local_dir=sys.argv[1])
PY
fi

echo "== 4/4 export =="
# The auxiliary weights (w2v-BERT, MaskGCT, CAMPPlus, BigVGAN, ~3.5 GB) are pulled by
# IndexTTS2.__init__ on first run into $CKPT_DIR/hf_cache.
"$VENV/bin/python" "$SCRIPT_DIR/export_gpt2_fp16.py" \
    --checkpoint-dir "$CKPT_DIR" --output-dir "$ONNX_DIR"

cat <<MSG

Done. To put the fp16 graphs into service:

    cp $ONNX_DIR/<variant>/gpt2_{init,step,forward}.onnx* $MODELS_DIR/
    rm -f $MODELS_DIR/gpt2_{init,step,forward}_int8.onnx

where <variant> is fp32, fp16-io32 or fp16 (see export_gpt2_fp16.py's docstring; fp16
needs the caller to feed fp16, the other two do not).

The loader prefers *_int8.onnx whenever it exists, so the int8 copies must go; after that
--providers auto sees plain names and places them on CUDA. Point the worker at this dir with
INDEXTTS_ONNX_MODELS=$MODELS_DIR (the ~/bin/indextts2-batch launcher already prefers it).
MSG
