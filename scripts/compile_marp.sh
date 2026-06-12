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

# For the PNG pass (the images the video player shows), hide mathwrite formula
# blocks: layout space is preserved but the glyphs are invisible, so the player
# can hand-write the formula into the blank region at narration time. The HTML
# and PDF renders above keep the formulas visible (they are the handout).
PNG_SRC="$SLIDES_MD"
PNG_TMP=""
if grep -q 'class="mathwrite"' "$SLIDES_MD"; then
  PNG_TMP="$(dirname "$SLIDES_MD")/.mathwrite_png.md"
  cp "$SLIDES_MD" "$PNG_TMP"
  printf '\n<style>section .mathwrite { visibility: hidden; }</style>\n' >> "$PNG_TMP"
  PNG_SRC="$PNG_TMP"
  echo "[compile_marp] mathwrite blocks detected — blanking them in the PNG render"
fi

echo "[compile_marp] PNG  →  $OUT_DIR/slides.images/"
# --image-scale 1.5: the default theme is 1024pt×768pt = 1365.33px wide; newer
# Chrome rejects fractional device-metrics widths. ×1.5 lands on 2048×1536
# (integers) and doubles as a quality bump for the player.
"${MARP[@]}" "$PNG_SRC" \
  --theme-set "$THEME_CSS" \
  --html \
  --allow-local-files \
  --images png \
  --image-scale 1.5 \
  -o "$OUT_DIR/slides.images/slide.png"

if [[ -n "$PNG_TMP" ]]; then rm -f "$PNG_TMP"; fi

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
