#!/usr/bin/env python3
"""Parse a marp slides.md into a structured JSON of pages and overlay annotations.

Usage:
    python3 split_slides.py <slides.md> <output.json>

Output JSON shape:
    {
      "pages": [
        {
          "index": 1,
          "raw_md": "...",
          "plain_text": "...",
          "image": "slides.images/01.png",
          "overlays": [{"id": "key-insight", "label": "關鍵推論"}]
        },
        ...
      ]
    }
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OVERLAY_BEGIN_RE = re.compile(
    r"<!--\s*overlay-begin:\s*id=([a-z0-9-]+)(?:,\s*label=\"([^\"]+)\")?\s*-->",
    re.IGNORECASE,
)
OVERLAY_END_RE = re.compile(
    r"<!--\s*overlay-end:\s*id=([a-z0-9-]+)\s*-->", re.IGNORECASE
)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
MARKDOWN_TOKEN_RE = re.compile(r"[#*_`>\-]+")


def split_pages(text: str) -> tuple[str, list[str]]:
    """Split marp markdown into (frontmatter, [page_text, ...]).

    Marp uses lines containing only `---` as slide separators. The first such
    block (between two `---` lines at the very top of the file) is the YAML
    frontmatter, not a separator.
    """
    lines = text.splitlines()
    n = len(lines)

    # Detect frontmatter.
    if n > 0 and lines[0].strip() == "---":
        # Find closing `---`.
        end = next((i for i in range(1, n) if lines[i].strip() == "---"), None)
        if end is not None:
            frontmatter = "\n".join(lines[: end + 1])
            body = lines[end + 1 :]
        else:
            frontmatter = ""
            body = lines
    else:
        frontmatter = ""
        body = lines

    # Split body on lines that are exactly `---`.
    pages: list[list[str]] = [[]]
    for line in body:
        if line.strip() == "---":
            pages.append([])
        else:
            pages[-1].append(line)

    # Strip leading/trailing blank lines per page; drop empties.
    cleaned: list[str] = []
    for p in pages:
        while p and p[0].strip() == "":
            p.pop(0)
        while p and p[-1].strip() == "":
            p.pop()
        if p:
            cleaned.append("\n".join(p))

    return frontmatter, cleaned


def extract_overlays(page_md: str) -> list[dict]:
    """Find overlay-begin/overlay-end pairs and return their {id, label} list."""
    overlays: list[dict] = []
    seen_ids: set[str] = set()

    for m in OVERLAY_BEGIN_RE.finditer(page_md):
        oid = m.group(1)
        label = m.group(2) or oid
        if oid in seen_ids:
            raise ValueError(f"Duplicate overlay-begin for id '{oid}'")
        seen_ids.add(oid)
        overlays.append({"id": oid, "label": label})

    end_ids = {m.group(1) for m in OVERLAY_END_RE.finditer(page_md)}
    begin_ids = seen_ids
    if begin_ids != end_ids:
        only_begin = begin_ids - end_ids
        only_end = end_ids - begin_ids
        msg_parts = []
        if only_begin:
            msg_parts.append(f"overlay-begin without matching overlay-end: {sorted(only_begin)}")
        if only_end:
            msg_parts.append(f"overlay-end without matching overlay-begin: {sorted(only_end)}")
        raise ValueError("; ".join(msg_parts))

    return overlays


def to_plain_text(page_md: str) -> str:
    """Strip markdown / HTML for sub-agent context (best-effort, not perfect)."""
    text = HTML_COMMENT_RE.sub("", page_md)
    text = MARKDOWN_TOKEN_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: split_slides.py <slides.md> <output.json>", file=sys.stderr)
        return 2

    src = Path(argv[1])
    dst = Path(argv[2])

    if not src.is_file():
        print(f"ERROR: not a file: {src}", file=sys.stderr)
        return 1

    text = src.read_text(encoding="utf-8")
    _frontmatter, page_texts = split_pages(text)

    pages = []
    for i, page_md in enumerate(page_texts, start=1):
        try:
            overlays = extract_overlays(page_md)
        except ValueError as exc:
            print(f"ERROR on page {i}: {exc}", file=sys.stderr)
            return 1
        pages.append(
            {
                "index": i,
                "raw_md": page_md,
                "plain_text": to_plain_text(page_md),
                "image": f"slides.images/{i:02d}.png",
                "overlays": overlays,
            }
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        json.dumps({"pages": pages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[split_slides] wrote {dst} with {len(pages)} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
