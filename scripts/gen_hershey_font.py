#!/usr/bin/env python3
"""Generate assets/player/hershey-font.js from bundled Hershey .jhf sources.

Single-stroke (centerline) glyph data for the player's hand-written math. The
player replaces each MathJax glyph outline with the matching Hershey single
stroke so the pen draws a real human-like trajectory (not an outline trace).

Output: window.HERSHEY_FONT = { "<key>": [ [[x,y],...], ... ], ... }
  - Latin/ASCII glyphs keyed by the literal character ("x", "=", "+", ...).
  - Greek glyphs keyed "g:<ascii-slot>". greeks.jhf orders its glyphs by POSITION
    in the Greek alphabet over the Latin slots — 'a'=alpha, 'b'=beta, 'c'=gamma,
    'd'=delta, ... 'x'=omega — lowercase on a..x and uppercase on A..X. (It is not
    a transliteration: 'q' holds rho, not theta. player.js MW_GSEQ/MW_GSEQ_UP index
    into these slots and must stay in step.) This is the SIMPLEX cut on purpose:
    the Complex/Duplex cuts fake a heavier weight by drawing each stroke as two
    parallel lines, which the player would then write out as a doubled trajectory.
  - Cursive Latin glyphs keyed "c:<char>", from the single-line SVG font
    HersheyScriptMed.svg. player.js routes math-italic variables here so a formula
    is written in a joined hand while operators stay upright; a char the cursive
    font lacks falls back to the upright glyph.
  - Hand-authored math symbols keyed "s:<name>" (int, prime, ...) for glyphs the
    classic Hershey set lacks.
Coordinates are Hershey units (origin at glyph centre, +y DOWN, baseline ~ +9);
the SVG font is y-UP with baseline 0, so it is negated and scaled to match. Only
each glyph's stroke bbox is used downstream, so the origin itself does not matter.
Run from the repo root:  python3 scripts/gen_hershey_font.py
"""
from __future__ import annotations
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HERSHEY = REPO / "assets" / "hershey"
OUT = REPO / "assets" / "player" / "hershey-font.js"


def parse_jhf(path: Path, first_code: int = 32) -> dict[str, list]:
    """Parse a Hershey .jhf: one glyph per line, ASCII order from first_code.
    Header = number(5) + vertex-count(3); then `count` coordinate pairs; pair 0
    is the left/right bearing (skipped); a pair starting with ' ' is a pen-up."""
    glyphs: dict[str, list] = {}
    for i, line in enumerate(path.read_text(encoding="latin1").split("\n")):
        if len(line) < 8:
            continue
        try:
            count = int(line[5:8])
        except ValueError:
            continue
        body = line[8:]
        strokes, cur = [], []
        for k in range(1, count):                      # pair 0 = bearings
            c1, c2 = body[2 * k:2 * k + 1], body[2 * k + 1:2 * k + 2]
            if not c1 or not c2:
                break
            if c1 == " ":                              # pen up
                if cur:
                    strokes.append(cur); cur = []
                continue
            cur.append([ord(c1) - 82, ord(c2) - 82])   # 'R' == 82
        if cur:
            strokes.append(cur)
        if strokes:
            glyphs[chr(first_code + i)] = strokes
    return glyphs


SVG_GLYPH_RE = re.compile(
    r'<glyph\s+unicode="(.)"[^>]*?horiz-adv-x="[\d.]+"(?:[^>]*?d="([^"]*)")?')
SVG_CMD_RE = re.compile(r"([ML])\s*(-?[\d.]+)\s+(-?[\d.]+)")


def parse_svg_font(path: Path, scale: float = 0.1) -> dict[str, list]:
    """Parse a single-line (pen-path) SVG font into the .jhf stroke convention.

    These fonts draw with `M`/`L` only — the glyph data IS the pen path — so each
    subpath maps straight onto one Hershey stroke. SVG fonts are y-UP; y is negated
    to match .jhf's y-DOWN, and coordinates are scaled from units-per-em 1000 down
    to the Hershey range so both sources sit at a comparable magnitude."""
    glyphs: dict[str, list] = {}
    for m in SVG_GLYPH_RE.finditer(path.read_text(encoding="utf-8")):
        ch, d = m.group(1), m.group(2) or ""
        strokes, cur = [], []
        for cmd, x, y in SVG_CMD_RE.findall(d):
            pt = [round(float(x) * scale, 2), round(-float(y) * scale, 2)]
            if cmd == "M":
                if len(cur) > 1:
                    strokes.append(cur)
                cur = [pt]
            else:
                cur.append(pt)
        if len(cur) > 1:
            strokes.append(cur)
        if strokes:
            glyphs[ch] = strokes
    return glyphs


# Hand-authored single strokes for symbols the classic Hershey set lacks.
# Same coordinate convention (centre origin, +y down, baseline ~+9); the player
# fits each into the matching MathJax glyph box, so only the SHAPE matters.
HAND = {
    # Integral: one continuous S — top hook, vertical stem, bottom hook.
    "s:int": [[
        [2.6, -13.5], [3.1, -15.0], [2.2, -16.0], [1.2, -16.0], [0.4, -15.0],
        [0.1, -13.0], [0.0, -8.0], [0.0, -2.0], [0.0, 4.0], [0.0, 9.0],
        [-0.1, 13.0], [-0.4, 15.0], [-1.2, 16.0], [-2.2, 16.0], [-3.0, 15.0],
        [-2.5, 13.5],
    ]],
    # Prime: a short upward tick sitting high (superscript position).
    "s:prime": [[[2.0, -14.0], [-1.0, -7.0]]],
    # Summation sign: single zig-zag stroke (top bar, down-diagonal, up-diagonal,
    # bottom bar) — best-effort for decks that use it.
    "s:sum": [[[6, -12], [-6, -12], [0, 0], [-6, 12], [6, 12]]],
    # Radical surd (the check part only; MathJax draws the vinculum as a rule).
    "s:surd": [[[-6, 2], [-3, 6], [0, -12], [8, -12]]],
}


def main() -> int:
    latin = parse_jhf(HERSHEY / "rowmans.jhf")
    greek = parse_jhf(HERSHEY / "greeks.jhf")
    cursive = parse_svg_font(HERSHEY / "HersheyScriptMed.svg")

    data: dict[str, list] = {}
    data.update(latin)                                 # keyed by literal char
    for slot, strokes in greek.items():
        data["g:" + slot] = strokes
    for ch, strokes in cursive.items():
        data["c:" + ch] = strokes
    data.update(HAND)

    payload = json.dumps(data, separators=(",", ":"))
    OUT.write_text(
        "// GENERATED by scripts/gen_hershey_font.py — do not edit by hand.\n"
        "// Single-stroke Hershey glyph centerlines for the player's hand-written math.\n"
        "//\n"
        "// The Hershey Fonts were originally created by Dr. A. V. Hershey while working\n"
        "// at the U. S. National Bureau of Standards. The format of the font data was\n"
        "// originally created by James Hurt, Cognition Inc., 900 Technology Park Drive,\n"
        "// Billerica MA 01821. Usable by anyone for any purpose provided this notice\n"
        "// travels with the data; see assets/hershey/NOTICE for the full terms.\n"
        "window.HERSHEY_FONT = " + payload + ";\n",
        encoding="utf-8",
    )
    print(f"[gen_hershey_font] wrote {OUT} "
          f"({len(latin)} latin, {len(greek)} greek, {len(cursive)} cursive, "
          f"{len(HAND)} hand, {len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
