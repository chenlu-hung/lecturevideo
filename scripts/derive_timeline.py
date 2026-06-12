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
# The id may contain a dot: plain overlays use `id`, mathwrite segments use `id.seg`.
OVERLAY_OPEN_RE = re.compile(r"\[overlay:([a-z0-9.-]+)\]")
OVERLAY_CLOSE_RE = re.compile(r"\[/overlay:([a-z0-9.-]+)\]")

DEFAULT_PAGE_DURATION = 20.0  # fallback when SRT missing/empty


def strip_overlay_markers(text: str) -> str:
    """Drop `[overlay:*]`/`[/overlay:*]` markers, keeping the spoken text.

    Used to derive on-screen captions from the narration SRT (the markers drive
    overlay/mathwrite timing but must never be shown to the viewer). Newlines are
    collapsed to single spaces so a multi-line cue renders as one caption line.
    """
    text = OVERLAY_OPEN_RE.sub("", text)
    text = OVERLAY_CLOSE_RE.sub("", text)
    return " ".join(text.split())


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


def load_mathwrite_meta(topic_dir: Path, pages: list[dict]) -> dict | None:
    """Return {(page_index, id): meta} from .mathwrite.json, or None when the deck
    declares no mathwrites. Raises SystemExit if mathwrites are declared but the
    rendered metadata is missing (render_mathwrite.py was not run).
    """
    if not any(page.get("mathwrites") for page in pages):
        return None
    meta_path = topic_dir / ".mathwrite.json"
    if not meta_path.is_file():
        raise SystemExit(
            f"ERROR: deck declares mathwrite blocks but {meta_path} is missing — "
            "run scripts/render_mathwrite.py first"
        )
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return {(m["page"], m["id"]): m for m in data.get("mathwrites", [])}


FALLBACK_BBOX = {"x": 0.1, "y": 0.35, "w": 0.8, "h": 0.3}


def build_page_mathwrites(page: dict, page_start: float, mw_meta: dict,
                          resolve, warnings: list[str]) -> list[dict]:
    """Assemble timeline mathwrite entries for one page.

    `resolve(marker_id) -> (global_start, global_end) | None` maps a
    `[overlay:id.seg]` marker pair to absolute times (SRT-estimated in the
    silent path, real-audio in the voiced path). A segment whose markers are
    missing falls back to a zero-length window at the slide start, so the
    formula still appears (fully drawn) instead of vanishing — the PNG region
    under it is blank.
    """
    idx = page["index"]
    entries: list[dict] = []
    for mw in page.get("mathwrites", []):
        mid = mw["id"]
        meta = mw_meta.get((idx, mid))
        if meta is None:
            warnings.append(f"page {idx}: mathwrite '{mid}' missing from .mathwrite.json — skipped")
            continue
        meta_segs = {s["seg"]: s for s in meta["segs"]}
        bbox = meta.get("bbox") or FALLBACK_BBOX
        segs: list[dict] = []
        for seg in mw["segs"]:
            sid = seg["seg"]
            rendered = meta_segs.get(sid)
            if rendered is None:
                warnings.append(f"page {idx}: mathwrite '{mid}' seg '{sid}' has no rendered SVG — skipped")
                continue
            marker = f"{mid}.{sid}"
            times = resolve(marker)
            if times is None:
                warnings.append(
                    f"page {idx}: mathwrite seg '{marker}' missing/unbalanced [overlay:*] "
                    "markers in SRT; drawing it instantly at slide start"
                )
                times = (page_start, page_start)
            segs.append({
                "seg": sid,
                "svg": rendered["svg"],
                "valign": rendered.get("valign", "0"),
                "start": round(times[0], 3),
                "end": round(times[1], 3),
            })
        if segs:
            entries.append({"slide": idx, "id": mid, "bbox": bbox, "segs": segs})
    return entries


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

    try:
        mw_meta = load_mathwrite_meta(slides_json.parent, pages)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    timeline_slides = []
    timeline_overlays = []
    timeline_mathwrites = []
    timeline_captions = []
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

        for cue in cues:
            ctext = strip_overlay_markers(cue["text"])
            if not ctext:
                continue
            timeline_captions.append(
                {
                    "start": round(global_start + cue["start"], 3),
                    "end": round(global_start + cue["end"], 3),
                    "text": ctext,
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

        if mw_meta is not None:
            def resolve(marker: str, _cues=cues, _gs=global_start):
                times = find_overlay_times(_cues, marker)
                if times is None:
                    return None
                return _gs + times[0], _gs + times[1]

            timeline_mathwrites.extend(
                build_page_mathwrites(page, global_start, mw_meta, resolve, warnings)
            )

        cursor = global_end

    timeline = {
        "total_duration": round(cursor, 3),
        "slides": timeline_slides,
        "overlays": timeline_overlays,
        "captions": timeline_captions,
    }
    if timeline_mathwrites:
        timeline["mathwrites"] = timeline_mathwrites

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[derive_timeline] wrote {out_path}: {len(timeline_slides)} slides, "
          f"{len(timeline_overlays)} overlays, {len(timeline_captions)} captions, "
          f"total {timeline['total_duration']}s")
    for w in warnings:
        print(f"[derive_timeline] WARN: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
