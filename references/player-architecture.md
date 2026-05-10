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
  <div id="overlays"></div>                  <!-- overlay <div>s rendered here -->
</div>
<audio id="narration" preload="metadata">    <!-- empty src in v0; future TTS -->
  <source src="audio.mp3" type="audio/mpeg">
</audio>
<div id="controls">
  <button id="play">▶</button>
  <input id="seek" type="range" min="0" max="100" value="0">
  <span id="time">0:00 / 0:00</span>
  <select id="speed"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option></select>
</div>
```

## Timeline data

`timeline.json` (produced by `derive_timeline.py`) shape:

```json
{
  "total_duration": 312.4,
  "slides": [
    {"index": 1, "start": 0.0, "end": 32.5, "image": "slides.images/01.png"},
    {"index": 2, "start": 32.5, "end": 71.2, "image": "slides.images/02.png"}
  ],
  "overlays": [
    {"slide": 2, "id": "key-insight", "start": 45.1, "end": 60.7, "label": "關鍵推論"}
  ]
}
```

`build_video.py` reads this and injects:

1. `<script>const TIMELINE = {...};</script>` block in `index.html`.
2. One `<div class="overlay" data-id=... data-start=... data-end=...>` per overlay, initially `opacity: 0`.

The injected overlay `<div>`s are blank — the player draws a label badge and a soft highlight border. If a richer overlay (custom HTML) is desired in future, extend `build_video.py` to read overlay content from the slide's markdown body inside the overlay markers and inject it as inner HTML.

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
4. Update `#seek` and `#time`.

Binary search (or simple linear scan, since N is small) finds the current slide in O(log N) / O(N).

## Overlay rendering

CSS handles the fade:

```css
.overlay {
  position: absolute;
  inset: 4% 4% auto auto;
  padding: 8px 14px;
  background: rgba(255, 220, 0, 0.95);
  border-radius: 6px;
  font-weight: 600;
  opacity: 0;
  transform: translateY(-6px);
  transition: opacity 240ms ease, transform 240ms ease;
}
.overlay.visible {
  opacity: 1;
  transform: translateY(0);
}
```

The overlay text shown is the `label` from `.slides.json`. The position (top-right by default) can be customised by adding a `position: <region>` field to the overlay annotation in slides.md and propagating it through `split_slides.py` and `build_video.py`.

## Adding TTS audio later (future extension)

When a future TTS phase becomes part of the skill:

1. Generate per-cue audio chunks by feeding each SRT cue text to the TTS engine.
2. Concatenate (with optional per-page silence) into `audio.mp3`.
3. Place at `<output>/<slug>/video/audio.mp3`.
4. The player auto-detects on next load and switches to audio mode.

If TTS pacing diverges from the planned SRT timing, regenerate `timeline.json` from the actual audio: each cue's `audio_offset` overrides the planned timestamp, and slide / overlay times are recomputed from `audio_offset`. This keeps narration and visuals locked.

## Browser compatibility

The player uses only:

- ES2020 features (no transpilation needed).
- `<audio>`, `<img>`, CSS transitions.
- `requestAnimationFrame`.

Tested mental model: any Chromium-based browser, Safari 15+, Firefox 90+.

## Debugging tips

- Overlay never appears: check `console.log(TIMELINE.overlays)` — if missing, `derive_timeline.py` did not parse the markers. Inspect the SRT for unbalanced `[overlay:*]` tags.
- Slides advance too fast: usually means a per-page SRT was empty or malformed; `derive_timeline.py` falls back to a default 20-second slide. Re-run the failing sub-agent.
- Audio drifts from slides: confirm `audio.duration` matches `TIMELINE.total_duration` within 1%. If not, regenerate `timeline.json` from the audio (future TTS hook).
