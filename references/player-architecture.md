# Player Architecture Reference

How the bundled `assets/player/` works and how `build_video.py` wires it up. Read this when debugging the video output, extending the player, or wiring TTS audio in the future.

## Files

- `index.html` — page shell, control bar, slot for `timeline.json` injection.
- `player.css` — layout, slide stage, overlay fade animation, control bar styling.
- `player.js` — main loop, timeline driver, overlay manager, audio sync.

## DOM layout

```html
<div id="stage">
  <img id="slide" src="slides/01.png">      <!-- current slide image -->
  <div id="mathwrites"></div>                <!-- hand-written math layer -->
  <div id="overlays"></div>                  <!-- overlay badges + in-place reveal crops -->
  <div id="captions"><span id="caption-text"></span></div>  <!-- subtitle bar -->
</div>
<audio id="narration" preload="metadata">    <!-- narration.wav present iff the TTS phase ran -->
  <source src="narration.wav" type="audio/wav">
</audio>
<div id="controls">
  <button id="play">▶</button>
  <button id="cc" class="toggle on">CC</button>  <!-- caption toggle; key "c" -->
  <input id="seek" type="range" min="0" max="100" value="0">
  <span id="time">0:00 / 0:00</span>
  <select id="speed"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option></select>
</div>
```

## Timeline data

`timeline.json` (produced by `derive_timeline.py`, or by `synthesize_tts.py` when TTS is
enabled — the latter adds an `"audio": "narration.wav"` field and times everything against
the real synthesized audio) shape:

```json
{
  "total_duration": 312.4,
  "audio": "narration.wav",
  "slides": [
    {"index": 1, "start": 0.0, "end": 32.5, "image": "slides.images/01.png"},
    {"index": 2, "start": 32.5, "end": 71.2, "image": "slides.images/02.png"}
  ],
  "overlays": [
    {"slide": 2, "id": "key-insight", "start": 45.1, "end": 60.7, "label": "關鍵推論",
     "bbox": {"x": 0.06, "y": 0.62, "w": 0.88, "h": 0.08}}
  ],
  "captions": [
    {"start": 32.5, "end": 38.9, "text": "我們先寫下要求的東西…"}
  ],
  "mathwrites": [
    {"slide": 2, "id": "bayes", "bbox": {"x": 0.06, "y": 0.53, "w": 0.88, "h": 0.13},
     "segs": [{"seg": "lhs", "svg": "<svg …>", "valign": "-0.566ex", "start": 45.1, "end": 51.0}]}
  ]
}
```

`captions` is one entry per SRT cue that carried spoken text: `[overlay:*]` markers are
stripped (they are timing metadata, not narration) and the **original** text is kept —
in the voiced path the slides/SRT stay Traditional even though only the Simplified copy
was fed to the TTS engine. Times are absolute, from the same source as the rest of the
timeline (SRT-estimated in the silent path, real audio in the voiced path).

`mathwrites` is present only when the deck declares mathwrite blocks. `bbox` is the
formula's position as fractions of the slide; each seg carries a standalone MathJax
SVG (from `.mathwrite.json`) plus its narration-derived time window.

`build_video.py` injects the `<script>const TIMELINE = {...};</script>` block into
`index.html` and copies `slides.images/` (including the `NN.reveal.png` crop sources) into
`video/slides/`. It does **not** emit overlay/mathwrite DOM — `player.js` builds those at
runtime from `TIMELINE`. Each overlay entry carries `{slide, id, label, start, end}` plus,
when measured, a `bbox` (fractions of the slide) used for the in-place reveal.

## Time source

Two clock modes; `player.js` picks one at startup:

1. **Audio mode** — if `<audio id="narration">` has a `src` that loads successfully (`loadedmetadata` fires with a positive `duration`), the player uses `audio.currentTime` as authoritative. Play/pause/seek/speed all delegate to the audio element.

2. **Timer mode** — fallback. Uses `requestAnimationFrame` to advance an internal `currentTime` counter; play/pause toggles the loop, seek jumps the counter, speed multiplies the per-frame delta.

In both modes, the same `tick(t)` function runs to update slide and overlays based on `t` against `TIMELINE`.

## Tick algorithm

On each tick with current time `t`:

1. Find the slide whose `[start, end)` contains `t`. Skip if it's the same as the current slide.
2. When the slide changes, swap the `<img id="slide">` `src`.
3. For every overlay whose `slide` matches the current slide:
   - If `start ≤ t < end` and not currently shown → fade in (CSS class `visible`).
   - If `t ≥ end` and currently shown → fade out (remove class).
4. Find the caption whose `[start, end)` contains `t` and write it into `#caption-text`
   (empty string clears the bar); skipped when captions are toggled off.
5. Update `#seek` and `#time`.

Binary search (or simple linear scan, since N is small) finds the current slide in O(log N) / O(N).

## Overlay rendering

Each overlay produces up to two elements, both built by `player.js` from `TIMELINE.overlays`:

1. **Badge** (`.overlay`) — a small top-right card showing the overlay's `label`, faded in
   only during `[start, end)` as a "look here" highlight. Stacks vertically when several
   overlays are active (`data-stack`).
2. **In-place reveal** (`.overlay-reveal`, only when the overlay has a `bbox`) — a box
   positioned over the slide region the base PNG left blank, with the matching crop of
   `NN.reveal.png` as a shifted background (`background-size` = displayed slide size,
   `background-position` = `-(bbox.x·W), -(bbox.y·H)`). It fades in at `start` and — unlike
   the badge — **stays visible until the slide changes**, because revealed content was merely
   delayed, not transient. `layoutOverlayReveals()` recomputes geometry on slide change /
   resize / export; reveal crops are preloaded so the frame export never captures a half-
   loaded image.

The base PNG has the overlay region blanked (`compile_marp.sh`), so before `start` the area
is empty and the reveal makes the content appear exactly where it lives. An overlay whose
`bbox` could not be measured gets only the badge (its content stays blanked — a warning is
emitted by `render_mathwrite.py`).

## Captions (subtitle bar)

The `#captions` bar shows the narration text at the bottom of the stage, in sync with both
clock modes (it works in silent timer mode too, so a voiceless deck is still followable).

- Data is `TIMELINE.captions` (see above). On each tick the player linear-scans for the
  cue whose `[start, end)` contains `t` and writes its text; an empty result clears the bar.
- The `CC` button (and the `c` key) toggles the bar via `setCaptions(on)`, which flips
  `#captions.off` and the button's `.on` class. State is in-memory only (not persisted).
- If `TIMELINE.captions` is empty the `CC` button hides itself.
- Styling is fixed-position over the slide, `pointer-events: none`, so it never blocks
  clicks or the mathwrite/overlay layers. Text stays in its original script (Traditional
  for ZH decks).

## Mathwrite rendering (hand-written math)

The `#mathwrites` layer hand-writes formulas into the region that the PNG render left
blank (`compile_marp.sh` hides `.mathwrite` divs for the PNG pass only). Key points:

- Each block is a `.mathwrite-box` absolutely positioned from `bbox` (fractions of the
  displayed slide `<img>`, recomputed on resize/slide change). Inside, the segment SVGs
  sit on one text baseline (MathJax's `vertical-align`), and the row is scaled to fit
  the box.
- Drawing is a **pure function of time** `t`: a segment's progress is
  `(t − start) / (end − start)`, clamped, mapped to a length along the segment's
  pieces (collected in reading order). Each glyph is **truly single-stroke
  hand-written**: `mwCollectNodes` replaces the MathJax glyph outline with its
  **Hershey single-stroke centerline** (`window.HERSHEY_FONT` from
  `hershey-font.js`; `mwCodeToKey` maps the glyph's codepoint — math-alphanumeric
  letters/digits fold to ASCII, Greek to `g:<slot>`, a few hand-authored math
  symbols to `s:<name>`), fit into the glyph's own `getBBox` and **y-flipped**
  (Hershey is y-down, MathJax glyph-local is y-up). That centerline path is
  `fill:none`, stroked at the constant `MW_PEN_EM` (font units → one uniform
  chalk/marker pen), and revealed by sweeping `stroke-dashoffset` from `len`→`0`
  along the real pen trajectory — **not** an outline trace and **not** a fade. A
  glyph with no Hershey mapping degrades to a `fade` node (clean opacity ramp,
  never an outline trace); invisible operators (U+2061…) are skipped; fraction-bar
  `<rect>`s grow by width. A single **pen-nib element** (`.mw-pen`) rides the
  **true stroke frontier** — `getPointAtLength` at the nib's distance along the
  centerline, mapped from the path's local space through `svgCTM⁻¹·elCTM` into
  viewBox space and then into the SVG's on-screen box (so the row's CSS
  translate/scale is honoured) — and rests at the end of the last glyph between
  glyphs, releasing when the segment is fully written. `mwCollectNodes` **defers**
  (leaves `seg.nodes` null to retry next frame / on slide-image load) while the box
  is still collapsed, so it never caches a degraded all-fallback state. Because
  every value is a function of `t` (no one-shot CSS animation), seeking lands on the
  exact partially-written state; the frame export refreshes the nib after layout
  settles. The single-stroke font data is generated offline by
  `scripts/gen_hershey_font.py` from the bundled public-domain `assets/hershey/*.jhf`.
- A segment with a zero-length window (missing SRT markers — the timeline producers
  warn) appears fully drawn from slide start, so the formula never silently vanishes.
- Ink colour comes from `--mw-ink` on `#mathwrites` (default near-black).
- Deep link: opening `index.html#t=42.5` starts the player paused at that time (also
  handy for screenshot-testing the half-written state).

## TTS audio (implemented — `synthesize_tts.py`)

The TTS phase is wired up via `scripts/synthesize_tts.py`, which drives the IndexTTS-2
MLX-Swift engine. It replaces `derive_timeline.py` in the audio path and works exactly as the
old "future extension" note anticipated — pacing is taken from the real audio, not the
planned SRT timestamps:

1. Each page's SRT cues are flattened to one globally-indexed list; `[overlay:*]` markers are
   stripped from the spoken text (they are timing metadata, not narration).
2. All cues are synthesized in a **single** `indextts2 --srt` batch call (the model loads once
   per process, so per-cue invocations would be ruinously slow). Output is one
   `combined_<NNN>.wav` per cue.
3. The per-cue wavs are concatenated with stdlib `wave` into `<slug>/narration.wav`
   (16-bit PCM mono 22.05 kHz), inserting `--cue-gap` silence between cues and `--page-gap`
   between pages.
4. `timeline.json` is rebuilt from the **actual** frame offsets: each slide's window spans its
   page's audio (slide windows kept contiguous so the player never lands in a gap), and each
   overlay's start/end come from where its opener/closer cue landed in the audio.

`build_video.py` then copies `narration.wav` into `video/` and the player auto-detects it on
load. Because the timeline is derived from the same audio, `audio.duration ≈
TIMELINE.total_duration` by construction.

## MP4 export (`scripts/export_mp4.mjs`)

The player doubles as a deterministic frame source for offline MP4 rendering. Because every
visual is a pure function of `t`, a headless browser can render frame `i` at `t = i/fps` and
screenshot it — no real-time playback, exact hand-written-math half-stroke states.

The player exposes a hook (installed in `init`, never touched by normal playback):

```js
window.__lectureExport = {
  total,                 // total_duration (seconds)
  slides,                // slide count
  prepare(),             // hide #controls + black background; returns total
  renderAt(t) -> Promise // render exactly one frame at t; resolves when settled
};
```

`renderAt(t)` pauses any playback, seeks, swaps the slide and **awaits `slide.decode()`** on a
page change, repaints overlays/mathwrites/captions, awaits `document.fonts.ready`, then waits
two `requestAnimationFrame`s (with a `setTimeout` guard against headless rAF throttling) so
stroke-dasharray styles and the mathwrite transform have settled before the screenshot.

`export_mp4.mjs` (Node, **no npm deps**) drives it:

1. Auto-detects Chrome (same candidates as `render_mathwrite.py`), launches it
   `--headless=new --remote-debugging-port=0`, and parses the DevTools WebSocket URL from
   stderr. A tiny CDP client (≈40 lines) over Node's built-in `WebSocket` correlates command
   ids and dispatches events; it attaches to a fresh target with `Target.attachToTarget
   {flatten:true}` and talks `Page`/`Emulation`/`Runtime` on that session.
2. Capture size defaults to the **first slide PNG's aspect ratio** (height capped at 1080, so a
   4:3 deck → 1440×1080, a 16:9 deck → 1920×1080) — never letterboxed. `--width`/`--height`
   override; dimensions are forced even for `yuv420p`.
3. `Emulation.setDeviceMetricsOverride` sets the viewport; once `#controls` is hidden the stage
   fills it exactly. For each of `ceil(total*fps)` frames: `Runtime.evaluate(renderAt(t),
   awaitPromise:true)` → `Page.captureScreenshot` → the PNG buffer is piped straight into
   `ffmpeg -f image2pipe` (no temp files unless `--keep-frames`).
4. ffmpeg encodes `libx264`/`yuv420p` (`-crf 18 -preset medium -movflags +faststart`); if
   `video/narration.wav` exists it is muxed as AAC with `-shortest` (silent decks export
   video-only). Output: `<topic>/video/lecture.mp4`.

A real lecture is many thousands of frames, so this is a minutes-long, compute-heavy step (like
TTS). A future optimisation is to hold static stretches (same slide, no overlay/caption/mathwrite
change) as repeated frames instead of re-screenshotting every one.

## Browser compatibility

The player uses only:

- ES2020 features (no transpilation needed).
- `<audio>`, `<img>`, CSS transitions.
- `requestAnimationFrame`.

Tested mental model: any Chromium-based browser, Safari 15+, Firefox 90+.

## Debugging tips

- Overlay never appears: check `console.log(TIMELINE.overlays)` — if missing, `derive_timeline.py` did not parse the markers. Inspect the SRT for unbalanced `[overlay:*]` tags.
- Slides advance too fast: usually means a per-page SRT was empty or malformed; `derive_timeline.py` falls back to a default 20-second slide. Re-run the failing sub-agent.
- Audio drifts from slides: confirm `audio.duration` matches `TIMELINE.total_duration` within 1%. With `synthesize_tts.py` they are derived from the same audio and should match by construction; a mismatch usually means `narration.wav` was rebuilt without re-running `build_video.py` (or vice versa) — re-run both.
- No audio / player stuck in timer mode: confirm `video/narration.wav` exists. The TTS phase writes `<slug>/narration.wav`; `build_video.py` only copies it in if it is present at build time, so run `synthesize_tts.py` **before** `build_video.py`.
