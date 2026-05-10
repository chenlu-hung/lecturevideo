#!/usr/bin/env python3
"""Plan how to distribute pages across parallel sub-agents.

Usage:
    python3 plan_subagent_batches.py <.slides.json> <pages_per_subagent>

Prints a JSON object to stdout:
    {
      "total_pages": 12,
      "pages_per_subagent": 5,
      "batches": [
        {"id": 0, "pages": [1, 2, 3, 4, 5]},
        {"id": 1, "pages": [6, 7, 8, 9, 10]},
        {"id": 2, "pages": [11, 12]}
      ]
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "Usage: plan_subagent_batches.py <.slides.json> <pages_per_subagent>",
            file=sys.stderr,
        )
        return 2

    slides_path = Path(argv[1])
    try:
        per_batch = int(argv[2])
    except ValueError:
        print("pages_per_subagent must be an integer", file=sys.stderr)
        return 2

    if per_batch < 1:
        print("pages_per_subagent must be ≥ 1", file=sys.stderr)
        return 2

    data = json.loads(slides_path.read_text(encoding="utf-8"))
    page_indices = [p["index"] for p in data["pages"]]

    batches = []
    for batch_id, start in enumerate(range(0, len(page_indices), per_batch)):
        chunk = page_indices[start : start + per_batch]
        batches.append({"id": batch_id, "pages": chunk})

    out = {
        "total_pages": len(page_indices),
        "pages_per_subagent": per_batch,
        "batches": batches,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
