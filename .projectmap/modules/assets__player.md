# Module: `assets/player`

## Summary
A single dependency-free IIFE (`player.js`) that runs the reveal.js-style auto-play lecture video in the browser — the only client-side piece of the pipeline. It consumes the `TIMELINE` global injected by `build_video.py`, advancing playback off a `<audio>` narration track when one is present (`tryAudio` at player.js:180) or a `requestAnimationFrame` timer fallback otherwise, switching slide images and fading overlay badges in/out at their timeline-derived times (`setSlide`/`updateOverlays`). It also hand-writes mathwrite formulas into the slide region the PNG render left blank: segment SVGs are stroke-revealed glyph-by-glyph then filled as a pure function of the current time (`mwPaintSeg`/`layoutMathwrites` at player.js:139,155), so seeking lands on the exact half-written state. It renders the narration text as a synced bottom subtitle bar from the timeline's `captions[]` (`findCaption`/`updateCaptions`/`setCaptions` at player.js:266,275,282), toggled by the `CC` button or `c` key. Transport controls — play/pause, seek bar, speed select, keyboard shortcuts, and `#t=<seconds>` deep links — are wired in `wire` (player.js:386). Lives at the runtime end of the pipeline alongside the `index.html`/`player.css` templates in the same `assets/player/` directory.

<!-- projectmap:auto:start (generated — do not edit by hand) -->
## Files (1)
- `assets/player/player.js`

## Public symbols (28)
- `function $` — assets/player/player.js:9
- `function buildOverlayElements` — assets/player/player.js:42
- `function buildMathwrites` — assets/player/player.js:69
- `function mwCollectNodes` — assets/player/player.js:93
- `function mwPaintNode` — assets/player/player.js:127
- `function mwPaintSeg` — assets/player/player.js:145
- `function layoutMathwrites` — assets/player/player.js:161
- `function updateMathwrites` — assets/player/player.js:191
- `function findSlide` — assets/player/player.js:205
- `function setSlide` — assets/player/player.js:217
- `function updateOverlays` — assets/player/player.js:246
- `function findCaption` — assets/player/player.js:266
- `function updateCaptions` — assets/player/player.js:275
- `function setCaptions` — assets/player/player.js:282
- `function fmt` — assets/player/player.js:291
- `function renderTime` — assets/player/player.js:298
- `function tick` — assets/player/player.js:305
- `function loop` — assets/player/player.js:314
- `function play` — assets/player/player.js:330
- `function pause` — assets/player/player.js:339
- `function togglePlay` — assets/player/player.js:345
- `function seekTo` — assets/player/player.js:349
- `function setSpeed` — assets/player/player.js:356
- `function tryAudio` — assets/player/player.js:362
- `function onLoaded` — assets/player/player.js:364
- `function onError` — assets/player/player.js:371
- `function cleanup` — assets/player/player.js:372
- `function wire` — assets/player/player.js:386

## Dependencies (imports)
- _none detected_
<!-- projectmap:auto:end -->
