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
# Resolve to absolute so later `$OUT_DIR/...` references survive the `cd` into
# slides.images during the PNG-rename step (a relative OUT_DIR would otherwise
# be re-resolved against the new cwd and silently skip the reveal-PNG move).
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

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

# ---- PNG render(s) for the video player ----
# The player shows PNGs, not the live HTML. Two regions are blanked in the image
# the player shows so they can be animated in at narration time instead of being
# baked on from slide-load:
#   * mathwrite formulas — the player hand-writes them into the blank region;
#   * overlay content — the player fades it in (in place) at its overlay window.
# Mathwrite blocks already carry a `<div class="mathwrite">`. Overlays are authored
# as HTML comments, so we first wrap each overlay's content in a
# `<div class="overlay-blank" data-ov=ID>` (blank lines kept around the content so
# the inner markdown still parses) — that gives the blanking CSS and the bbox probe
# something to target. The HTML/PDF handout above is rendered from the original
# slides.md, so it keeps everything visible.
HAS_MW=0; grep -q 'class="mathwrite"' "$SLIDES_MD" && HAS_MW=1
HAS_OV=0; grep -q 'overlay-begin:' "$SLIDES_MD" && HAS_OV=1

SRC_DIR="$(dirname "$SLIDES_MD")"
RENDER_SRC="$SRC_DIR/.render_src.md"

# Wrap overlay markers into divs (a no-op copy when there are no overlays).
python3 - "$SLIDES_MD" "$RENDER_SRC" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
BEGIN = re.compile(r'^\s*<!--\s*overlay-begin:\s*id=([a-z0-9-]+).*?-->\s*$', re.I)
END   = re.compile(r'^\s*<!--\s*overlay-end:\s*id=[a-z0-9-]+\s*-->\s*$', re.I)
out = []
for line in open(src, encoding='utf-8').read().splitlines():
    mb = BEGIN.match(line)
    if mb:
        out += ['<div class="overlay-blank" data-ov="%s">' % mb.group(1), '']
    elif END.match(line):
        out += ['', '</div>']
    else:
        out.append(line)
open(dst, 'w', encoding='utf-8').write("\n".join(out) + "\n")
PY

# Probe HTML with everything visible (overlay divs present) — render_mathwrite.py
# measures mathwrite + overlay bounding boxes from this.
if [[ "$HAS_MW" == 1 || "$HAS_OV" == 1 ]]; then
  echo "[compile_marp] probe →  $OUT_DIR/.render.html (bbox source)"
  "${MARP[@]}" "$RENDER_SRC" --theme-set "$THEME_CSS" --html --allow-local-files \
    -o "$OUT_DIR/.render.html"
fi

# Hide-CSS per pass.
HIDE_BASE=""
[[ "$HAS_MW" == 1 ]] && HIDE_BASE+="section .mathwrite{visibility:hidden}"
[[ "$HAS_OV" == 1 ]] && HIDE_BASE+="section .overlay-blank{visibility:hidden}"

# Base PNGs (mathwrite + overlay regions blanked) — what the player shows.
BASE_SRC="$RENDER_SRC"
if [[ -n "$HIDE_BASE" ]]; then
  BASE_SRC="$SRC_DIR/.png_base.md"; cp "$RENDER_SRC" "$BASE_SRC"
  printf '\n<style>%s</style>\n' "$HIDE_BASE" >> "$BASE_SRC"
  echo "[compile_marp] blanking mathwrite/overlay regions in the base PNG render"
fi
echo "[compile_marp] PNG  →  $OUT_DIR/slides.images/"
# --image-scale 1.5: the default theme is 1024pt×768pt = 1365.33px wide; newer
# Chrome rejects fractional device-metrics widths. ×1.5 lands on 2048×1536
# (integers) and doubles as a quality bump for the player.
"${MARP[@]}" "$BASE_SRC" \
  --theme-set "$THEME_CSS" --html --allow-local-files \
  --images png --image-scale 1.5 \
  -o "$OUT_DIR/slides.images/slide.png"

# Reveal PNGs (overlays visible; mathwrite still blanked) — the player crops each
# overlay's region from these and fades it in at the overlay window. Only needed
# when the deck has overlays.
if [[ "$HAS_OV" == 1 ]]; then
  REVEAL_SRC="$SRC_DIR/.png_reveal.md"; cp "$RENDER_SRC" "$REVEAL_SRC"
  [[ "$HAS_MW" == 1 ]] && printf '\n<style>section .mathwrite{visibility:hidden}</style>\n' >> "$REVEAL_SRC"
  echo "[compile_marp] PNG  →  $OUT_DIR/slides.images/ (overlay reveal layer)"
  REVEAL_TMP="$OUT_DIR/.reveal_png"; mkdir -p "$REVEAL_TMP"
  "${MARP[@]}" "$REVEAL_SRC" \
    --theme-set "$THEME_CSS" --html --allow-local-files \
    --images png --image-scale 1.5 \
    -o "$REVEAL_TMP/slide.png"
fi

rm -f "$RENDER_SRC" "$SRC_DIR/.png_base.md" "$SRC_DIR/.png_reveal.md"

# marp-cli emits slide.001.png … — rename the base set to NN.png and, if present,
# the reveal set to NN.reveal.png (alongside the base images).
cd "$OUT_DIR/slides.images"
for f in slide.*.png; do
  [[ -e "$f" ]] || continue
  num="${f#slide.}"; num="${num%.png}"
  printf -v padded "%02d" "$((10#$num))"
  mv -f "$f" "$padded.png"
done
if [[ -d "$OUT_DIR/.reveal_png" ]]; then
  for f in "$OUT_DIR/.reveal_png"/slide.*.png; do
    [[ -e "$f" ]] || continue
    b="$(basename "$f")"; num="${b#slide.}"; num="${num%.png}"
    printf -v padded "%02d" "$((10#$num))"
    mv -f "$f" "$OUT_DIR/slides.images/$padded.reveal.png"
  done
  rm -rf "$OUT_DIR/.reveal_png"
fi

echo "[compile_marp] done."
