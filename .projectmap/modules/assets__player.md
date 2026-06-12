# Module: `assets/player`

## Summary
A single dependency-free IIFE (`player.js`) that runs the reveal.js-style auto-play lecture video in the browser — the only client-side piece of the pipeline. It consumes the `TIMELINE` global injected by `build_video.py`, advancing playback off a `<audio>` narration track when one is present (`tryAudio` at player.js:180) or a `requestAnimationFrame` timer fallback otherwise, switching slide images and fading overlay badges in/out at their timeline-derived times (`setSlide`/`updateOverlays`). It also hand-writes mathwrite formulas into the slide region the PNG render left blank: segment SVGs are stroke-revealed glyph-by-glyph then filled as a pure function of the current time (`mwPaintSeg`/`layoutMathwrites` at player.js:139,155), so seeking lands on the exact half-written state. Transport controls — play/pause, seek bar, speed select, keyboard shortcuts, and `#t=<seconds>` deep links — are wired in `wire` (player.js:353). Lives at the runtime end of the pipeline alongside the `index.html`/`player.css` templates in the same `assets/player/` directory.

<!-- projectmap:auto:start (generated — do not edit by hand) -->
## Files (1)
- `assets/player/player.js`

## Public symbols (25)
- `function $` — assets/player/player.js:9
- `function buildOverlayElements` — assets/player/player.js:36
- `function buildMathwrites` — assets/player/player.js:63
- `function mwCollectNodes` — assets/player/player.js:87
- `function mwPaintNode` — assets/player/player.js:121
- `function mwPaintSeg` — assets/player/player.js:139
- `function layoutMathwrites` — assets/player/player.js:155
- `function updateMathwrites` — assets/player/player.js:185
- `function findSlide` — assets/player/player.js:199
- `function setSlide` — assets/player/player.js:211
- `function updateOverlays` — assets/player/player.js:240
- `function fmt` — assets/player/player.js:259
- `function renderTime` — assets/player/player.js:266
- `function tick` — assets/player/player.js:273
- `function loop` — assets/player/player.js:281
- `function play` — assets/player/player.js:297
- `function pause` — assets/player/player.js:306
- `function togglePlay` — assets/player/player.js:312
- `function seekTo` — assets/player/player.js:316
- `function setSpeed` — assets/player/player.js:323
- `function tryAudio` — assets/player/player.js:329
- `function onLoaded` — assets/player/player.js:331
- `function onError` — assets/player/player.js:338
- `function cleanup` — assets/player/player.js:339
- `function wire` — assets/player/player.js:353

## Dependencies (imports)
- _none detected_
<!-- projectmap:auto:end -->
