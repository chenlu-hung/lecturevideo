# Workflow Reference

Detailed procedure for the lecture-video-generator skill. Read this when SKILL.md's high-level steps need expansion.

## Phase 0 — Sanity checks (every invocation)

Before any phase runs:

1. **Validate `subagent_model`**: must be one of `opus`, `sonnet`, `haiku`. If invalid or empty, fall back to `sonnet` and tell the user once ("invalid subagent_model `<x>`, falling back to sonnet").
2. **Validate `pages_per_subagent`**: integer in `[1, 20]`. Out of range → clamp and warn.
3. **Verify tooling**: `npx`, `python3` on `PATH`. Halt with an install instruction if missing.
4. **Resolve `marp_template_path`**: if unset → use bundled `assets/marp/theme.css`. If set but file does not exist → halt and ask the user.

## Phase 1 — Outline

### 1.1 Slugify the topic & collision policy

Derive the directory name:

1. Trim whitespace.
2. Replace runs of whitespace with `-`.
3. Strip filesystem-unsafe characters: `/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`.
4. Preserve CJK characters and digits.
5. Truncate to 80 characters.
6. If the result is empty, fall back to `lecture-<timestamp>`.

Collision handling — when `<output_dir>/<slug>/` already exists:

- If `.state.json` is present and the user has not asked to start fresh → treat as a resume (see SKILL.md "Resume vs. redo"). Do not overwrite anything.
- If no `.state.json` (i.e. a stale or unrelated folder) → ask the user: "use existing folder", "create `<slug>-2`", or "abort". Never silently overwrite.

### 1.2 Initialize state

After the directory is created, write `.state.json`:

```json
{"phase": "outline", "started_at": "<ISO-8601>", "completed_phases": []}
```

### 1.3 Generate outline.md

The outline is a dialogue artefact — it is intended for the user to read and edit. Use this template:

```markdown
# 教學影片大綱：<topic>

- **語言**: <language>
- **聽眾**: <audience>
- **目標長度**: <length_minutes> 分鐘
- **預估投影片數**: <N>

## 章節

### 1. <章節標題>
- **學習目標**：<bullet>
- **重點 overlay**：<id 與用途的草稿>
- **內容大要**：<bullet>

### 2. ...
```

Estimate page count as roughly `length_minutes * 1.5` (i.e. each slide takes ~40 seconds), with a minimum of 3 and a maximum of 30 unless the user requests otherwise.

### 1.4 Review loop

Show the outline summary to the user with these options (use `AskUserQuestion`):

- **Approve as-is** — proceed to Phase 2.
- **I will edit `outline.md` myself** — wait for the user to confirm they're done; then re-read.
- **Apply these changes:** — collect free-form notes; rewrite outline.md; show again.
- **Skip outline (simple topic)** — only offered when estimated pages < 5.

### 1.5 Mark complete

Update `.state.json`:

```json
{"phase": "slides", "completed_phases": ["outline"], ...}
```

## Phase 2 — Slides

### 2.1 Verify template

Resolve `marp_template_path`. If relative, resolve against the working directory of the user's session, not the output directory. Halt with a clear message if the file does not exist.

### 2.2 Compose slides.md

Use this frontmatter exactly:

```yaml
---
marp: true
theme: input
paginate: true
headingDivider: 1
---
```

Note: `theme: input` matches the theme name declared in the bundled default `theme.css` (`/* @theme input */`). If the user supplies a different template whose theme name differs, change this line accordingly.

Each slide is separated by a line containing only `---`. The first slide should be a title slide (just `#` heading + subtitle). Subsequent slides correspond to outline sections.

For long sections, split into multiple slides. Keep bullet density moderate (≤ 5 bullets per slide). Use marp's `<!-- _class: lead -->` for emphasis slides.

### 2.3 Add overlay annotations

Place overlay markers around content that should appear with a delay (synced to narration):

```markdown
- 一般要點 A
<!-- overlay-begin: id=key-insight, label="關鍵推論" -->
- **重要結論**：在這裡解釋為什麼……
<!-- overlay-end: id=key-insight -->
- 一般要點 B
```

Overlay ids must be unique across the deck, kebab-case, and meaningful (the sub-agent will reference them in the SRT).

Detailed annotation grammar: see `marp-and-overlays.md`.

### 2.4 Compile

Run:

```bash
bash scripts/compile_marp.sh \
  <output>/<slug>/slides.md \
  <marp_template_path> \
  <output>/<slug>
```

Expected outputs in `<output>/<slug>/`:
- `slides.html`
- `slides.pdf`
- `slides.images/01.png`, `02.png`, …

### 2.5 Parse for downstream phases

```bash
python3 scripts/split_slides.py \
  <output>/<slug>/slides.md \
  <output>/<slug>/.slides.json
```

The resulting JSON shape:

```json
{
  "pages": [
    {
      "index": 1,
      "raw_md": "# Title\n\n## Subtitle\n",
      "plain_text": "Title Subtitle",
      "image": "slides.images/01.png",
      "overlays": []
    },
    {
      "index": 2,
      "raw_md": "...",
      "plain_text": "...",
      "image": "slides.images/02.png",
      "overlays": [{"id": "key-insight", "label": "關鍵推論"}]
    }
  ]
}
```

### 2.6 Mark complete

Update `.state.json` with `completed_phases: ["outline", "slides"]`.

## Phase 3 — SRT scripts via parallel sub-agents

### 3.1 Plan batches

```bash
python3 scripts/plan_subagent_batches.py \
  <output>/<slug>/.slides.json \
  <pages_per_subagent>
```

This prints JSON like:

```json
{"batches": [{"id": 0, "pages": [1, 2, 3, 4, 5]}, {"id": 1, "pages": [6, 7, 8]}]}
```

### 3.2 Dispatch sub-agents in parallel

Follow the canonical procedure in `references/subagent-prompts.md` §"How to dispatch". Do not duplicate the prompt template here — load that file and use its template directly.

### 3.3 Validate

Run the rules in `references/srt-and-timing.md` §"Validation rules" against each page's `.srt`. On any failure, re-dispatch one sub-agent for the affected pages (use the same template, but include the validation error message in the prompt so the sub-agent can correct it).

### 3.4 Mark complete

Update `.state.json` with `completed_phases: ["outline", "slides", "scripts"]`.

## Phase 4 — Video

### 4.1 Derive timeline

```bash
python3 scripts/derive_timeline.py \
  <output>/<slug>/scripts \
  <output>/<slug>/.slides.json \
  <output>/<slug>/timeline.json
```

The script:
1. Reads each `NN.srt` (timestamps reset per page from `00:00:00,000`).
2. Computes `page_duration = max(end_timestamp)` per page.
3. Concatenates pages: page N starts at `sum(page_duration for k < N)`.
4. Resolves `[overlay:id]` opener cue start time and `[/overlay:id]` closer cue end time → overlay absolute start/end.
5. Writes `timeline.json`.

### 4.2 Build video

```bash
python3 scripts/build_video.py <output>/<slug>
```

The script:
1. Copies `assets/player/` (resolved relative to the skill root) into `<output>/<slug>/video/`.
2. Reads `timeline.json` and inlines it into `index.html` as a `<script>` block.
3. Adjusts slide image paths so the player loads from `../slides.images/`.

### 4.3 Mark complete and notify

Update `.state.json` with `completed_phases: ["outline", "slides", "scripts", "video"]`. Tell the user the absolute path to `<output>/<slug>/video/index.html`.

## Re-doing a single phase

When the user requests a redo:

| User intent | Action |
|-------------|--------|
| Edit the outline | Re-run Phase 1.4 → invalidate `slides`, `scripts`, `video` |
| Tweak a slide | Edit `slides.md` directly → re-run Phase 2.4–2.5 → invalidate `scripts`, `video` |
| Rewrite narration for slide N | Re-dispatch one sub-agent for that page → re-run Phase 4 |
| Adjust overlay timing | Edit the relevant SRT cues → re-run Phase 4 |
| Change marp theme | Update `marp_template_path` → re-run Phase 2.4 → re-run Phase 4 (slide images change) |

In every case, after invalidation rewrite `.state.json` to drop the affected phases from `completed_phases`, then run only the affected steps.
