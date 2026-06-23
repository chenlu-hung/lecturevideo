#!/usr/bin/env python3
"""Render mathwrite segments to SVG and measure their on-slide positions.

Usage:
    python3 render_mathwrite.py <topic_dir> [--chrome <path>] [--mathjax-url <url>]

Reads:
    <topic_dir>/.slides.json   — pages with mathwrite declarations (from split_slides.py)
    <topic_dir>/slides.html    — marp HTML render (from compile_marp.sh)

Writes:
    <topic_dir>/.mathwrite.json — per-mathwrite {page, id, bbox, segs:[{seg, svg, valign}]}

How it works: a probe HTML file is built from slides.html with an appended script
that (a) loads MathJax (tex-svg) and typesets every declared segment TeX into a
standalone SVG, and (b) measures each `<div class="mathwrite">` bounding box as
fractions of its slide. The probe runs once in headless Chrome (`--dump-dom`,
the same browser marp-cli already needs) and the result JSON is parsed out of
the dumped DOM. Requires network access for the MathJax CDN unless
--mathjax-url points at a local copy.

The player later hand-writes these SVGs glyph-by-glyph (a pen nib traces each glyph
outline while the ink fills in behind it) inside the measured bbox — which
compile_marp.sh leaves blank in the PNG render.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_MATHJAX_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"
RESULT_RE = re.compile(r'<pre id="__MW_RESULT__"[^>]*>(.*?)</pre>', re.DOTALL)

# MathJax is only needed to typeset mathwrite segment TeX — overlay-only decks skip
# it entirely (no network dependency).
MATHJAX_HEAD = """
<script>
window.MathJax = { svg: { fontCache: 'none' }, startup: { typeset: false } };
</script>
<script src="__MATHJAX_URL__"></script>
"""

PROBE_BODY = """
<script>
(async () => {
  const DATA = __MW_DATA__;      // mathwrite blocks to measure + typeset
  const OVS = __OV_DATA__;       // overlay regions to measure
  const NEED_MJ = __NEED_MJ__;   // whether MathJax was loaded
  const finish = (payload) => {
    const pre = document.createElement('pre');
    pre.id = '__MW_RESULT__';
    pre.textContent = JSON.stringify(payload);
    document.body.appendChild(pre);
  };
  try {
    if (NEED_MJ) await MathJax.startup.promise;
    // Force every slide laid out and visible so rects are measurable
    // (the bespoke template hides inactive slides).
    const style = document.createElement('style');
    style.textContent = [
      '.bespoke-marp-slide { display: block !important; visibility: visible !important;',
      '  position: static !important; transform: none !important; opacity: 1 !important; }',
      'svg[data-marpit-svg] { display: block !important; visibility: visible !important; }',
    ].join('\\n');
    document.head.appendChild(style);

    const slides = document.querySelectorAll('svg[data-marpit-svg]');
    const measure = (slideEl, el) => {
      if (!slideEl || !el) return null;
      const section = slideEl.querySelector('section');
      if (!section) return null;
      const sr = section.getBoundingClientRect();
      const er = el.getBoundingClientRect();
      if (sr.width > 0 && sr.height > 0 && er.width > 0) {
        return { x: (er.left - sr.left) / sr.width, y: (er.top - sr.top) / sr.height,
                 w: er.width / sr.width, h: er.height / sr.height };
      }
      return null;
    };

    const result = [];
    for (const item of DATA) {
      const slide = slides[item.page - 1];
      let bbox = null;
      if (slide) {
        const section = slide.querySelector('section');
        const els = section ? section.querySelectorAll('.mathwrite') : [];
        bbox = measure(slide, els[item.ord]);
      }
      const segs = [];
      for (const s of item.segs) {
        const node = await MathJax.tex2svgPromise(s.tex, { display: true });
        const svg = node.querySelector('svg');
        segs.push({
          seg: s.seg,
          svg: svg ? svg.outerHTML : null,
          valign: svg && svg.style.verticalAlign ? svg.style.verticalAlign : '0',
        });
      }
      result.push({ page: item.page, id: item.id, bbox: bbox, segs: segs });
    }

    const ovresult = [];
    for (const ov of OVS) {
      const slide = slides[ov.page - 1];
      let bbox = null;
      if (slide) {
        const section = slide.querySelector('section');
        const el = section ? section.querySelector('.overlay-blank[data-ov="' + ov.id + '"]') : null;
        bbox = measure(slide, el);
      }
      ovresult.push({ page: ov.page, id: ov.id, bbox: bbox });
    }

    finish({ mathwrites: result, overlays: ovresult });
  } catch (e) {
    finish({ error: String(e && e.stack || e) });
  }
})();
</script>
"""


def find_chrome(explicit: str | None) -> str | None:
    if explicit:
        return explicit if Path(explicit).is_file() else None
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).is_file():
        return env
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def make_unique_ids(svg: str, prefix: str) -> str:
    """Prefix MathJax element ids so multiple SVGs can coexist in one document."""
    return svg.replace("MJX-", f"{prefix}-MJX-")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a]
    chrome_arg = None
    mathjax_url = DEFAULT_MATHJAX_URL
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--chrome" and i + 1 < len(args):
            chrome_arg = args[i + 1]
            i += 2
        elif args[i] == "--mathjax-url" and i + 1 < len(args):
            mathjax_url = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    if len(positional) != 1:
        print("Usage: render_mathwrite.py <topic_dir> [--chrome <path>] [--mathjax-url <url>]",
              file=sys.stderr)
        return 2

    topic_dir = Path(positional[0]).resolve()
    slides_json = topic_dir / ".slides.json"
    # Prefer the probe render (overlay divs present, everything visible) emitted by
    # compile_marp.sh; fall back to slides.html for older compiles.
    render_html = topic_dir / ".render.html"
    slides_html = render_html if render_html.is_file() else topic_dir / "slides.html"
    out_path = topic_dir / ".mathwrite.json"

    if not slides_json.is_file():
        print(f"ERROR: missing {slides_json} (run split_slides.py first)", file=sys.stderr)
        return 1
    if not slides_html.is_file():
        print(f"ERROR: missing {slides_html} (run compile_marp.sh first)", file=sys.stderr)
        return 1

    pages = json.loads(slides_json.read_text(encoding="utf-8"))["pages"]
    items = []
    for page in pages:
        for ord_, mw in enumerate(page.get("mathwrites", [])):
            items.append({
                "page": page["index"],
                "ord": ord_,
                "id": mw["id"],
                "segs": [{"seg": s["seg"], "tex": s["tex"]} for s in mw["segs"]],
            })
    ov_items = [{"page": page["index"], "id": ov["id"]}
                for page in pages for ov in page.get("overlays", [])]

    if not items and not ov_items:
        out_path.write_text(json.dumps({"mathwrites": [], "overlays": []}), encoding="utf-8")
        print("[render_mathwrite] no mathwrite/overlay regions declared — wrote empty .mathwrite.json")
        return 0

    chrome = find_chrome(chrome_arg)
    if not chrome:
        print("ERROR: no Chrome/Chromium found. Install Google Chrome or set CHROME_PATH / --chrome.",
              file=sys.stderr)
        return 1

    need_mj = bool(items)
    head = MATHJAX_HEAD.replace("__MATHJAX_URL__", mathjax_url) if need_mj else ""
    body = (PROBE_BODY
            .replace("__MW_DATA__", json.dumps(items, ensure_ascii=False))
            .replace("__OV_DATA__", json.dumps(ov_items, ensure_ascii=False))
            .replace("__NEED_MJ__", "true" if need_mj else "false"))
    probe = slides_html.read_text(encoding="utf-8") + head + body

    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=topic_dir,
                                     prefix=".mathwrite_probe_", delete=False,
                                     encoding="utf-8") as f:
        probe_path = Path(f.name)
        f.write(probe)

    try:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--virtual-time-budget=30000", "--dump-dom", probe_path.as_uri()]
        print(f"[render_mathwrite] probing {len(items)} mathwrite block(s) + "
              f"{len(ov_items)} overlay region(s) via headless Chrome …")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    finally:
        probe_path.unlink(missing_ok=True)

    m = RESULT_RE.search(proc.stdout)
    if not m:
        print("ERROR: probe produced no result. Chrome stderr follows:", file=sys.stderr)
        print(proc.stderr[-2000:], file=sys.stderr)
        print("Hint: the MathJax CDN must be reachable, or pass --mathjax-url with a local copy.",
              file=sys.stderr)
        return 1

    payload = json.loads(html.unescape(m.group(1)))
    if "error" in payload:
        print(f"ERROR: probe script failed: {payload['error']}", file=sys.stderr)
        return 1

    warnings: list[str] = []
    for mw in payload["mathwrites"]:
        if mw["bbox"] is None:
            warnings.append(f"page {mw['page']} mathwrite '{mw['id']}': bbox not measurable; "
                            "player will use a centered fallback box")
        for si, seg in enumerate(mw["segs"]):
            if not seg["svg"]:
                print(f"ERROR: page {mw['page']} mathwrite '{mw['id']}' seg '{seg['seg']}': "
                      "MathJax produced no SVG (bad TeX?)", file=sys.stderr)
                return 1
            seg["svg"] = make_unique_ids(seg["svg"], f"mw-{mw['id']}-{si}")

    for ov in payload.get("overlays", []):
        if ov["bbox"] is None:
            warnings.append(f"page {ov['page']} overlay '{ov['id']}': bbox not measurable; "
                            "it cannot be revealed in place (and is blanked in the PNG) — "
                            "check that its content renders to a non-empty box")

    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    n_segs = sum(len(mw["segs"]) for mw in payload["mathwrites"])
    n_ov = len(payload.get("overlays", []))
    print(f"[render_mathwrite] wrote {out_path}: {len(payload['mathwrites'])} block(s), "
          f"{n_segs} segment SVG(s), {n_ov} overlay bbox(es)")
    for w in warnings:
        print(f"[render_mathwrite] WARN: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
