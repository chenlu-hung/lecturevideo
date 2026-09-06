#!/usr/bin/env python3
"""Detect slides whose content is cut off — either overflowing the slide box, or
clipped inside a box that squeezes its own content.

Usage:
    python3 check_fit.py <topic_dir> [--chrome <path>] [--tolerance <px>]

Reads:
    <topic_dir>/slides.html   — the marp handout render (from compile_marp.sh),
                                with every overlay / mathwrite formula visible.

Prints a per-page report to stdout and exits:
    0  — every page fits
    3  — one or more pages OVERFLOW (content past the slide's bottom edge) or
         CLIPPED (a box inside the page cuts its own rows/lines away)
    2  — usage error
    1  — runtime error (missing input, no Chrome, probe failure)

How it works: slides.html is loaded once in headless Chrome (`--dump-dom`, the
same browser marp-cli and render_mathwrite.py already need — no network needed,
KaTeX is already inlined). An appended probe forces every slide visible, waits
for fonts, and compares scrollHeight against clientHeight at two levels.

  OVERFLOW — on the `<section>`: how far content spills past the slide box. The
  theme anchors content to the top, so overflow always runs off the bottom.

  CLIPPED — on every descendant box. A section-level measurement alone is blind
  to a table (or any box) that is flex-shrunk or overflow-hidden: it cuts its own
  rows away while the section reports no overflow at all, so the page measures as
  fitting and the deck ships with rows nobody can read. Both checks are pure
  CSS-px (scrollHeight/clientHeight), so neither is disturbed by the SVG's
  viewport scaling — mixing that scaling with CSS-px padding is what makes
  getBoundingClientRect the wrong tool here.

Run this after compile_marp.sh. Thin each offending page (split it, cut words, or
drop a display formula to its own page) and re-compile until clean. A CLIPPED page
needs the *box* to fit, not merely the page: trimming a table's cell text can
reflow the column widths and leave the clip untouched, so confirm by eye that the
missing row is back. The density budget that keeps pages fitting is in
references/marp-and-overlays.md §"Layout & density".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Reuse the exact Chrome-detection logic (CHROME_PATH / common install paths /
# PATH lookup) that render_mathwrite.py already uses, so both tools agree on
# which browser to drive. Same directory → importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_mathwrite import find_chrome  # noqa: E402

RESULT_RE = re.compile(r'<pre id="__FIT_RESULT__"[^>]*>(.*?)</pre>', re.DOTALL)

PROBE_BODY = """
<script>
(async () => {
  const finish = (payload) => {
    const pre = document.createElement('pre');
    pre.id = '__FIT_RESULT__';
    pre.textContent = JSON.stringify(payload);
    document.body.appendChild(pre);
  };
  try {
    // Force every slide laid out and visible (the bespoke template hides
    // inactive slides), matching render_mathwrite.py's probe.
    const style = document.createElement('style');
    style.textContent = [
      '.bespoke-marp-slide { display: block !important; visibility: visible !important;',
      '  position: static !important; transform: none !important; opacity: 1 !important; }',
      'svg[data-marpit-svg] { display: block !important; visibility: visible !important; }',
    ].join('\\n');
    document.head.appendChild(style);

    // Line wrapping and thus content height depend on the real (CJK) font
    // metrics — wait for fonts before measuring.
    if (document.fonts && document.fonts.ready) await document.fonts.ready;

    const slides = document.querySelectorAll('svg[data-marpit-svg]');
    const pages = [];
    for (let i = 0; i < slides.length; i++) {
      const section = slides[i].querySelector('section');
      if (!section) { pages.push({ page: i + 1, overflow: null }); continue; }
      // scrollHeight > clientHeight ⇒ content spills past the (overflow:hidden)
      // box. The delta is the overflow in section-local px, unaffected by the
      // SVG's viewport scaling.
      const overflow = Math.max(0, section.scrollHeight - section.clientHeight);

      // Same test one level down. A box that clips its OWN content — a table
      // squeezed by the flex column, most often — leaves the section's own
      // scrollHeight untouched, so the page measures as fitting while rows are
      // visually cut away. Ask every descendant, not just the slide.
      const clipped = [];
      for (const el of section.querySelectorAll('*')) {
        // SVG children (KaTeX/MathJax output) have no scrollHeight worth
        // reading; their HTML container is checked instead.
        if (!(el instanceof HTMLElement)) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        // Deliberately NOT exempting overflow:auto/scroll. marp's default theme
        // gives <table> overflow:auto so a wide table can be scrolled — but a
        // slide is never scrolled, so on a deck that "scrollable" box is simply
        // rows the audience cannot read. This is the exact case that ships
        // broken: the section reports no overflow and the table swallows the
        // remainder silently.
        if (el.clientHeight <= 0) continue;
        const cut = el.scrollHeight - el.clientHeight;
        if (cut > 2) clipped.push({ tag: el.tagName.toLowerCase(), cut: Math.round(cut) });
      }
      // Nested boxes report the same cut; keep the worst per tag.
      const worst = {};
      for (const c of clipped) {
        if (!worst[c.tag] || c.cut > worst[c.tag].cut) worst[c.tag] = c;
      }
      pages.push({ page: i + 1, overflow: overflow, box: section.clientHeight,
                   clipped: Object.values(worst).sort((a, b) => b.cut - a.cut) });
    }
    finish({ pages: pages });
  } catch (e) {
    finish({ error: String(e && e.stack || e) });
  }
})();
</script>
"""


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a]
    chrome_arg = None
    tolerance = 2  # px of sub-pixel / rounding slack before a page counts as overflowing
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--chrome" and i + 1 < len(args):
            chrome_arg = args[i + 1]
            i += 2
        elif args[i] == "--tolerance" and i + 1 < len(args):
            try:
                tolerance = int(args[i + 1])
            except ValueError:
                print(f"ERROR: --tolerance expects an integer, got {args[i + 1]!r}", file=sys.stderr)
                return 2
            i += 2
        else:
            positional.append(args[i])
            i += 1

    if len(positional) != 1:
        print("Usage: check_fit.py <topic_dir> [--chrome <path>] [--tolerance <px>]",
              file=sys.stderr)
        return 2

    topic_dir = Path(positional[0]).resolve()
    slides_html = topic_dir / "slides.html"
    if not slides_html.is_file():
        print(f"ERROR: missing {slides_html} (run compile_marp.sh first)", file=sys.stderr)
        return 1

    chrome = find_chrome(chrome_arg)
    if not chrome:
        print("ERROR: no Chrome/Chromium found. Install Google Chrome or set CHROME_PATH / --chrome.",
              file=sys.stderr)
        return 1

    probe = slides_html.read_text(encoding="utf-8") + PROBE_BODY
    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=topic_dir,
                                     prefix=".check_fit_probe_", delete=False,
                                     encoding="utf-8") as f:
        probe_path = Path(f.name)
        f.write(probe)

    try:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--virtual-time-budget=8000", "--dump-dom", probe_path.as_uri()]
        print(f"[check_fit] measuring slide fit via headless Chrome …")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    finally:
        probe_path.unlink(missing_ok=True)

    m = RESULT_RE.search(proc.stdout)
    if not m:
        print("ERROR: probe produced no result. Chrome stderr follows:", file=sys.stderr)
        print(proc.stderr[-2000:], file=sys.stderr)
        return 1

    payload = json.loads(m.group(1))
    if "error" in payload:
        print(f"ERROR: probe script failed: {payload['error']}", file=sys.stderr)
        return 1

    pages = payload["pages"]
    if not pages:
        print("ERROR: no slides found in slides.html (bad render?)", file=sys.stderr)
        return 1

    overflowing = []
    clipping = []
    for p in pages:
        ov = p.get("overflow")
        if ov is None:
            print(f"[check_fit] WARN: page {p['page']:02d} has no <section> to measure", file=sys.stderr)
            continue
        if ov > tolerance:
            overflowing.append(p)
            print(f"[check_fit] page {p['page']:02d} OVERFLOW by {int(round(ov))}px")
        cut = [c for c in p.get("clipped", []) if c["cut"] > tolerance]
        if cut:
            clipping.append(p)
            where = ", ".join(f"<{c['tag']}> by {c['cut']}px" for c in cut)
            print(f"[check_fit] page {p['page']:02d} CLIPPED — {where}")

    if overflowing or clipping:
        bad = len({p["page"] for p in overflowing + clipping})
        if overflowing:
            print(f"[check_fit] {len(overflowing)} page(s) overflow — "
                  "thin them (split / cut / move a formula to its own page).")
        if clipping:
            print(f"[check_fit] {len(clipping)} page(s) clip a box's own content — "
                  "its rows/lines are cut off on screen even though the page fits. "
                  "Thin the page so the box gets the height it needs.")
        print(f"[check_fit] {bad} of {len(pages)} page(s) need work — fix and re-compile.")
        return 3

    print(f"[check_fit] all {len(pages)} page(s) fit.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
