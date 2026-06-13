# Module: `scripts`

## Summary
The deterministic, stdlib-only Python backbone of the lecture-video pipeline — the token-cheap work that is kept out of the LLM. `split_slides.py:120` parses a marp `slides.md` into per-page JSON (plain text + overlay annotations), `plan_subagent_batches.py:25` chunks those pages for the parallel narration sub-agents, `derive_timeline.py:112` folds the per-page SRT files into one global `timeline.json` (slide durations plus overlay show/hide times), and `build_video.py:31` assembles the browser player from `assets/player/` with that timeline injected. The optional voiced path `synthesize_tts.py:147` replaces `derive_timeline` when TTS is enabled: it drives the IndexTTS-2 MLX CLI to synthesize `narration.wav` and rebuilds `timeline.json` from the real audio (reusing `derive_timeline`'s `parse_srt` + overlay regexes), converting the spoken text Traditional→Simplified via `to_simplified`/`opencc` (`synthesize_tts.py:94`) since the tokenizer is Simplified-only. For hand-written math (mathwrite blocks), `render_mathwrite.py:135` typesets each declared segment's TeX to SVG via MathJax and measures the formula's on-slide bbox in one headless-Chrome pass over `slides.html`, writing `.mathwrite.json`; `derive_timeline.load_mathwrite_meta`/`build_page_mathwrites` (`derive_timeline.py:113,133`) merge that metadata with `[overlay:id.seg]` marker times into `timeline.mathwrites` for both silent and voiced paths. Both timeline producers also emit a `captions[]` array — one entry per spoken SRT cue with `[overlay:*]` markers stripped (`derive_timeline.strip_overlay_markers` at derive_timeline.py:42) and the original text kept — which the player shows as a synced subtitle bar. The one non-Python tool is `export_mp4.mjs:190` (optional Phase 5): a dependency-free Node ≥ 22 script that drives headless Chrome over the DevTools Protocol via a hand-rolled `CDP` client (`export_mp4.mjs:122`), steps the built player frame-by-frame through its `window.__lectureExport.renderAt(t)` hook, and pipes screenshots into ffmpeg to produce `video/lecture.mp4`. Each Python tool is a self-contained `main(argv) -> int` CLI; the companion `compile_marp.sh` (not indexed) wraps marp-cli to render HTML/PDF/PNG and blanks `.mathwrite` divs in the PNG pass only.

<!-- projectmap:auto:start (generated — do not edit by hand) -->
## Files (7)
- `scripts/build_video.py`
- `scripts/derive_timeline.py`
- `scripts/export_mp4.mjs`
- `scripts/plan_subagent_batches.py`
- `scripts/render_mathwrite.py`
- `scripts/split_slides.py`
- `scripts/synthesize_tts.py`

## Public symbols (46)
- `function main` — scripts/build_video.py:31
- `function strip_overlay_markers` — scripts/derive_timeline.py:42
- `function parse_timestamp` — scripts/derive_timeline.py:54
- `function parse_srt` — scripts/derive_timeline.py:58
- `function find_overlay_times` — scripts/derive_timeline.py:99
- `function load_mathwrite_meta` — scripts/derive_timeline.py:125
- `function build_page_mathwrites` — scripts/derive_timeline.py:145
- `function main` — scripts/derive_timeline.py:193
- `function resolve` — scripts/derive_timeline.py:283
- `function parseArgs` — scripts/export_mp4.mjs:42
- `function next` — scripts/export_mp4.mjs:50
- `function findChrome` — scripts/export_mp4.mjs:78
- `function even` — scripts/export_mp4.mjs:94
- `function readPngSize` — scripts/export_mp4.mjs:96
- `function resolveSize` — scripts/export_mp4.mjs:108
- `class CDP` — scripts/export_mp4.mjs:122
- `method constructor` — scripts/export_mp4.mjs:123
- `method _onMessage` — scripts/export_mp4.mjs:130
- `method on` — scripts/export_mp4.mjs:142
- `method send` — scripts/export_mp4.mjs:146
- `function connect` — scripts/export_mp4.mjs:157
- `function launchChrome` — scripts/export_mp4.mjs:166
- `function onData` — scripts/export_mp4.mjs:177
- `function sleep` — scripts/export_mp4.mjs:188
- `function main` — scripts/export_mp4.mjs:190
- `function cleanup` — scripts/export_mp4.mjs:221
- `function sess` — scripts/export_mp4.mjs:232
- `function writeFrame` — scripts/export_mp4.mjs:279
- `function main` — scripts/plan_subagent_batches.py:25
- `function find_chrome` — scripts/render_mathwrite.py:109
- `function make_unique_ids` — scripts/render_mathwrite.py:130
- `function main` — scripts/render_mathwrite.py:135
- `function split_pages` — scripts/split_slides.py:50
- `function extract_overlays` — scripts/split_slides.py:95
- `function extract_mathwrites` — scripts/split_slides.py:123
- `function to_plain_text` — scripts/split_slides.py:173
- `function main` — scripts/split_slides.py:181
- `function strip_markers` — scripts/synthesize_tts.py:55
- `function parse_args` — scripts/synthesize_tts.py:62
- `function to_simplified` — scripts/synthesize_tts.py:96
- `function resolve_indextts2` — scripts/synthesize_tts.py:131
- `function main` — scripts/synthesize_tts.py:149
- `function seg_path` — scripts/synthesize_tts.py:242
- `function t` — scripts/synthesize_tts.py:306
- `function resolve` — scripts/synthesize_tts.py:359
- `function _find_overlay_cues` — scripts/synthesize_tts.py:392

## Dependencies (imports)
- `__future__`
- `argparse`
- `derive_timeline`
- `html`
- `json`
- `os`
- `pathlib`
- `re`
- `shutil`
- `subprocess`
- `sys`
- `tempfile`
- `wave`
<!-- projectmap:auto:end -->
