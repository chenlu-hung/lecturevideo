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

The player later draws these SVGs progressively (stroke-then-fill) inside the
measured bbox — which compile_marp.sh leaves blank in the PNG render.
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

PROBE_SCRIPT = """
<script>
window.MathJax = { svg: { fontCache: 'none' }, startup: { typeset: false } };
</script>
<script src="__MATHJAX_URL__"></script>
<script>
(async () => {
  const DATA = __MW_DATA__;
  const finish = (payload) => {
    const pre = document.createElement('pre');
    pre.id = '__MW_RESULT__';
    pre.textContent = JSON.stringify(payload);
    document.body.appendChild(pre);
  };
  try {
    await MathJax.startup.promise;
    // Force every slide to be laid out and visible so rects are measurable
    // (the bespoke template hides inactive slides).
    const style = document.createElement('style');
    style.textContent = [
      '.bespoke-marp-slide { display: block !important; visibility: visible !important;',
      '  position: static !important; transform: none !important; opacity: 1 !important; }',
      'svg[data-marpit-svg] { display: block !important; visibility: visible !important; }',
    ].join('\\n');
    document.head.appendChild(style);

    const slides = document.querySelectorAll('svg[data-marpit-svg]');
    const result = [];
    for (const item of DATA) {
      let bbox = null;
      const slide = slides[item.page - 1];
      if (slide) {
        const section = slide.querySelector('section');
        const els = section ? section.querySelectorAll('.mathwrite') : [];
        const el = els[item.ord];
        if (el && section) {
          const sr = section.getBoundingClientRect();
          const er = el.getBoundingClientRect();
          if (sr.width > 0 && sr.height > 0 && er.width > 0) {
            bbox = {
              x: (er.left - sr.left) / sr.width,
              y: (er.top - sr.top) / sr.height,
              w: er.width / sr.width,
              h: er.height / sr.height,
            };
          }
        }
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
    finish({ mathwrites: result });
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
    slides_html = topic_dir / "slides.html"
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

    if not items:
        out_path.write_text(json.dumps({"mathwrites": []}), encoding="utf-8")
        print("[render_mathwrite] no mathwrite blocks declared — wrote empty .mathwrite.json")
        return 0

    chrome = find_chrome(chrome_arg)
    if not chrome:
        print("ERROR: no Chrome/Chromium found. Install Google Chrome or set CHROME_PATH / --chrome.",
              file=sys.stderr)
        return 1

    probe = (slides_html.read_text(encoding="utf-8")
             + PROBE_SCRIPT
             .replace("__MATHJAX_URL__", mathjax_url)
             .replace("__MW_DATA__", json.dumps(items, ensure_ascii=False)))

    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=topic_dir,
                                     prefix=".mathwrite_probe_", delete=False,
                                     encoding="utf-8") as f:
        probe_path = Path(f.name)
        f.write(probe)

    try:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--virtual-time-budget=30000", "--dump-dom", probe_path.as_uri()]
        print(f"[render_mathwrite] probing {len(items)} mathwrite block(s) via headless Chrome …")
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

    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    n_segs = sum(len(mw["segs"]) for mw in payload["mathwrites"])
    print(f"[render_mathwrite] wrote {out_path}: {len(payload['mathwrites'])} block(s), "
          f"{n_segs} segment SVG(s)")
    for w in warnings:
        print(f"[render_mathwrite] WARN: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
