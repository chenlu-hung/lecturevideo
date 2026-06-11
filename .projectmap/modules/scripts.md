# Module: `scripts`

## Summary
The deterministic, stdlib-only Python backbone of the lecture-video pipeline — the token-cheap work that is kept out of the LLM. `split_slides.py:120` parses a marp `slides.md` into per-page JSON (plain text + overlay annotations), `plan_subagent_batches.py:25` chunks those pages for the parallel narration sub-agents, `derive_timeline.py:112` folds the per-page SRT files into one global `timeline.json` (slide durations plus overlay show/hide times), and `build_video.py:31` assembles the browser player from `assets/player/` with that timeline injected. The optional voiced path `synthesize_tts.py:147` replaces `derive_timeline` when TTS is enabled: it drives the IndexTTS-2 MLX CLI to synthesize `narration.wav` and rebuilds `timeline.json` from the real audio (reusing `derive_timeline`'s `parse_srt` + overlay regexes), converting the spoken text Traditional→Simplified via `to_simplified`/`opencc` (`synthesize_tts.py:94`) since the tokenizer is Simplified-only. Each is a self-contained `main(...) -> int` CLI; the companion `compile_marp.sh` (not indexed) wraps marp-cli to render HTML/PDF/PNG.

<!-- projectmap:auto:start (generated — do not edit by hand) -->
## Files (5)
- `scripts/build_video.py`
- `scripts/derive_timeline.py`
- `scripts/plan_subagent_batches.py`
- `scripts/split_slides.py`
- `scripts/synthesize_tts.py`

## Public symbols (18)
- `function main` — scripts/build_video.py:31
- `function parse_timestamp` — scripts/derive_timeline.py:41
- `function parse_srt` — scripts/derive_timeline.py:45
- `function find_overlay_times` — scripts/derive_timeline.py:86
- `function main` — scripts/derive_timeline.py:112
- `function main` — scripts/plan_subagent_batches.py:25
- `function split_pages` — scripts/split_slides.py:39
- `function extract_overlays` — scripts/split_slides.py:84
- `function to_plain_text` — scripts/split_slides.py:112
- `function main` — scripts/split_slides.py:120
- `function strip_markers` — scripts/synthesize_tts.py:53
- `function parse_args` — scripts/synthesize_tts.py:60
- `function to_simplified` — scripts/synthesize_tts.py:94
- `function resolve_indextts2` — scripts/synthesize_tts.py:129
- `function main` — scripts/synthesize_tts.py:147
- `function seg_path` — scripts/synthesize_tts.py:235
- `function t` — scripts/synthesize_tts.py:299
- `function _find_overlay_cues` — scripts/synthesize_tts.py:351

## Dependencies (imports)
- `__future__`
- `argparse`
- `derive_timeline`
- `json`
- `os`
- `pathlib`
- `re`
- `shutil`
- `subprocess`
- `sys`
- `wave`
<!-- projectmap:auto:end -->
