# Module: `scripts`

## Summary
The deterministic, stdlib-only Python backbone of the lecture-video pipeline — the token-cheap work that is kept out of the LLM. `split_slides.py:120` parses a marp `slides.md` into per-page JSON (plain text + overlay annotations), `plan_subagent_batches.py:25` chunks those pages for the parallel narration sub-agents, `derive_timeline.py:112` folds the per-page SRT files into one global `timeline.json` (slide durations plus overlay show/hide times), and `build_video.py:31` assembles the browser player from `assets/player/` with that timeline injected. The optional voiced path `synthesize_tts.py:147` replaces `derive_timeline` when TTS is enabled: it drives the IndexTTS-2 MLX CLI to synthesize `narration.wav` and rebuilds `timeline.json` from the real audio (reusing `derive_timeline`'s `parse_srt` + overlay regexes), converting the spoken text Traditional→Simplified via `to_simplified`/`opencc` (`synthesize_tts.py:94`) since the tokenizer is Simplified-only. For hand-written math (mathwrite blocks), `render_mathwrite.py:135` typesets each declared segment's TeX to SVG via MathJax and measures the formula's on-slide bbox in one headless-Chrome pass over `slides.html`, writing `.mathwrite.json`; `derive_timeline.load_mathwrite_meta`/`build_page_mathwrites` (`derive_timeline.py:113,133`) merge that metadata with `[overlay:id.seg]` marker times into `timeline.mathwrites` for both silent and voiced paths. Both timeline producers also emit a `captions[]` array — one entry per spoken SRT cue with `[overlay:*]` markers stripped (`derive_timeline.strip_overlay_markers` at derive_timeline.py:42) and the original text kept — which the player shows as a synced subtitle bar. Each is a self-contained `main(...) -> int` CLI; the companion `compile_marp.sh` (not indexed) wraps marp-cli to render HTML/PDF/PNG and blanks `.mathwrite` divs in the PNG pass only.

<!-- projectmap:auto:start (generated — do not edit by hand) -->
## Files (6)
- `scripts/build_video.py`
- `scripts/derive_timeline.py`
- `scripts/plan_subagent_batches.py`
- `scripts/render_mathwrite.py`
- `scripts/split_slides.py`
- `scripts/synthesize_tts.py`

## Public symbols (27)
- `function main` — scripts/build_video.py:31
- `function strip_overlay_markers` — scripts/derive_timeline.py:42
- `function parse_timestamp` — scripts/derive_timeline.py:54
- `function parse_srt` — scripts/derive_timeline.py:58
- `function find_overlay_times` — scripts/derive_timeline.py:99
- `function load_mathwrite_meta` — scripts/derive_timeline.py:125
- `function build_page_mathwrites` — scripts/derive_timeline.py:145
- `function main` — scripts/derive_timeline.py:193
- `function resolve` — scripts/derive_timeline.py:283
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
