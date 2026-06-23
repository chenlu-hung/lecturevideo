#!/usr/bin/env python3
"""Assemble the final HTML video player from the bundled assets and timeline.

Usage:
    python3 build_video.py <topic_dir>

Where <topic_dir> contains:
    timeline.json
    slides.images/01.png, 02.png, ...

Produces:
    <topic_dir>/video/
        index.html       (from assets/player/index.html, with TIMELINE injected)
        player.css       (copied verbatim)
        player.js        (copied verbatim)
        slides/01.png …  (symlinked or copied from ../slides.images/)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_ROOT / "assets" / "player"

TIMELINE_PLACEHOLDER = "/* __TIMELINE__ */"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: build_video.py <topic_dir>", file=sys.stderr)
        return 2

    topic_dir = Path(argv[1]).resolve()
    timeline_path = topic_dir / "timeline.json"
    images_src = topic_dir / "slides.images"

    if not timeline_path.is_file():
        print(f"ERROR: missing {timeline_path}", file=sys.stderr)
        return 1
    if not ASSETS_DIR.is_dir():
        print(f"ERROR: skill assets not found at {ASSETS_DIR}", file=sys.stderr)
        return 1

    video_dir = topic_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    video_slides_dir = video_dir / "slides"
    if video_slides_dir.is_symlink() or video_slides_dir.is_file():
        video_slides_dir.unlink()
    elif video_slides_dir.exists():
        shutil.rmtree(video_slides_dir)

    # Copy (not symlink) the slide images so video/ is self-contained. A symlink
    # breaks when video/ is opened from cloud-synced storage (Google Drive / Dropbox
    # / iCloud) or moved/shared — the browser can't resolve it and the stage goes
    # blank. The PNGs are small, so copying is cheap and robust.
    shutil.copytree(images_src, video_slides_dir)

    # Copy player.css, player.js, and the Hershey single-stroke font data verbatim.
    for fname in ("player.css", "player.js", "hershey-font.js"):
        src = ASSETS_DIR / fname
        if not src.is_file():
            print(f"ERROR: missing asset {src}", file=sys.stderr)
            return 1
        shutil.copy2(src, video_dir / fname)

    # Narration audio (optional): produced by synthesize_tts.py. When present the player
    # runs in audio mode; when absent it falls back to its internal timer clock. Always
    # reconcile the video/ copy so a stale track from a previous TTS run is not left behind.
    narration_src = topic_dir / "narration.wav"
    narration_dst = video_dir / "narration.wav"
    if narration_src.is_file():
        shutil.copy2(narration_src, narration_dst)
        print(f"[build_video] included narration audio ({narration_src.name})")
    elif narration_dst.exists():
        narration_dst.unlink()

    # Inject timeline into index.html.
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    # Rewrite slide image paths so they resolve under <video>/slides/.
    for slide in timeline.get("slides", []):
        original = slide.get("image", "")
        if original.startswith("slides.images/"):
            slide["image"] = "slides/" + original[len("slides.images/"):]
        elif "/" not in original:
            slide["image"] = "slides/" + original

    template = (ASSETS_DIR / "index.html").read_text(encoding="utf-8")
    if TIMELINE_PLACEHOLDER not in template:
        print(
            f"ERROR: index.html template missing placeholder {TIMELINE_PLACEHOLDER}",
            file=sys.stderr,
        )
        return 1

    payload = "const TIMELINE = " + json.dumps(timeline, ensure_ascii=False) + ";"
    rendered = template.replace(TIMELINE_PLACEHOLDER, payload)

    (video_dir / "index.html").write_text(rendered, encoding="utf-8")

    print(f"[build_video] wrote {video_dir}/index.html")
    print(f"[build_video] open file://{video_dir}/index.html in your browser")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
