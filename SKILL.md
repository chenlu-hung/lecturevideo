---
name: lecture-video-generator
description: This skill should be used when the user asks to "generate a lecture video", "make a teaching video on X", "produce slides + narration video", "create lecture from topic", "auto-generate teaching slides", "auto-generate marp slides with narration", "make a voiced/narrated lecture video", "把主題做成教學影片", "生成教學影片", "從主題自動生成投影片與講稿", "做一個關於 X 的上課影片", "幫我做投影片+講稿", "把 X 變成上課影片", "自動生成 marp 投影片", "帶旁白腳本的投影片", "要有旁白/配音的教學影片", "有聲教學影片", "數學式手寫動畫", "板書動畫", "像老師寫黑板一樣寫公式", "handwritten math animation", "export the lecture video to MP4", "把教學影片轉成 mp4", "把 html 影片轉成 mp4", "把投影片影片轉成 mp4", "匯出 mp4", "輸出成 mp4 檔", "convert the lecture/HTML video to mp4", "render the lecture as an mp4 file", or provides a topic and wants an end-to-end pipeline that produces marp slides (HTML+PDF), per-page SRT narration via parallel sub-agents, and a reveal.js-style auto-play HTML video with overlay timing — optionally with real spoken narration synthesized by a local IndexTTS-2 (TTS) voice, and optionally exported to a standalone MP4 file.
version: 0.5.0
---

# Lecture Video Generator

End-to-end pipeline that turns a single topic into a full teaching video: outline → marp slides (HTML+PDF) → per-slide SRT narration via parallel sub-agents → reveal.js-style auto-play HTML video with overlay timing.

The skill is fully self-contained. All bundled scripts under `scripts/`, references under `references/`, and the player template under `assets/player/` are designed to work together without any external skill or plugin.

## Inputs

Collect these from the user before starting. Only `topic` and `language` are required.

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `topic` | yes | — | The subject of the lecture. Used as the output subdirectory name (slugified). |
| `language` | yes | — | Output language for outline, slides, and narration (e.g. `繁體中文`, `English`). |
| `audience` | no | "大學部學生" / "undergraduate students" | Adjusts depth and tone. |
| `length_minutes` | no | `15` | Target total narration length; drives slide count and SRT pacing. |
| `marp_template_path` | no | bundled `assets/marp/theme.css` (theme name `input`) | Path to a marp theme CSS file. The bundled default is self-contained; override with any other CSS that declares `/* @theme <name> */`. |
| `subagent_model` | no | `sonnet` | Model passed to each script-generation sub-agent. Must be one of `opus`, `sonnet`, `haiku`; if invalid, fall back to `sonnet` and warn. |
| `pages_per_subagent` | no | `5` | Number of slides each sub-agent narrates. |
| `output_dir` | no | `./output` (relative to current working directory) | Parent of the per-topic folder. |
| `tts` | no | `false` | When true, Phase 4 synthesizes spoken narration (a real voiced video) instead of a silent timer-driven one. |
| `voice_ref` | required if `tts` | — | Path to a reference speaker `.wav` (the voice to clone). IndexTTS-2 is zero-shot; a few seconds of clean speech is enough. |
| `emotion_ref` | no | (uses `voice_ref`) | Optional separate `.wav` whose emotion drives the delivery. |
| `indextts2_dir` | no | `$INDEXTTS2_DIR` | Path to the IndexTTS-2 MLX checkout (provides the CLI binary + models). Required when `tts` is true unless `$INDEXTTS2_DIR` is set. |

To gather missing values, use `AskUserQuestion` with the exact field names. For `topic` and `language`, accept free-form text; for `subagent_model`, offer `opus`/`sonnet`/`haiku` chips. If the user asks for a voiced video (`tts`), confirm `voice_ref` — there is no default voice.

## Output Layout

All artefacts live under one folder per topic:

```
<output_dir>/<slug-of-topic>/
├── outline.md                # Phase 1 result (human-editable)
├── slides.md                 # Phase 2 marp source
├── slides.html               # Phase 2 marp HTML
├── slides.pdf                # Phase 2 marp PDF
├── slides.images/01.png …    # Phase 2 per-page PNG, player base (mathwrite+overlay blanked)
├── slides.images/01.reveal.png … # Phase 2 overlay-visible crop source (decks with overlays)
├── .slides.json              # Internal: parsed pages + overlays + mathwrites
├── .mathwrite.json           # Internal: per-segment math SVGs + mathwrite/overlay bboxes
├── scripts/01.srt …          # Phase 3 per-page SRT
├── timeline.json             # Phase 4 derived global timeline
├── narration.mp3             # Phase 4 (TTS only) spoken narration track (.wav with --audio-format wav/both)
├── .tts_segments/            # Phase 4 (TTS only) per-cue wavs + combined.srt
├── video/                    # Phase 4 player (open index.html)
│   └── lecture.mp4           # Phase 5 (optional) exported MP4
└── .state.json               # Resumable state marker
```

## Pipeline

Run phases in order. After each phase, write the phase name to `.state.json` so the user may resume from any point.

### Phase 1 — Outline

1. Slugify `topic` for filesystem (lowercase, replace spaces with `-`, strip punctuation that is unsafe for filenames; preserve CJK characters).
2. Create `<output_dir>/<slug>/`.
3. Generate `outline.md` with sections: title, audience, language, target length, estimated page count, chapter outline, learning objectives per chapter, suggested overlay highlights per chapter.
4. Show the outline to the user. Ask whether to proceed, request edits inline, or let the user edit `outline.md` directly. Re-read after the user confirms edits are done.
5. If `topic` is trivial (estimated < 5 pages) and the user agrees, skip outline review and go straight to Phase 2.

Detailed step-by-step procedure, decision tree, and re-do mechanics: see `references/workflow.md`.

### Phase 2 — Slides

1. Read `outline.md`. Resolve `marp_template_path` (default = bundled `assets/marp/theme.css`). Halt if the file does not exist.
2. Generate `slides.md` using marp syntax following `references/marp-and-overlays.md`. The marp frontmatter, slide separator rules, overlay annotation grammar, **and the layout/density budget (§"Layout & density": ≤5 bullets or ~10 lines per page, display math at the top level only, no per-page font hacks)** are defined there — do not reinvent them.
3. Run `bash scripts/compile_marp.sh <output_dir>/<slug>/slides.md <marp_template_path> <output_dir>/<slug>` to produce `slides.html`, `slides.pdf`, and the player PNGs. The base `slides.images/NN.png` has mathwrite formulas **and** overlay content blanked (so the player animates them in at narration time, not from slide-load); when the deck has overlays it also emits `slides.images/NN.reveal.png` (overlays visible) that the player crops the revealed content from. The HTML/PDF handout keeps everything visible.
4. Run `python3 scripts/check_fit.py <output_dir>/<slug>` to catch any page whose content overflows the slide box (exit `3` + one `page NN OVERFLOW by …px` line per offender; exit `0` when clean). For each offender, **thin the page** (split it, cut words, or move a display formula to its own page — never shrink the font) and re-run `compile_marp.sh` + `check_fit.py` until it exits `0`.
5. Run `python3 scripts/split_slides.py <output_dir>/<slug>/slides.md <output_dir>/<slug>/.slides.json` to extract per-page text, overlay, and mathwrite metadata.
6. **If the deck declares mathwrite blocks or overlays**: run `python3 scripts/render_mathwrite.py <output_dir>/<slug>` to render each mathwrite segment to SVG and measure the on-slide bbox of every mathwrite block **and** every overlay region; writes `.mathwrite.json`. Needs the same headless Chrome marp uses; the MathJax CDN is only required when there are mathwrite blocks (overlay-only decks skip it). These bboxes let the player hand-write formulas and **reveal overlay content in place** at narration time — so skipping this step on a deck with overlays would leave their content blanked and unrevealed. Use mathwrite for any display formula the narration walks through term by term — the player hand-writes it like a teacher at a whiteboard, synced to the narration (grammar in `references/marp-and-overlays.md` §"Mathwrite").

### Phase 3 — SRT scripts via parallel sub-agents

1. Run `python3 scripts/plan_subagent_batches.py <output_dir>/<slug>/.slides.json <pages_per_subagent>` to print batches as JSON.
2. **Launch all batches in parallel** following the canonical dispatch procedure in `references/subagent-prompts.md` (single message, one `Agent` call per batch, `subagent_type=general-purpose`, `model=<subagent_model>`).
3. After all sub-agents finish, validate every page's SRT against the rules in `references/srt-and-timing.md` §"Validation rules". On failure, re-dispatch only the affected pages with the validation error included in the prompt.

The SRT format and overlay-tagging contract live in `references/srt-and-timing.md`; the verbatim sub-agent prompt template lives in `references/subagent-prompts.md`. Load both before this phase runs.

### Phase 4 — Video

Build the global timeline. **Choose one of two paths depending on `tts`:**

**4a. Silent (default, `tts` = false).** Run `python3 scripts/derive_timeline.py <output_dir>/<slug>/scripts <output_dir>/<slug>/.slides.json <output_dir>/<slug>/timeline.json` to produce a global timeline. The script joins per-page SRT (each starting at `00:00:00,000`) into a single timeline and resolves `[overlay:id]` markers to absolute start/end times.

**4b. Voiced (`tts` = true).** Run `python3 scripts/synthesize_tts.py <output_dir>/<slug> --ref <voice_ref> [--emo-ref <emotion_ref>] [--indextts2-dir <dir>]`. This **replaces** `derive_timeline.py`: it synthesizes each cue with IndexTTS-2 (one batched `--srt` call), writes `<slug>/narration.mp3` (`--audio-format wav`/`both` to keep the lossless wav), and rebuilds `timeline.json` so slide/overlay times match the *real* spoken audio (not the sub-agents' estimated SRT timestamps). It strips `[overlay:*]` markers before speaking and reuses the same overlay contract. For Traditional Chinese it auto-converts the spoken text to Simplified via `opencc` (`--zh-convert`, default `auto`), because IndexTTS-2's tokenizer is Simplified-only — slides and SRT keep their Traditional text. Synthesis is compute-heavy (minutes); pass `--seed N` for reproducible audio. Do **not** also run `derive_timeline.py` — it would overwrite the audio-accurate timeline.

Add `--remote-host <ssh-host>` (or set `$LECTUREVIDEO_TTS_HOST`) to run the synthesis on a CUDA machine instead of the local MLX binary — same flags, same output, ~20× faster per cue on an RTX 4080 (RTF 0.42 vs the local MLX binary's 8.4). Only the per-cue TTS moves; the timeline is still built locally from the wavs that come back, and an interrupted run resumes with `--remote-resume`. See `references/remote-tts.md` for the transport, the execution-provider policy and how to set a remote box up.

Then, for both paths:

1. Run `python3 scripts/build_video.py <output_dir>/<slug>` to copy `assets/player/{index.html,player.css,player.js}` into `<output_dir>/<slug>/video/` (verbatim — never regenerate them) and inject `timeline.json` plus overlay metadata into the template. If a narration track is present (path 4b), it is also copied into `video/` — mp3 wins when both exist.
2. The player auto-advances slides per timeline and provides play/pause/seek/speed. Overlay content is **revealed in place** at its narration window (cropped from `NN.reveal.png` over the blanked base) and stays until the slide changes; a small top-right badge also fades in/out as a highlight. When a narration track is present the player uses it as the master clock (audio mode); otherwise it runs on its internal timer.
3. Tell the user to open `<output_dir>/<slug>/video/index.html` in a browser.

Player internals, timing model, and the TTS audio path: see `references/player-architecture.md`.

### Phase 5 — MP4 export (optional)

Run only when the user wants a standalone video file (e.g. "export to MP4", "把 html 影片轉成 mp4", "匯出 mp4") rather than the browser player. **Requires Phase 4 to have completed** — it reads the built `video/index.html` (and `video/narration.mp3`/`.wav` if the voiced path produced one).

1. Verify `<output_dir>/<slug>/video/index.html` exists. If not, run Phase 4 (`build_video.py`) first.
2. Run `node scripts/export_mp4.mjs <output_dir>/<slug>`. It steps the player frame-by-frame in headless Chrome (driving Chrome over the DevTools Protocol via Node's built-in `WebSocket` — **no npm install**) and pipes screenshots into `ffmpeg`, producing `<output_dir>/<slug>/video/lecture.mp4` (H.264 + AAC; video-only when there is no narration track). Capturing dominates the wall clock and one Chrome only saturates one core, so the frame range is split across several Chrome instances, each encoding its own chunk into `video/.export-chunks/`; the chunks are then concatenated by stream copy and the narration muxed in. The split is invisible in the output — the captured frames are byte-identical to a single-worker run. Because the player renders every frame as a pure function of time, the export is deterministic — exact overlay/caption/mathwrite states, no real-time playback. To stay fast it reads `timeline.json` and only screenshots frames that actually change: hand-written math (mathwrite) is the sole true `f(t)` animation and is captured per-frame, while static stretches (slide/overlay/caption changes are discrete under frame-stepped seek) reuse one cached screenshot — typically ~6× fewer screenshots than naive per-frame capture (it prints `screenshots N / M frames`). If `timeline.json` is missing it falls back to capturing every frame.
3. Defaults: 30 fps, size auto-derived from the slide aspect ratio (height capped at 1080), CRF 18, JPEG capture at quality 92, and `min(6, cores − 2)` parallel Chrome workers. Tune with `--fps`/`--width`/`--height`/`--crf`/`--preset`/`--workers`/`--jpeg-quality`; `--png` restores lossless capture (slower, and the frames are re-encoded by libx264 regardless); override binaries with `--chrome`/`--ffmpeg`; `--out` to change the path. Pass `--fps 60` for smoother handwriting at roughly double the time. Export is compute-heavy (minutes for a multi-minute lecture).
4. Tell the user the resulting `video/lecture.mp4` path.

This phase needs **Node ≥ 22**, a local **Chrome/Chromium** (auto-detected; `--chrome` or `$CHROME_PATH` to override), and **ffmpeg** on PATH. It is purely additive — it never modifies the player or timeline, so it can be run any time after Phase 4 and re-run with different options.

## Resume vs. redo

On every invocation, read `.state.json` first. Then apply this precedence:

1. **Explicit redo request** (user says "rewrite slide 5", "regenerate slides", "redo outline"): consult the redo table in `references/workflow.md` §"Re-doing a single phase", invalidate the affected phases from `completed_phases`, and re-run only what is needed.
2. **No explicit request, state present**: resume from the first phase not in `completed_phases`.
3. **No state**: start from Phase 1.

The redo table specifies exactly which downstream phases each kind of edit invalidates.

## Bundled Resources

### References (load on demand)

- `references/workflow.md` — full step-by-step workflow, decision tree, and re-do recipes.
- `references/marp-and-overlays.md` — marp frontmatter reference and overlay annotation grammar.
- `references/srt-and-timing.md` — SRT format spec and `[overlay:*]` tagging rules.
- `references/subagent-prompts.md` — verbatim prompt template to send each sub-agent.
- `references/player-architecture.md` — player.js timeline model, overlay rendering, audio slot.

### Scripts (run via Bash)

- `scripts/compile_marp.sh` — invokes `npx @marp-team/marp-cli` to emit HTML, PDF, the base player PNGs (mathwrite + overlay regions blanked), and — for decks with overlays — the `NN.reveal.png` crop sources plus a `.render.html` probe for bbox measurement.
- `scripts/check_fit.py` — loads `slides.html` in headless Chrome and reports any page whose content overflows the slide box (exit `3` on overflow). Run after `compile_marp.sh`; no network needed.
- `scripts/split_slides.py` — parses `slides.md` into a structured JSON of pages, overlays, and mathwrites.
- `scripts/render_mathwrite.py` — renders mathwrite segment TeX to SVG (MathJax) and measures every mathwrite block's and overlay's on-slide bbox via headless Chrome; writes `.mathwrite.json`. Run whenever the deck declares mathwrite blocks or overlays (MathJax/network only needed for mathwrite).
- `scripts/plan_subagent_batches.py` — splits page list into batches sized by `pages_per_subagent`.
- `scripts/derive_timeline.py` — concatenates per-page SRT into a global timeline and resolves overlay times (silent path).
- `scripts/synthesize_tts.py` — TTS path: synthesizes narration via IndexTTS-2, writes `narration.mp3` (see `--audio-format`), and rebuilds `timeline.json` from the real audio. Replaces `derive_timeline.py` when `tts` is enabled.
- `scripts/remote/indextts2_onnx_batch.py` — the remote worker behind `--remote-host`: a drop-in stand-in for the MLX binary driven by ONNX Runtime (torch-free) on an NVIDIA GPU. Pushed to the remote automatically on every run; `scripts/remote/indextts2-batch.sh` is the launcher installed there as `~/bin/indextts2-batch`.
- `scripts/remote/{setup_gpt2_fp16.sh,export_gpt2_fp16.py,use_gpt2_variant.sh}` — one-off, run on the remote: re-export IndexTTS-2's GPT-2 stack to fp16 ONNX so the CUDA EP can run it (the published graphs are int8, which it cannot). This is the only step that needs PyTorch.
- `scripts/build_video.py` — wires `assets/player/` into `<output>/video/` with injected timeline (and the narration track if present).
- `scripts/export_mp4.mjs` — (optional Phase 5) renders the built player frame-by-frame in headless Chrome and muxes with ffmpeg into `video/lecture.mp4`. Node ≥ 22, no npm deps; needs Chrome + ffmpeg.

### Assets (copied into output)

- `assets/marp/theme.css` — bundled default marp theme (theme name `input`, 4:3 1024×768pt, CJK-friendly font stack). Used when `marp_template_path` is not specified.
- `assets/player/index.html` — reveal.js-style auto-play HTML (slides as `<img>`, with `<audio>` slot, overlay/reveal/mathwrite layers).
- `assets/player/player.css` — layout, overlay-reveal + badge fade animation, mathwrite layer.
- `assets/player/player.js` — timeline driver: advances slides, hand-writes mathwrite formulas (each glyph replaced by its Hershey single-stroke centerline and drawn along a real pen trajectory; data in `assets/player/hershey-font.js`), reveals overlay content in place, syncs to audio when present.
- `assets/player/hershey-font.js` — generated single-stroke (Hershey) glyph centerlines used by the mathwrite hand-writing: upright Latin, Greek (both cases), and a cursive hand for math-italic variables (built by `scripts/gen_hershey_font.py` from `assets/hershey/`).

## Required Tooling

Verify availability before starting; halt with a clear instruction to install if missing.

- `node` and `npx` (for `@marp-team/marp-cli`; first run will auto-install).
- `python3` (for the bundled scripts; standard library only — no `pip install` required).
- A modern browser to view the player. PDF export uses marp-cli's built-in chromium download.
- **Only for Phase 5 (MP4 export):** Node ≥ 22, a local Chrome/Chromium (auto-detected; `--chrome` or `$CHROME_PATH` to override), and `ffmpeg` on PATH. `export_mp4.mjs` has no npm dependencies. The rest of the pipeline works without these — only MP4 export needs them.
- **Phase 2 fit check (`check_fit.py`) and mathwrite rendering (`render_mathwrite.py`):** a local Chrome/Chromium/Edge (auto-detected; override with `CHROME_PATH` or `--chrome`). `check_fit.py` needs no network; `render_mathwrite.py` additionally needs the MathJax CDN at build time when the deck has mathwrite blocks (`--mathjax-url` can point at a local copy).
- **Only when `tts` is enabled:** a built IndexTTS-2 MLX-Swift CLI (Apple Silicon + the converted models) at `indextts2_dir`, plus a `voice_ref` `.wav`. If the binary is missing, `synthesize_tts.py` halts and tells the user to build it (`./build.sh Debug` in that checkout). The skill works fully without this — only the voiced path needs it. Alternatively, `--remote-host` needs nothing locally but `ssh` and `rsync`, with the engine installed on the remote (`references/remote-tts.md`).
- **For Traditional Chinese narration with TTS:** `opencc` on PATH (`brew install opencc`) so the spoken text can be converted to Simplified (IndexTTS-2 is Simplified-only). Without it, `synthesize_tts.py` warns and proceeds, but Traditional characters will be mispronounced.
