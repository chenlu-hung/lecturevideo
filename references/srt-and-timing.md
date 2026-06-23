# SRT & Timing Reference

The exact format and conventions every per-page SRT must follow. This file is referenced both by the main agent (for validation) and inlined verbatim into sub-agent prompts (for generation).

## SRT format

Standard SRT, UTF-8, LF line endings:

```
1
00:00:00,000 --> 00:00:06,500
歡迎來到這堂課，今天我們要談的是反向傳播。

2
00:00:06,500 --> 00:00:13,200
反向傳播是訓練神經網路最核心的演算法。

3
00:00:13,200 --> 00:00:20,000
[overlay:chain-rule]它的關鍵想法是運用鏈鎖律，把誤差訊號往回層層傳遞。[/overlay:chain-rule]
```

Rules:

- Each cue has three parts: index, timestamp, text.
- Cue indices start at 1 and are monotonically increasing by 1.
- Timestamps use `HH:MM:SS,mmm` (comma before milliseconds — not period).
- Cues must not overlap. The next cue's start should equal the current cue's end (or be ≥ it; small gaps are acceptable for natural pauses).
- Blank line separates cues.

## Per-page timing convention

Each per-page `.srt` file starts at `00:00:00,000` — local to that page. The `derive_timeline.py` script later concatenates them into a global timeline.

Per-cue length guidance:

- Aim for 6–12 seconds per cue.
- Maximum 18 seconds (longer cues frustrate viewers reading along).
- Minimum 2 seconds (avoid flicker).

Per-page total length guidance:

- Title slide: 8–20 seconds.
- Regular content slide: 30–90 seconds.
- Heavy detail / formula slide: up to 150 seconds.

The main agent passes a `target_seconds` value to each sub-agent based on `length_minutes / page_count` and the slide's content density.

## Narration style

Write as the lecturer speaking warmly to the class. Concrete style requirements:

- **親切詳細** — friendly and thorough. Use "我們" / "we" rather than "你必須" / "you must".
- **承上啟下** — transition phrases between cues ("接下來……", "更進一步來看……").
- **舉例** — when introducing a concept, give a concrete example or analogy in the same cue or the next.
- **不照唸投影片** — do not just read bullets verbatim. Expand each bullet into a sentence or two of explanation.
- **問句帶動思考** — occasional rhetorical questions ("這樣設計的好處是什麼？") are welcome.
- **語言** — strictly the user-specified language. If the user says `繁體中文`, do not switch to simplified or English mid-cue.

Length per slide should match the slide's information density. A slide with five bullets needs a longer narration than a slide with one image and a caption.

## Overlay tagging

Whenever the narration enters the topic that the overlay highlights, wrap the relevant span with `[overlay:<id>]…[/overlay:<id>]` markers inside the cue text.

Rules:

- The opener `[overlay:foo]` and closer `[/overlay:foo]` may span multiple cues. The opener's cue start time becomes the overlay's `start`; the closer's cue end time becomes the overlay's `end`.
- Every overlay id declared on a slide must appear in **exactly one** opener and **exactly one** closer in that slide's SRT.
- Markers must be balanced (each opener has a closer; no nested same-id markers).
- The markers are stripped before the player displays subtitles, so they will not show up to the viewer.
- The markers do not affect timestamps — they live inside the cue text.
- Each cue's text (markers removed, **original script kept**) becomes one entry in
  `timeline.json`'s `captions[]`, which the player shows as a bottom subtitle bar in sync
  with playback. Write cue text as clean spoken sentences — it is read aloud *and* shown.

### Example

Slide 7 has overlays `chain-rule` and `lr-tip`. Its `07.srt`:

```
1
00:00:00,000 --> 00:00:08,000
這頁我們來看反向傳播怎麼把誤差分配回每一層的權重。

2
00:00:08,000 --> 00:00:18,000
[overlay:chain-rule]關鍵的工具是鏈鎖律——你可以把它想成一條梯度的接力棒，每一層接力把誤差傳給上一層。

3
00:00:18,000 --> 00:00:26,000
這樣一來，即使網路有十層、二十層，我們仍然可以系統性地算出每個權重該怎麼動。[/overlay:chain-rule]

4
00:00:26,000 --> 00:00:34,000
[overlay:lr-tip]實務上學習率怎麼選呢？我建議從 1e-3 開始，再觀察 loss 曲線決定要放大或縮小。[/overlay:lr-tip]
```

Resulting overlay times (after `derive_timeline.py`, assuming this is page 7 starting at global `t=180s`):

- `chain-rule`: start=188s, end=206s.
- `lr-tip`: start=206s, end=214s.

## Mathwrite segment tagging

Slides may declare mathwrite blocks (hand-written math; see `marp-and-overlays.md`).
Each segment behaves exactly like an overlay whose id is `<block-id>.<seg>`:

- Wrap the narration that explains a segment with `[overlay:bayes.lhs]…[/overlay:bayes.lhs]`.
- While the marker pair is open, the player is **writing that part of the formula on
  screen**, so narrate as a teacher writing on the board ("我們先寫下後驗機率…",
  "接著等號右邊是…").
- Tag the segments **in their declared order**, each exactly once, ideally back-to-back —
  the formula is written left to right without long pauses.
- Give each segment at least one full cue (≥ 3–4 seconds) of narration.

### Remarks about a formula appear after it is written

When a slide hand-writes a formula and then makes a remark or draws a conclusion about
it (e.g. "顯然，$\frac{d}{dx}(x\sin x)\neq x\cos x$", "注意右邊正是我們要的", "於是得證"),
the remark must be narrated **entirely after** the formula's final `mathwrite-seg`
closer — never interleaved before the writing is finished. Concretely:

- Place the remark's cue(s), and any `[overlay:<id>]` window for the remark, strictly
  after the last `[/overlay:<block-id>.<last-seg>]` of the formula it comments on.
- The remark reads as the *takeaway once the writing is complete* — the lecturer finishes
  writing, pauses, then points out what the finished line means.

This keeps the spoken order natural. (Whether the on-slide remark text is *visually*
withheld until that moment depends on how the slide is authored — see
`marp-and-overlays.md` §"Timing when content appears".)

## Validation rules (used by main agent)

A page's SRT is valid iff all of the following hold:

1. File exists and is non-empty.
2. Parses as SRT (each cue has index, timestamp line, ≥1 text line, blank separator except after the last cue).
3. Cue indices are 1, 2, 3, … with no gaps.
4. Timestamps are monotonic non-decreasing; no cue's `start ≥ end`.
5. For every overlay id declared in `.slides.json` for this page — including every
   mathwrite segment id `<block-id>.<seg>` — the SRT contains exactly one
   `[overlay:<id>]` opener and one `[/overlay:<id>]` closer.
6. No stray markers (every opener has a closer; no closers without openers; no nested same-id markers).
7. Total page duration is in [15, 180] seconds (warn outside, fail outside [5, 300]).

If validation fails, re-dispatch one sub-agent to regenerate only the affected pages, passing the validation error so the sub-agent can correct it.
