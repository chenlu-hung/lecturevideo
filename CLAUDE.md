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

# Phase 2 — flag any page whose content overflows the slide box (headless Chrome, no network).
# Exit 3 + one "page NN OVERFLOW by …px" line per offender; 0 when clean. Run after compile;
# on overflow, thin/split the page and re-compile until clean.
python3 scripts/check_fit.py output/<slug>

# Phase 2 — parse slides.md into pages + overlay/mathwrite metadata
python3 scripts/split_slides.py output/<slug>/slides.md output/<slug>/.slides.json

# Phase 2 — ONLY when the deck declares mathwrite blocks (hand-written math animation):
# typeset each segment's TeX to SVG (MathJax CDN) and measure the blanked formula bbox,
# in one headless-Chrome pass over slides.html (Chrome auto-detected; CHROME_PATH/--chrome
# to override). Writes .mathwrite.json — both timeline producers refuse to run without it
# when mathwrites are declared.
python3 scripts/render_mathwrite.py output/<slug>

# Phase 3 — plan how pages split across parallel narration sub-agents (prints JSON)
python3 scripts/plan_subagent_batches.py output/<slug>/.slides.json 5

# Phase 4 — fold per-page SRTs + slide metadata into one global timeline (SILENT path)
python3 scripts/derive_timeline.py output/<slug>/scripts output/<slug>/.slides.json output/<slug>/timeline.json

# Phase 4 — VOICED path (replaces derive_timeline): synthesize narration via IndexTTS-2,
# write narration.mp3 (--audio-format wav/both for the lossless track), and rebuild
# timeline.json from the real audio. Needs a built
# IndexTTS-2 MLX CLI (Apple Silicon) + a reference voice. Compute-heavy (minutes).
python3 scripts/synthesize_tts.py output/<slug> --ref voice.wav --indextts2-dir "$INDEXTTS2_DIR"

# Phase 4 — assemble the browser player (copies assets/player/*, injects timeline,
# and copies narration.{mp3,wav} into video/ if present)
python3 scripts/build_video.py output/<slug>
# then open output/<slug>/video/index.html

# Phase 5 (optional) — export the built player to MP4. Steps the player frame-by-frame
# in headless Chrome (raw CDP over Node's built-in WebSocket — no npm deps) and pipes
# screenshots into ffmpeg (H.264 + AAC). Needs Chrome + ffmpeg. Compute-heavy (minutes).
node scripts/export_mp4.mjs output/<slug>           # → output/<slug>/video/lecture.mp4
# defaults: 30fps, size auto from slide aspect (height capped 1080), JPEG q92 capture, and
# min(6, cores-2) parallel Chrome workers; --fps/--width/--height/--crf/--preset/--workers/
# --jpeg-quality/--png to tune.
# Speed comes from two independent savings:
#   (a) It reads timeline.json and only screenshots frames that actually change — every mathwrite
#       (hand-written math) frame is rendered per-frame (the sole true f(t) animation), while static
#       stretches (slides/overlay/caption changes are discrete under frame-stepped seek) reuse one
#       cached screenshot. On a typical deck that's ~6x fewer screenshots.
#   (b) Capturing is ~99% of what's left (a 1080p screenshot costs ~85ms against a ~0.5ms renderAt
#       round-trip, and one Chrome saturates one core), so the frame range is split across N Chrome
#       instances that each encode their own chunk .mp4 into video/.export-chunks/; the chunks are
#       concatenated by stream copy and the narration muxed in at the end. Measured on an 8-core M1:
#       1.98x at 2 workers, 3.6x at 4, 4.3x at 6. JPEG capture is a further ~1.25x over PNG (the
#       frames go through libx264 anyway); --png restores lossless capture.
# Pass --fps 60 for smoother handwriting at ~2x the time.
```

The silent and voiced timeline producers are **mutually exclusive** — run exactly one before
`build_video.py`. `synthesize_tts.py` emits its own audio-accurate `timeline.json`, so running
`derive_timeline.py` afterwards would clobber it with SRT-estimated times.

All Python scripts are **stdlib-only** (≥ 3.8, no `pip install`). To smoke-test one in
isolation, run it with no args — each prints its own usage and exits `2`. The one non-Python
tool is `scripts/export_mp4.mjs` (Node ≥ 22, **no npm deps** — it speaks the Chrome DevTools
Protocol over Node's built-in `WebSocket`); like the marp wrapper it relies only on tools
already required elsewhere (Chrome + ffmpeg).

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
4. **`timeline.json`**: produced one of two ways, both honoring the same overlay contract:
   - **Silent** — `derive_timeline.py` concatenates page durations (page N's global start =
     sum of prior page durations; default 20s when a page's SRT is missing/empty) to convert all
     page-local times to **absolute** times, resolving each `[overlay:*]` pair via
     `find_overlay_times` (opener cue's start, closer cue's end).
   - **Voiced** — `synthesize_tts.py` instead measures the **real** synthesized audio: page/
     overlay times come from where each cue actually lands in `narration.wav`, slide windows are
     kept contiguous, and it adds an `"audio": "narration.wav"` field. It strips `[overlay:*]`
     markers before speaking and reuses `derive_timeline`'s `parse_srt`/overlay regexes. It also
     converts the spoken text **Traditional→Simplified via `opencc`** (`--zh-convert`, default
     `auto`) — IndexTTS-2's tokenizer is Simplified-only, so Traditional chars are out-of-vocab
     and mispronounced. Only the audio's input is converted; slides/SRT stay Traditional.
5. **player** (`assets/player/player.js`): consumes the `TIMELINE` global, switches slide
   `<img>`s, fades overlay badges in/out, and shows the active **caption** at those absolute
   times (`CC` button / `c` key toggles the subtitle bar).

Both timeline producers also emit a `captions[]` array — one entry per spoken SRT cue,
`[overlay:*]` markers stripped, **original** (Traditional) text kept, timed from the same
source as everything else (SRT-estimated when silent, real audio when voiced). The player
renders it in a bottom subtitle bar so even a silent deck is followable; the bar works in
both clock modes. Captions never feed the TTS engine — only the separately Simplified-
converted spoken text does.

**Mathwrite** (hand-written math animation) rides the same contract with two extras: the
slides.md grammar is `mathwrite-begin/seg/end` comments around a mandatory
`<div class="mathwrite">` holding the real `$$…$$` (HTML/PDF keep the formula; the PNG pass
in `compile_marp.sh` hides the div so the player can write into the blank region). Each
declared seg is timed exactly like an overlay whose SRT marker id is `<id>.<seg>` (the
overlay regexes allow dots). `render_mathwrite.py` contributes the per-seg MathJax SVGs and
the formula bbox via `.mathwrite.json`; `derive_timeline.build_page_mathwrites` merges meta
+ times into `timeline.mathwrites` for both silent and voiced paths. The player draws each
seg as a pure function of t with **true single-stroke handwriting**: each MathJax glyph
outline is *replaced* by its Hershey single-stroke centerline (`assets/player/hershey-font.js`,
keyed in `player.js` `mwCodeToKey`: upright math-alphanumeric→ASCII, **math-italic
variables→`c:<char>` in a joined cursive hand**, Greek→`g:<slot>` (lower *and* upper case),
a few hand-authored math symbols `s:<name>` like the integral, minus→`-`) fit into the glyph's own
box (y-flipped, since Hershey is y-down and MathJax glyph-local is y-up), then revealed by
sweeping `stroke-dashoffset` along that centerline — a real pen trajectory, **not** an
outline trace or a fade. Pen width is a constant in font units (`MW_PEN_EM`) so the ink reads
as one uniform chalk/marker stroke, sized for the tightest loops in the set (the cursive
variables and the denser Greek letters — a fatter nib fills those in solid); `<rect>` rules
grow by width; a cursive glyph the script font lacks writes upright, and a glyph with no
Hershey mapping at all degrades to a clean opacity fade (never an outline trace); invisible operators
(U+2061…) are skipped. The single `.mw-pen` nib rides the **true stroke frontier**
(`getPointAtLength`, mapped through the SVG/CSS transforms). `mwCollectNodes` defers (leaves
`seg.nodes` null to retry) while the box is unlaid-out, so it never caches an all-fallback
state. Seeking lands on the correct half-written state. A seg with missing SRT markers
degrades to "fully drawn from slide start" (warned, never blank). The single-stroke font data
is generated by `scripts/gen_hershey_font.py` from the bundled Hershey sources in
`assets/hershey/` — `rowmans.jhf` (upright Latin), `greeks.jhf` (Greek), and the single-line SVG
font `HersheyScriptMed.svg` (cursive); all three are **simplex** cuts, i.e. one line per stroke —
the Complex/Duplex cuts fake a heavier weight with two parallel lines, which the player would
write out as a doubled pen trajectory. **`greeks.jhf` orders its glyphs by position in the Greek
alphabet over the Latin slots** — `'a'`=α, `'b'`=β, `'c'`=γ … `'x'`=ω, lowercase on `a..x` and
uppercase on `A..X` — *not* by transliteration; `MW_GSEQ`/`MW_GSEQ_UP` index into those slots and
must stay in step with the file. The Hershey data is free for any use *provided its
acknowledgement travels with the font data* — it is not public domain — so
`assets/hershey/NOTICE` carries the terms and `gen_hershey_font.py` stamps the
acknowledgement into the generated `hershey-font.js` header. Keep both when editing.

`build_video.py` injects the timeline into `assets/player/index.html` by replacing the
`/* __TIMELINE__ */` placeholder and rewrites slide image paths from `slides.images/NN.png`
to `slides/NN.png` (it **copies** the PNGs into `video/slides/` so `video/` is self-contained —
a symlink breaks when the folder is opened from cloud storage like Google Drive).

## Conventions and gotchas

- **Never regenerate the player assets** (`assets/player/{index.html,player.css,player.js}`) as
  part of running the pipeline — `build_video.py` copies them verbatim; edit them only as a
  deliberate change to the player itself.
- **Script CLI shape:** each Python tool is `main(argv) -> int` with exit codes `2` = usage,
  `1` = runtime error, `0` = ok; progress prints `[<name>] …` to stdout, warnings/errors to
  stderr. `derive_timeline.py` warns (not fails) on a missing/empty page SRT.
- **The player has two clocks:** it prefers the `<audio>` narration track (`tryAudio`, 1.5s
  detection timeout) and falls back to a `requestAnimationFrame` timer when no audio is present.
  The track is `video/narration.mp3` (or `.wav`), produced by the voiced Phase 4 path; the
  silent path leaves it absent so the player stays on the timer. `synthesize_tts.py`
  `--audio-format` decides which is delivered (default `mp3` — a 50-min lecture drops from
  ~135 MB to ~25 MB, which matters in cloud-synced folders) and records it in `timeline.json`'s
  `"audio"` field. `assets/player/index.html` lists a `<source>` for **both** names and the
  browser falls through to whichever exists; `build_video.py` copies exactly one in and deletes
  the other — keep those three in sync when adding a format.
- **Resume vs redo:** `output/<slug>/.state.json` records completed phases. On re-invocation the
  skill resumes from the first incomplete phase unless the user explicitly asks to redo one; the
  exact downstream-invalidation rules are the redo table in `references/workflow.md`.
- **Slugify preserves CJK** characters (this skill is bilingual ZH/EN); slides/narration/theme
  are all CJK-friendly.
- `assets/marp/theme.css` declares theme name `input` and is the default when no
  `marp_template_path` is given; a custom theme must declare its own `/* @theme <name> */`.
- **Theme size must convert to an integer pixel count** — newer Chrome rejects fractional
  device-metrics widths during PNG export. The bundled theme is `960pt × 540pt` (16:9), which
  is exactly `1280 × 720px` (1pt = 4/3px), and `compile_marp.sh`'s `--image-scale 1.5` lands
  on a whole `1920 × 1080`. Avoid a size whose px equivalent is fractional (e.g. `1024pt` =
  `1365.33px`). Custom themes with pt sizes have the same constraint.
- **The theme is top-anchored, not centered** — `section` overrides the imported marp default
  theme's vertical centering (`place-content`) with `justify-content: flex-start` so pages of
  any fullness lay out from the top. Content that exceeds the box is clipped at the bottom;
  `scripts/check_fit.py` is the guard, and `references/marp-and-overlays.md` §"Layout & density"
  is the authoring budget that keeps pages fitting.

## Project map

A `.projectmap/` index exists — use it before broad exploration:
- Read `.projectmap/ARCHITECTURE.md` for the module map, entry points, and conventions.
- To locate a symbol, grep `.projectmap/tags` (ctags format) instead of scanning the repo.
- Open `.projectmap/modules/<name>.md` only for the module you're working in.
Re-run `/project-map update` after substantial changes.
