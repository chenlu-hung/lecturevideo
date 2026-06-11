---
name: lecture-video-generator
description: This skill should be used when the user asks to "generate a lecture video", "make a teaching video on X", "produce slides + narration video", "create lecture from topic", "auto-generate teaching slides", "auto-generate marp slides with narration", "make a voiced/narrated lecture video", "把主題做成教學影片", "生成教學影片", "從主題自動生成投影片與講稿", "做一個關於 X 的上課影片", "幫我做投影片+講稿", "把 X 變成上課影片", "自動生成 marp 投影片", "帶旁白腳本的投影片", "要有旁白/配音的教學影片", "有聲教學影片", or provides a topic and wants an end-to-end pipeline that produces marp slides (HTML+PDF), per-page SRT narration via parallel sub-agents, and a reveal.js-style auto-play HTML video with overlay timing — optionally with real spoken narration synthesized by a local IndexTTS-2 (TTS) voice.
version: 0.2.0
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
├── slides.images/01.png …    # Phase 2 per-page PNG (used by player)
├── .slides.json              # Internal: parsed pages + overlays
├── scripts/01.srt …          # Phase 3 per-page SRT
├── timeline.json             # Phase 4 derived global timeline
├── narration.wav             # Phase 4 (TTS only) spoken narration track
├── .tts_segments/            # Phase 4 (TTS only) per-cue wavs + combined.srt
├── video/                    # Phase 4 player (open index.html)
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
2. Generate `slides.md` using marp syntax following `references/marp-and-overlays.md`. The marp frontmatter, slide separator rules, and overlay annotation grammar are defined there — do not reinvent them.
3. Run `bash scripts/compile_marp.sh <output_dir>/<slug>/slides.md <marp_template_path> <output_dir>/<slug>` to produce `slides.html`, `slides.pdf`, and `slides.images/NN.png`.
4. Run `python3 scripts/split_slides.py <output_dir>/<slug>/slides.md <output_dir>/<slug>/.slides.json` to extract per-page text and overlay metadata.

### Phase 3 — SRT scripts via parallel sub-agents

1. Run `python3 scripts/plan_subagent_batches.py <output_dir>/<slug>/.slides.json <pages_per_subagent>` to print batches as JSON.
2. **Launch all batches in parallel** following the canonical dispatch procedure in `references/subagent-prompts.md` (single message, one `Agent` call per batch, `subagent_type=general-purpose`, `model=<subagent_model>`).
3. After all sub-agents finish, validate every page's SRT against the rules in `references/srt-and-timing.md` §"Validation rules". On failure, re-dispatch only the affected pages with the validation error included in the prompt.

The SRT format and overlay-tagging contract live in `references/srt-and-timing.md`; the verbatim sub-agent prompt template lives in `references/subagent-prompts.md`. Load both before this phase runs.

### Phase 4 — Video

Build the global timeline. **Choose one of two paths depending on `tts`:**

**4a. Silent (default, `tts` = false).** Run `python3 scripts/derive_timeline.py <output_dir>/<slug>/scripts <output_dir>/<slug>/.slides.json <output_dir>/<slug>/timeline.json` to produce a global timeline. The script joins per-page SRT (each starting at `00:00:00,000`) into a single timeline and resolves `[overlay:id]` markers to absolute start/end times.

**4b. Voiced (`tts` = true).** Run `python3 scripts/synthesize_tts.py <output_dir>/<slug> --ref <voice_ref> [--emo-ref <emotion_ref>] [--indextts2-dir <dir>]`. This **replaces** `derive_timeline.py`: it synthesizes each cue with IndexTTS-2 (one batched `--srt` call), writes `<slug>/narration.wav`, and rebuilds `timeline.json` so slide/overlay times match the *real* spoken audio (not the sub-agents' estimated SRT timestamps). It strips `[overlay:*]` markers before speaking and reuses the same overlay contract. For Traditional Chinese it auto-converts the spoken text to Simplified via `opencc` (`--zh-convert`, default `auto`), because IndexTTS-2's tokenizer is Simplified-only — slides and SRT keep their Traditional text. Synthesis is compute-heavy (minutes); pass `--seed N` for reproducible audio. Do **not** also run `derive_timeline.py` — it would overwrite the audio-accurate timeline.

Then, for both paths:

1. Run `python3 scripts/build_video.py <output_dir>/<slug>` to copy `assets/player/{index.html,player.css,player.js}` into `<output_dir>/<slug>/video/` (verbatim — never regenerate them) and inject `timeline.json` plus overlay metadata into the template. If `narration.wav` is present (path 4b), it is also copied into `video/`.
2. The player auto-advances slides per timeline, fades overlays in/out, and provides play/pause/seek/speed. When `narration.wav` is present the player uses it as the master clock (audio mode); otherwise it runs on its internal timer.
3. Tell the user to open `<output_dir>/<slug>/video/index.html` in a browser.

Player internals, timing model, and the TTS audio path: see `references/player-architecture.md`.

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

- `scripts/compile_marp.sh` — invokes `npx @marp-team/marp-cli` to emit HTML, PDF, and per-page PNG.
- `scripts/split_slides.py` — parses `slides.md` into a structured JSON of pages and overlays.
- `scripts/plan_subagent_batches.py` — splits page list into batches sized by `pages_per_subagent`.
- `scripts/derive_timeline.py` — concatenates per-page SRT into a global timeline and resolves overlay times (silent path).
- `scripts/synthesize_tts.py` — TTS path: synthesizes narration via IndexTTS-2, writes `narration.wav`, and rebuilds `timeline.json` from the real audio. Replaces `derive_timeline.py` when `tts` is enabled.
- `scripts/build_video.py` — wires `assets/player/` into `<output>/video/` with injected timeline (and `narration.wav` if present).

### Assets (copied into output)

- `assets/marp/theme.css` — bundled default marp theme (theme name `input`, 4:3 1024×768pt, CJK-friendly font stack). Used when `marp_template_path` is not specified.
- `assets/player/index.html` — reveal.js-style auto-play HTML (slides as `<img>`, with `<audio>` slot, overlay `<div>`s).
- `assets/player/player.css` — layout and overlay fade animation.
- `assets/player/player.js` — timeline driver: advances slides, shows/hides overlays, syncs to audio when present.

## Required Tooling

Verify availability before starting; halt with a clear instruction to install if missing.

- `node` and `npx` (for `@marp-team/marp-cli`; first run will auto-install).
- `python3` (for the bundled scripts; standard library only — no `pip install` required).
- A modern browser to view the player. PDF export uses marp-cli's built-in chromium download.
- **Only when `tts` is enabled:** a built IndexTTS-2 MLX-Swift CLI (Apple Silicon + the converted models) at `indextts2_dir`, plus a `voice_ref` `.wav`. If the binary is missing, `synthesize_tts.py` halts and tells the user to build it (`./build.sh Debug` in that checkout). The skill works fully without this — only the voiced path needs it.
- **For Traditional Chinese narration with TTS:** `opencc` on PATH (`brew install opencc`) so the spoken text can be converted to Simplified (IndexTTS-2 is Simplified-only). Without it, `synthesize_tts.py` warns and proceeds, but Traditional characters will be mispronounced.
