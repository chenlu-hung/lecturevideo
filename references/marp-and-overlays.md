# Marp & Overlay Reference

Conventions for writing `slides.md` so it compiles correctly with marp and is consumable by the overlay-aware video player.

## Marp basics used by this skill

Frontmatter (always exactly this, unless the user supplies a non-default theme):

```yaml
---
marp: true
theme: input
paginate: true
headingDivider: 1
---
```

- `theme: input` — matches the theme name declared inside the bundled default `theme.css` (`/* @theme input */`). When a custom template is supplied, look at the first `/* @theme … */` line in that CSS and copy its value here.
- `headingDivider: 1` — any top-level `#` heading auto-creates a new slide. **Use one convention only**: prefer explicit `---` separators throughout the deck (the slug remains stable and overlay annotations cannot drift across pages). When `headingDivider` is on, do NOT also place `---` directly above an `#` heading or the deck will gain a stray empty slide.
- `paginate: true` — page numbers are essential for the audience to follow along.

Per-slide directives (HTML comments):

```markdown
<!-- _class: lead -->        # emphasis / title-card slide
<!-- _backgroundColor: #f5f5f5 -->
<!-- _color: #333 -->
```

These apply only to the slide that immediately follows. Without the leading underscore (`_class:`), the directive applies to the rest of the deck.

Slide separator: a line containing only `---` (no leading/trailing whitespace).

## Overlay annotations

Overlays mark content that appears with a delay during playback, synced to narration. The slide compiles normally — marp does not interpret the annotations. The video player reads them via `.slides.json` and shows them at the right time.

### Grammar

```markdown
<!-- overlay-begin: id=<kebab-id>, label="<short human label>" -->
<content that should fade in at overlay time>
<!-- overlay-end: id=<kebab-id> -->
```

Rules:

- `id` is required, kebab-case (`[a-z0-9-]+`), and **unique across the entire deck** (not just per page). The downstream timeline keys overlays by `(slide, id)`, but unique-across-deck ids prevent confusion in `[overlay:id]` SRT markers.
- `label` is required, a short human description used by the sub-agent when writing narration.
- An overlay region is bound to **exactly one slide** — the slide containing the `overlay-begin` line. Overlay regions may not span across `---` slide separators.
- The opening and closing comments must be on their own lines.
- Overlays may not nest. If you need layered reveals, use multiple sibling overlays.
- Content between the markers may be any markdown that marp accepts (bullets, tables, images, code).
- An overlay annotation does **not** change the visual layout. The marker stays in the markdown as a comment; the player draws an opacity transition on the corresponding region.

### Example — bullet reveal

```markdown
# 反向傳播的核心想法

- 從輸出層往回傳播誤差訊號
<!-- overlay-begin: id=chain-rule, label="鏈鎖律的角色" -->
- 每一層用**鏈鎖律**把誤差分解到參數
<!-- overlay-end: id=chain-rule -->
- 用梯度更新權重
```

### Example — emphasis box

```markdown
# 學習率的影響

學習率太大 → 震盪；太小 → 收斂太慢。

<!-- overlay-begin: id=lr-tip, label="實務建議" -->
> 實務上常從 `1e-3` 開始，再依 loss 曲線調整。
<!-- overlay-end: id=lr-tip -->
```

### Picking overlay regions

When generating slides from an outline, add overlays where the narration will spend extra time explaining a sub-point. Good candidates:

- Definitions or formulas that benefit from "now look here" emphasis.
- Quotes / takeaways that should appear after preamble.
- Diagrams' annotation labels (use HTML / SVG inside the overlay block).

Avoid overlays for:

- Headings (the slide title should be visible from start).
- The first bullet on a slide (no narration time before it).

## Mathwrite annotations (hand-written math)

A mathwrite block marks a display formula that the video player **hand-writes like a
teacher at a whiteboard**, stroke-drawing each segment while the narration explains it.
The HTML/PDF deck shows the formula normally; only the PNG render (what the player
shows) blanks it out so the player can draw into the empty space.

### Grammar

```markdown
<!-- mathwrite-begin: id=bayes -->
<div class="mathwrite">

$$P(\theta \mid x) \; = \; \frac{P(x \mid \theta)\,P(\theta)}{P(x)}$$

</div>
<!-- mathwrite-seg: seg=lhs, label="後驗機率", tex="P(\theta \mid x)" -->
<!-- mathwrite-seg: seg=eq, label="等號", tex="=" -->
<!-- mathwrite-seg: seg=rhs, label="概似乘先驗除以證據", tex="\frac{P(x \mid \theta)\,P(\theta)}{P(x)}" -->
<!-- mathwrite-end: id=bayes -->
```

Rules:

- `id` is kebab-case and **unique across the deck**. A block may not nest or span slides.
- The `<div class="mathwrite">` wrapper is **required** (exactly one per block), with blank
  lines around the `$$…$$` so marp still parses the math. It is how the build step locates
  and blanks the formula; `split_slides.py` rejects a block without it.
- Each `mathwrite-seg` declares one narration-synced drawing step, **in writing order**:
  - `seg` — kebab-case, unique within the block. The SRT marker is `[overlay:<id>.<seg>]`.
  - `label` — short hint for the narration sub-agent (what this segment means).
  - `tex` — the segment's TeX. Double quotes are not allowed inside; the segments are
    rendered independently and laid out left-to-right on one baseline, so together they
    should read as the full formula. **Split only at horizontal seams** (left side / relation
    sign / right side / appended terms) — never inside a fraction, radical, or matrix.
- The `$$…$$` inside the div is the authoritative full formula for the handout; keep the
  seg `tex` values consistent with it.
- One to two mathwrite blocks per slide at most; each block needs enough narration time
  (≥ 3–4 seconds per segment) to look like writing rather than flashing.

After compiling, `scripts/render_mathwrite.py <topic_dir>` must run once to render the
segment SVGs and measure the blanked region (see SKILL.md Phase 2).

## Default theme reference

The bundled default at `../marp_keynote_template/theme.css` (resolved from the user's working directory) defines:

- 1024pt × 768pt slide size (4:3).
- 36pt body text, 80pt h1 (centered), 52pt h2.
- Font stack favouring `PingFang TC` for CJK rendering.
- Accent palette: `--kn-accent1: #0365C0`, `--kn-accent2: #00882B`, etc.

When the user supplies a different template, do not rewrite the CSS — only adjust `theme:` in the frontmatter.

## Common pitfalls

- **Forgetting `marp: true`** — the file will not be processed by marp-cli. Always include it.
- **Using `===` or `***` as slide separators** — only `---` works.
- **Overlay markers inside a list item** — keep them on their own lines, not on the same line as a bullet.
- **Image paths** — use paths relative to `slides.md`, not to the project root.
- **Math** — marp supports `$inline$` and `$$display$$` math via KaTeX when the marp-cli is invoked with `--html`. The bundled `compile_marp.sh` already passes `--html`.
