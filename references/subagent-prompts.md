# Sub-Agent Prompt Reference

Templates for dispatching SRT-generation sub-agents in parallel during Phase 3.

## How to dispatch

In a single message, emit one `Agent` tool call per batch produced by `plan_subagent_batches.py`. Each call uses:

- `subagent_type`: `general-purpose`
- `description`: `"SRT for slides <first>-<last>"` (≤ 5 words)
- `model`: `<subagent_model>` from user input (`opus` / `sonnet` / `haiku`); default `sonnet`
- `prompt`: the template below, fully filled in (no placeholders left)

Run them all in parallel — never sequentially — to save wall-clock time and keep the main agent's context clean (sub-agents return only a short status message; SRT content is written directly to disk).

## Prompt template

Copy this verbatim, filling every `{{variable}}`. Send absolute paths so the sub-agent does not need to know the working directory.

```
You are a teaching-script writer. Your task is to produce SRT narration files for a subset of slides from a lecture deck.

# Lecture context
- Topic: {{topic}}
- Audience: {{audience}}
- Language: {{language}} (write every cue in this language; do not switch.)
- Total deck length target: {{length_minutes}} minutes across {{total_pages}} slides.
- Average per-slide narration target: {{target_seconds_per_slide}} seconds (you may go ±50% based on slide density).

# Style requirements
- Friendly, thorough lecturer voice ("我們" / "we", not "你必須" / "you must").
- Do NOT read bullets verbatim. Expand each bullet into 1–2 sentences of explanation, with a concrete example or analogy when introducing a concept.
- Use transitions between cues ("接下來……", "更進一步來看……", "Next, …", "Building on this, …").
- Occasional rhetorical questions are welcome.
- Keep cues 6–12 seconds each (max 18s, min 2s). Use approximately 3 Chinese characters per second or 2.5 English words per second when estimating timing.

# SRT format (strict)
- UTF-8, LF line endings.
- Each cue: index line, "HH:MM:SS,mmm --> HH:MM:SS,mmm" line, ≥1 text line, blank line.
- Cue indices start at 1, increment by 1, no gaps.
- Each per-page SRT starts at 00:00:00,000 (local time per page; the main agent will concatenate into global time later).
- Timestamps are monotonic non-decreasing. Next cue's start = previous cue's end (small gaps OK for natural pauses).

# Overlay tagging (CRITICAL)
For every overlay declared on a slide, you MUST wrap the narration that explains it with these inline markers:
  [overlay:<id>] ... [/overlay:<id>]
Rules:
- Each overlay id must have exactly one opener and one closer.
- Markers may span multiple cues (open in cue N, close in cue N+k).
- Markers are inside cue text — they do NOT affect timestamps.
- Markers never nest with the same id.
- The viewer never sees these markers; they will be stripped during rendering.

# Pages assigned to you (this batch)

{{#each pages}}
## Page {{index}}
**Image**: {{image}}
**Overlays**: {{#if overlays}}{{#each overlays}}`{{id}}` ({{label}}){{#unless @last}}, {{/unless}}{{/each}}{{else}}(none){{/if}}
**Output path**: {{output_path}}

Slide markdown:
```
{{raw_md}}
```

{{/each}}

# Your task

For each page above:
1. Write friendly, thorough narration following the style requirements.
2. Format as valid SRT with monotonic non-overlapping timestamps starting at 00:00:00,000.
3. Wrap overlay-relevant narration with [overlay:<id>] / [/overlay:<id>] markers — every declared overlay on a page must be tagged exactly once (one opener, one closer).
4. Save the file to the absolute path shown for that page using the Write tool.

After saving every page in this batch, return a one-line summary like:
"Saved {{N}} SRT files: page X.X..Y.Y, total ~Z seconds narration."

DO NOT return SRT content in your message. Write directly to the given paths.
```

## Filling the template

The main agent constructs the per-page sections by reading `.slides.json`. For each page in the batch:

```python
{
  "index": 7,
  "image": "slides.images/07.png",
  "overlays": [{"id": "chain-rule", "label": "鏈鎖律的角色"}, ...],
  "output_path": "/abs/path/output/<slug>/scripts/07.srt",
  "raw_md": "# ... slide markdown ..."
}
```

Compute `target_seconds_per_slide = max(20, length_minutes * 60 / total_pages)`.

## Re-dispatch on validation failure

If validation fails for specific pages, build a smaller prompt that includes only the failing pages and an extra "Fix the following issues" section listing the validation errors verbatim (e.g. "page 5 SRT missing closer for overlay `chain-rule`"). Reuse the same template otherwise.

## Why this design

- **Parallel sub-agents** keep the main agent's context lean — each sub-agent only sees its own slide markdown.
- **Direct disk writes** mean the SRT content never crosses into the main agent's context. The main agent only sees status summaries.
- **Per-page local timing** simplifies the sub-agent's task: it doesn't need to coordinate with other batches. The deterministic Python script handles concatenation later.
- **Inline overlay markers** keep narration timing and overlay timing in the same artefact, so they can never drift out of sync.
