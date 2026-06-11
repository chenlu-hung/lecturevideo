# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This repo is itself a **self-contained Claude Code agent skill** (`lecture-video-generator`),
not a conventional application. `SKILL.md` is the entry point: it defines a 4-phase pipeline
that an agent runs to turn a topic into a teaching video (outline → marp slides → per-page SRT
narration written by parallel sub-agents → an auto-play HTML player). The Python/shell scripts
and the JS player are the deterministic machinery the skill drives; the writing (outline,
slides, narration) is done by the LLM/sub-agents. There is **no external dependency on any
other plugin or skill** — everything needed is bundled here.

Read `SKILL.md` first when changing behaviour; it is the spec the scripts serve. Deep,
load-on-demand detail lives in `references/` (workflow/redo matrix, marp+overlay grammar,
SRT timing contract, sub-agent prompt template, player internals).

## Pipeline commands

There is no build system and **no test suite**. To develop or exercise the pipeline you run
the scripts directly against an `output/<slug>/` working folder (`output/` is gitignored).
Paths below are relative to the repo root.

```bash
# Phase 2 — render marp deck to HTML + PDF + per-page PNG (needs node/npx; auto-installs marp-cli)
bash scripts/compile_marp.sh output/<slug>/slides.md assets/marp/theme.css output/<slug>

# Phase 2 — parse slides.md into pages + overlay metadata
python3 scripts/split_slides.py output/<slug>/slides.md output/<slug>/.slides.json

# Phase 3 — plan how pages split across parallel narration sub-agents (prints JSON)
python3 scripts/plan_subagent_batches.py output/<slug>/.slides.json 5

# Phase 4 — fold per-page SRTs + slide metadata into one global timeline
python3 scripts/derive_timeline.py output/<slug>/scripts output/<slug>/.slides.json output/<slug>/timeline.json

# Phase 4 — assemble the browser player (copies assets/player/*, injects timeline)
python3 scripts/build_video.py output/<slug>
# then open output/<slug>/video/index.html
```

All Python scripts are **stdlib-only** (≥ 3.8, no `pip install`). To smoke-test one in
isolation, run it with no args — each prints its own usage and exits `2`.

## Architecture: the cross-format overlay + timing contract

The non-obvious core is how an "overlay" (a highlight that appears only while it's being
narrated) and slide timing propagate through four different file formats. Changing any one
format means updating its producer and consumer together:

1. **marp markdown** (`slides.md`): `<!-- overlay-begin: id=…, label="…" -->` …
   `<!-- overlay-end: id=… -->`. Parsed by `split_slides.py` (`OVERLAY_BEGIN_RE`/`OVERLAY_END_RE`),
   which also enforces balanced begin/end pairs per page.
2. **`.slides.json`**: per-page `{index, raw_md, plain_text, image, overlays[]}`. Intermediate
   handoff from the marp deck to the narration sub-agents.
3. **per-page SRT** (`scripts/NN.srt`): each file starts at `00:00:00,000` (page-local time).
   Sub-agents wrap the narrated sentences for an overlay in `[overlay:id]` … `[/overlay:id]`.
4. **`timeline.json`**: `derive_timeline.py` concatenates page durations (page N's global start =
   sum of prior page durations; default 20s when a page's SRT is missing/empty) to convert all
   page-local times to **absolute** times, and resolves each `[overlay:*]` marker pair to
   absolute `{start, end}`. `find_overlay_times` takes the opener cue's start and the closer
   cue's end.
5. **player** (`assets/player/player.js`): consumes the `TIMELINE` global, switches slide
   `<img>`s and fades overlay badges in/out at those absolute times.

`build_video.py` injects the timeline into `assets/player/index.html` by replacing the
`/* __TIMELINE__ */` placeholder and rewrites slide image paths from `slides.images/NN.png`
to `slides/NN.png` (it symlinks the PNG dir into `video/`).

## Conventions and gotchas

- **Never regenerate the player assets** (`assets/player/{index.html,player.css,player.js}`) as
  part of running the pipeline — `build_video.py` copies them verbatim; edit them only as a
  deliberate change to the player itself.
- **Script CLI shape:** each Python tool is `main(argv) -> int` with exit codes `2` = usage,
  `1` = runtime error, `0` = ok; progress prints `[<name>] …` to stdout, warnings/errors to
  stderr. `derive_timeline.py` warns (not fails) on a missing/empty page SRT.
- **The player has two clocks:** it prefers a `<audio>` narration track (`tryAudio`, 1.5s
  detection timeout) and falls back to a `requestAnimationFrame` timer when no audio is present.
  The `<audio>` slot is the reserved extension point for future TTS — keep it intact.
- **Resume vs redo:** `output/<slug>/.state.json` records completed phases. On re-invocation the
  skill resumes from the first incomplete phase unless the user explicitly asks to redo one; the
  exact downstream-invalidation rules are the redo table in `references/workflow.md`.
- **Slugify preserves CJK** characters (this skill is bilingual ZH/EN); slides/narration/theme
  are all CJK-friendly.
- `assets/marp/theme.css` declares theme name `input` and is the default when no
  `marp_template_path` is given; a custom theme must declare its own `/* @theme <name> */`.

## Project map

A `.projectmap/` index exists — use it before broad exploration:
- Read `.projectmap/ARCHITECTURE.md` for the module map, entry points, and conventions.
- To locate a symbol, grep `.projectmap/tags` (ctags format) instead of scanning the repo.
- Open `.projectmap/modules/<name>.md` only for the module you're working in.
Re-run `/project-map update` after substantial changes.
