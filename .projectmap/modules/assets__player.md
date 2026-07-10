# Module: `assets/player`

## Summary
A single dependency-free IIFE (`player.js`) that runs the reveal.js-style auto-play lecture video in the browser — the only client-side piece of the pipeline. It consumes the `TIMELINE` global injected by `build_video.py`, advancing playback off a `<audio>` narration track when one is present (`tryAudio` at player.js:180) or a `requestAnimationFrame` timer fallback otherwise, switching slide images and fading overlay badges in/out at their timeline-derived times (`setSlide`/`updateOverlays`). It also hand-writes mathwrite formulas into the slide region the PNG render left blank: segment SVGs are stroke-revealed glyph-by-glyph then filled as a pure function of the current time (`mwPaintSeg`/`layoutMathwrites` at player.js:139,155), so seeking lands on the exact half-written state. It renders the narration text as a synced bottom subtitle bar from the timeline's `captions[]` (`findCaption`/`updateCaptions`/`setCaptions` at player.js:266,275,282), toggled by the `CC` button or `c` key. Transport controls — play/pause, seek bar, speed select, keyboard shortcuts, and `#t=<seconds>` deep links — are wired in `wire` (player.js:386). It also exposes a deterministic export hook (`exportPrepare`/`exportRenderAt` at player.js:395,367, surfaced as `window.__lectureExport.renderAt(t)`) so the optional Phase 5 `scripts/export_mp4.mjs` can render any frame as a pure function of time t and capture it headlessly into an MP4. Lives at the runtime end of the pipeline alongside the `index.html`/`player.css` templates in the same `assets/player/` directory.

<!-- projectmap:auto:start (generated — do not edit by hand) -->
## Files (2)
- `assets/player/hershey-font.js`
- `assets/player/player.js`

## Public symbols (43)
- `function $` — assets/player/player.js:9
- `function buildOverlayElements` — assets/player/player.js:43
- `function buildMathwrites` — assets/player/player.js:94
- `function mwCodeToKey` — assets/player/player.js:139
- `function mwHersheyStrokes` — assets/player/player.js:153
- `function mwStrokesBBox` — assets/player/player.js:157
- `function mwHersheyPaths` — assets/player/player.js:168
- `function mwViewBoxBBox` — assets/player/player.js:190
- `function mwReadingOrder` — assets/player/player.js:219
- `function mwCollectNodes` — assets/player/player.js:254
- `function mwPaintNode` — assets/player/player.js:325
- `function mwPaintSeg` — assets/player/player.js:342
- `function mwPenPoint` — assets/player/player.js:377
- `function mwPenPointFallback` — assets/player/player.js:408
- `function mwUpdatePen` — assets/player/player.js:414
- `function layoutMathwrites` — assets/player/player.js:428
- `function layoutOverlayReveals` — assets/player/player.js:465
- `function relayout` — assets/player/player.js:491
- `function updateMathwrites` — assets/player/player.js:496
- `function findSlide` — assets/player/player.js:513
- `function setSlide` — assets/player/player.js:525
- `function updateOverlays` — assets/player/player.js:562
- `function findCaption` — assets/player/player.js:592
- `function updateCaptions` — assets/player/player.js:601
- `function setCaptions` — assets/player/player.js:608
- `function fmt` — assets/player/player.js:617
- `function renderTime` — assets/player/player.js:624
- `function tick` — assets/player/player.js:631
- `function loop` — assets/player/player.js:640
- `function play` — assets/player/player.js:656
- `function pause` — assets/player/player.js:665
- `function togglePlay` — assets/player/player.js:671
- `function seekTo` — assets/player/player.js:675
- `function setSpeed` — assets/player/player.js:682
- `function exportRenderAt` — assets/player/player.js:693
- `function finish` — assets/player/player.js:714
- `function exportPrepare` — assets/player/player.js:721
- `function tryAudio` — assets/player/player.js:742
- `function onLoaded` — assets/player/player.js:744
- `function onError` — assets/player/player.js:751
- `function cleanup` — assets/player/player.js:752
- `function wire` — assets/player/player.js:766
- `function onLayoutChange` — assets/player/player.js:785

## Dependencies (imports)
- _none detected_
<!-- projectmap:auto:end -->
