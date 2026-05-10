#!/usr/bin/env bash
# Compile a marp slide deck to HTML, PDF, and per-page PNG images.
#
# Usage:
#   compile_marp.sh <slides.md> <theme.css> <output-dir>
#
# Outputs (under <output-dir>):
#   slides.html
#   slides.pdf
#   slides.images/01.png, 02.png, ...
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <slides.md> <theme.css> <output-dir>" >&2
  exit 2
fi

SLIDES_MD="$1"
THEME_CSS="$2"
OUT_DIR="$3"

if [[ ! -f "$SLIDES_MD" ]]; then
  echo "ERROR: slides.md not found: $SLIDES_MD" >&2
  exit 1
fi
if [[ ! -f "$THEME_CSS" ]]; then
  echo "ERROR: theme.css not found: $THEME_CSS" >&2
  exit 1
fi

mkdir -p "$OUT_DIR" "$OUT_DIR/slides.images"

MARP=(npx --yes @marp-team/marp-cli@latest)

echo "[compile_marp] HTML →  $OUT_DIR/slides.html"
"${MARP[@]}" "$SLIDES_MD" \
  --theme-set "$THEME_CSS" \
  --html \
  --allow-local-files \
  -o "$OUT_DIR/slides.html"

echo "[compile_marp] PDF  →  $OUT_DIR/slides.pdf"
"${MARP[@]}" "$SLIDES_MD" \
  --theme-set "$THEME_CSS" \
  --html \
  --allow-local-files \
  --pdf \
  -o "$OUT_DIR/slides.pdf"

echo "[compile_marp] PNG  →  $OUT_DIR/slides.images/"
"${MARP[@]}" "$SLIDES_MD" \
  --theme-set "$THEME_CSS" \
  --html \
  --allow-local-files \
  --images png \
  -o "$OUT_DIR/slides.images/slide.png"

# marp-cli emits slide.001.png, slide.002.png, ... — rename to NN.png
cd "$OUT_DIR/slides.images"
for f in slide.*.png; do
  [[ -e "$f" ]] || continue
  num="${f#slide.}"
  num="${num%.png}"
  printf -v padded "%02d" "$((10#$num))"
  mv -f "$f" "$padded.png"
done

echo "[compile_marp] done."
