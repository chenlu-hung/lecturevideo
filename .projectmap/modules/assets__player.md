# Module: `assets/player`

## Summary
A single dependency-free IIFE (`player.js`) that runs the reveal.js-style auto-play lecture video in the browser — the only client-side piece of the pipeline. It consumes the `TIMELINE` global injected by `build_video.py`, advancing playback off a `<audio>` narration track when one is present (`tryAudio` at player.js:180) or a `requestAnimationFrame` timer fallback otherwise, switching slide images and fading overlay badges in/out at their timeline-derived times (`setSlide`/`updateOverlays`). It also wires the transport controls — play/pause, seek bar, speed select, and keyboard shortcuts (`wire` at player.js:204). Lives at the runtime end of the pipeline alongside the `index.html`/`player.css` templates in the same `assets/player/` directory.

<!-- projectmap:auto:start (generated — do not edit by hand) -->
## Files (1)
- `assets/player/player.js`

## Public symbols (19)
- `function $` — assets/player/player.js:9
- `function buildOverlayElements` — assets/player/player.js:34
- `function findSlide` — assets/player/player.js:52
- `function setSlide` — assets/player/player.js:64
- `function updateOverlays` — assets/player/player.js:92
- `function fmt` — assets/player/player.js:111
- `function renderTime` — assets/player/player.js:118
- `function tick` — assets/player/player.js:125
- `function loop` — assets/player/player.js:132
- `function play` — assets/player/player.js:148
- `function pause` — assets/player/player.js:157
- `function togglePlay` — assets/player/player.js:163
- `function seekTo` — assets/player/player.js:167
- `function setSpeed` — assets/player/player.js:174
- `function tryAudio` — assets/player/player.js:180
- `function onLoaded` — assets/player/player.js:182
- `function onError` — assets/player/player.js:189
- `function cleanup` — assets/player/player.js:190
- `function wire` — assets/player/player.js:204

## Dependencies (imports)
- _none detected_
<!-- projectmap:auto:end -->
