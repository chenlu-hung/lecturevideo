#!/usr/bin/env bash
# Assemble a worker model dir that pairs one exported GPT-2 variant with the shared graphs.
#
#   bash use_gpt2_variant.sh <fp32|fp16-io32|fp16> [ROOT]
#
# Builds $ROOT/models-<variant>/ out of symlinks: everything from $ROOT/models except the
# int8 GPT-2 graphs, plus that variant's gpt2_{init,step,forward}. Point the worker at it
# with INDEXTTS_ONNX_MODELS so the variants can be benchmarked against each other without
# disturbing the int8 set that is currently in service.
#
# Two things the symlinks have to respect:
#   - ONNX Runtime resolves a graph's external-data blob relative to the path it was handed,
#     so <name>.onnx.data must be linked next to <name>.onnx here, not left behind.
#   - The loader prefers <name>_int8.onnx whenever it exists, so the int8 GPT-2 graphs must
#     be absent from this dir — otherwise the variant is silently ignored.
set -euo pipefail

VARIANT="${1:?usage: use_gpt2_variant.sh <fp32|fp16-io32|fp16> [ROOT]}"
ROOT="${2:-/data/lecturevideo-tts}"
SRC="$ROOT/onnx-fp16/$VARIANT"
BASE="$ROOT/models"
DEST="$ROOT/models-$VARIANT"

[ -d "$SRC" ] || { echo "ERROR: no such variant dir: $SRC" >&2; exit 1; }

rm -rf "$DEST"
mkdir -p "$DEST"

for f in "$BASE"/*; do
    name="$(basename "$f")"
    case "$name" in
        gpt2_init_int8.onnx|gpt2_step_int8.onnx|gpt2_forward_int8.onnx) continue ;;
    esac
    ln -s "$f" "$DEST/$name"
done

for name in gpt2_init gpt2_step gpt2_forward; do
    [ -f "$SRC/$name.onnx" ] || { echo "ERROR: $SRC/$name.onnx missing" >&2; exit 1; }
    ln -s "$SRC/$name.onnx" "$DEST/$name.onnx"
    [ -f "$SRC/$name.onnx.data" ] && ln -s "$SRC/$name.onnx.data" "$DEST/$name.onnx.data"
done

echo "$DEST ready:"
ls -l "$DEST" | awk '{print "  " $9, $10, $11}' | sed '/^  $/d'
echo
echo "Run the worker against it with:"
echo "  INDEXTTS_ONNX_MODELS=$DEST ~/bin/indextts2-batch --ref … --srt … --out …"
