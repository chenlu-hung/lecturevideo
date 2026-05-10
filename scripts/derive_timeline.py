#!/usr/bin/env python3
"""Derive a global timeline.json from per-page SRT files and slide metadata.

Usage:
    python3 derive_timeline.py <scripts_dir> <.slides.json> <timeline.json>

Reads:
    <scripts_dir>/01.srt, 02.srt, ... — each starts at 00:00:00,000.
    <.slides.json> — structured page list with overlay declarations.

Writes:
    <timeline.json> — global timeline (slides + overlays + total duration).

Algorithm:
    1. Parse each SRT into cues (index, start_local, end_local, text).
    2. Per page, find max(end_local) → page duration; default 20s if SRT empty.
    3. Concatenate: page N global_start = sum(durations[k < N]).
    4. For each overlay declared on a page, scan that page's cues for
       [overlay:id] (opener) and [/overlay:id] (closer); convert local times
       to global; produce {slide, id, label, start, end}.
    5. Validate every declared overlay was tagged exactly once. Warn but do
       not fail when a page's SRT is missing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
OVERLAY_OPEN_RE = re.compile(r"\[overlay:([a-z0-9-]+)\]")
OVERLAY_CLOSE_RE = re.compile(r"\[/overlay:([a-z0-9-]+)\]")

DEFAULT_PAGE_DURATION = 20.0  # fallback when SRT missing/empty


def parse_timestamp(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: Path) -> list[dict]:
    """Return a list of {index, start, end, text} cues. Empty list if missing."""
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []

    cues: list[dict] = []
    blocks = re.split(r"\r?\n\r?\n", raw)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        idx_line = lines[0].strip()
        ts_line = lines[1].strip() if len(lines) >= 2 else ""
        m = TIMESTAMP_RE.match(ts_line)
        if not m:
            # Maybe the index line is missing; try parsing line 0 as timestamp.
            m = TIMESTAMP_RE.match(idx_line)
            if not m:
                continue
            text_lines = lines[1:]
        else:
            text_lines = lines[2:]
        start = parse_timestamp(*m.group(1, 2, 3, 4))
        end = parse_timestamp(*m.group(5, 6, 7, 8))
        cues.append(
            {
                "index": len(cues) + 1,
                "start": start,
                "end": end,
                "text": "\n".join(text_lines),
            }
        )
    return cues


def find_overlay_times(cues: list[dict], overlay_id: str) -> tuple[float, float] | None:
    """Find local start/end times for an overlay id within a page's cues.

    The opener `[overlay:id]` may appear in any cue; that cue's start time
    is the overlay's local start. The closer `[/overlay:id]` may appear in
    any later cue (or the same one); that cue's end time is the overlay's
    local end. Returns None if the markers are unbalanced or missing.
    """
    open_cue: dict | None = None
    close_cue: dict | None = None

    for cue in cues:
        if open_cue is None and re.search(r"\[overlay:" + re.escape(overlay_id) + r"\]", cue["text"]):
            open_cue = cue
        if re.search(r"\[/overlay:" + re.escape(overlay_id) + r"\]", cue["text"]):
            close_cue = cue
            if open_cue is not None:
                break

    if open_cue is None or close_cue is None:
        return None
    if close_cue["start"] < open_cue["start"]:
        return None
    return open_cue["start"], close_cue["end"]


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "Usage: derive_timeline.py <scripts_dir> <.slides.json> <timeline.json>",
            file=sys.stderr,
        )
        return 2

    scripts_dir = Path(argv[1])
    slides_json = Path(argv[2])
    out_path = Path(argv[3])

    if not slides_json.is_file():
        print(f"ERROR: missing {slides_json}", file=sys.stderr)
        return 1

    slides_data = json.loads(slides_json.read_text(encoding="utf-8"))
    pages = slides_data["pages"]

    timeline_slides = []
    timeline_overlays = []
    warnings: list[str] = []

    cursor = 0.0
    for page in pages:
        idx = page["index"]
        srt_path = scripts_dir / f"{idx:02d}.srt"
        cues = parse_srt(srt_path)

        if not cues:
            duration = DEFAULT_PAGE_DURATION
            warnings.append(f"page {idx}: no SRT cues found; using {duration}s default")
        else:
            duration = max(cue["end"] for cue in cues)
            if duration <= 0:
                duration = DEFAULT_PAGE_DURATION
                warnings.append(f"page {idx}: SRT has zero/negative duration; using {duration}s")

        global_start = cursor
        global_end = cursor + duration
        timeline_slides.append(
            {
                "index": idx,
                "start": round(global_start, 3),
                "end": round(global_end, 3),
                "image": page.get("image", f"slides.images/{idx:02d}.png"),
            }
        )

        for overlay in page.get("overlays", []):
            oid = overlay["id"]
            label = overlay.get("label", oid)
            times = find_overlay_times(cues, oid)
            if times is None:
                warnings.append(
                    f"page {idx}: overlay '{oid}' missing or unbalanced [overlay:*] markers in SRT"
                )
                continue
            local_start, local_end = times
            timeline_overlays.append(
                {
                    "slide": idx,
                    "id": oid,
                    "label": label,
                    "start": round(global_start + local_start, 3),
                    "end": round(global_start + local_end, 3),
                }
            )

        cursor = global_end

    timeline = {
        "total_duration": round(cursor, 3),
        "slides": timeline_slides,
        "overlays": timeline_overlays,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[derive_timeline] wrote {out_path}: {len(timeline_slides)} slides, "
          f"{len(timeline_overlays)} overlays, total {timeline['total_duration']}s")
    for w in warnings:
        print(f"[derive_timeline] WARN: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
