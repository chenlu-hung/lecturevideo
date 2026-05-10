(() => {
  "use strict";

  if (typeof TIMELINE === "undefined") {
    console.error("TIMELINE missing — build_video.py was not run.");
    return;
  }

  const $ = (id) => document.getElementById(id);
  const stage = $("stage");
  const slide = $("slide");
  const overlaysRoot = $("overlays");
  const playBtn = $("play");
  const seek = $("seek");
  const timeLabel = $("time");
  const pageInfo = $("page-info");
  const speedSel = $("speed");
  const audio = $("narration");

  const SLIDES = TIMELINE.slides || [];
  const OVERLAYS = TIMELINE.overlays || [];
  const TOTAL = TIMELINE.total_duration || 0;

  let useAudio = false;
  let currentTime = 0;
  let playing = false;
  let lastFrame = 0;
  let speed = 1;
  let currentSlideIndex = -1;
  const overlayState = new Map(); // overlayId -> visible boolean
  const overlayEls = new Map();   // overlayId -> HTMLElement

  // ---------- overlay rendering ----------
  function buildOverlayElements() {
    overlaysRoot.innerHTML = "";
    overlayEls.clear();
    OVERLAYS.forEach((ov) => {
      const el = document.createElement("div");
      el.className = "overlay";
      el.dataset.id = ov.id;
      el.dataset.start = ov.start;
      el.dataset.end = ov.end;
      el.dataset.slide = ov.slide;
      el.textContent = ov.label || ov.id;
      el.style.display = "none";
      overlaysRoot.appendChild(el);
      overlayEls.set(ov.id + "@" + ov.slide, el);
      overlayState.set(ov.id + "@" + ov.slide, false);
    });
  }

  function findSlide(t) {
    // Linear scan — N is small (≤ tens of slides typically).
    for (let i = 0; i < SLIDES.length; i++) {
      const s = SLIDES[i];
      if (t >= s.start && t < s.end) return i;
    }
    if (SLIDES.length && t >= SLIDES[SLIDES.length - 1].end) {
      return SLIDES.length - 1;
    }
    return 0;
  }

  function setSlide(i) {
    if (i === currentSlideIndex) return;
    currentSlideIndex = i;
    const s = SLIDES[i];
    if (!s) return;
    slide.src = s.image;
    pageInfo.textContent = `${s.index} / ${SLIDES.length}`;
    // Hide overlays from other slides.
    overlayEls.forEach((el, key) => {
      const overlaySlide = parseInt(el.dataset.slide, 10);
      if (overlaySlide !== s.index) {
        el.style.display = "none";
        el.classList.remove("visible");
        overlayState.set(key, false);
      } else {
        el.style.display = "";
      }
    });
    // Restack overlays for this slide.
    let stack = 0;
    overlayEls.forEach((el) => {
      if (parseInt(el.dataset.slide, 10) === s.index) {
        el.dataset.stack = String(stack);
        stack++;
      }
    });
  }

  function updateOverlays(t) {
    const s = SLIDES[currentSlideIndex];
    if (!s) return;
    overlayEls.forEach((el, key) => {
      if (parseInt(el.dataset.slide, 10) !== s.index) return;
      const start = parseFloat(el.dataset.start);
      const end = parseFloat(el.dataset.end);
      const shouldShow = t >= start && t < end;
      const isShown = overlayState.get(key);
      if (shouldShow && !isShown) {
        el.classList.add("visible");
        overlayState.set(key, true);
      } else if (!shouldShow && isShown) {
        el.classList.remove("visible");
        overlayState.set(key, false);
      }
    });
  }

  function fmt(t) {
    if (!isFinite(t) || t < 0) t = 0;
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function renderTime(t) {
    timeLabel.textContent = `${fmt(t)} / ${fmt(TOTAL)}`;
    if (TOTAL > 0) {
      seek.value = String(Math.round((t / TOTAL) * 1000));
    }
  }

  function tick(t) {
    setSlide(findSlide(t));
    updateOverlays(t);
    renderTime(t);
  }

  // ---------- main loop ----------
  function loop(now) {
    if (!playing) return;
    if (useAudio) {
      currentTime = audio.currentTime;
    } else {
      const dt = ((now - lastFrame) / 1000) * speed;
      lastFrame = now;
      currentTime = Math.min(TOTAL, currentTime + dt);
      if (currentTime >= TOTAL) {
        pause();
      }
    }
    tick(currentTime);
    requestAnimationFrame(loop);
  }

  function play() {
    if (TOTAL <= 0) return;
    playing = true;
    playBtn.textContent = "⏸";
    if (useAudio) audio.play().catch(() => {});
    lastFrame = performance.now();
    requestAnimationFrame(loop);
  }

  function pause() {
    playing = false;
    playBtn.textContent = "▶";
    if (useAudio) audio.pause();
  }

  function togglePlay() {
    playing ? pause() : play();
  }

  function seekTo(fraction) {
    const t = Math.max(0, Math.min(TOTAL, fraction * TOTAL));
    currentTime = t;
    if (useAudio) audio.currentTime = t;
    tick(t);
  }

  function setSpeed(s) {
    speed = s;
    if (useAudio) audio.playbackRate = s;
  }

  // ---------- audio detection ----------
  function tryAudio() {
    return new Promise((resolve) => {
      const onLoaded = () => {
        cleanup();
        if (audio.duration > 0 && isFinite(audio.duration)) {
          useAudio = true;
        }
        resolve();
      };
      const onError = () => { cleanup(); resolve(); };
      const cleanup = () => {
        audio.removeEventListener("loadedmetadata", onLoaded);
        audio.removeEventListener("error", onError);
      };
      audio.addEventListener("loadedmetadata", onLoaded);
      audio.addEventListener("error", onError);
      // If readyState already has metadata, fire manually.
      if (audio.readyState >= 1) onLoaded();
      // Hard timeout — never block startup more than 1.5s.
      setTimeout(() => { cleanup(); resolve(); }, 1500);
    });
  }

  // ---------- wiring ----------
  function wire() {
    playBtn.addEventListener("click", togglePlay);
    seek.addEventListener("input", () => {
      seekTo(parseInt(seek.value, 10) / 1000);
    });
    speedSel.addEventListener("change", () => {
      setSpeed(parseFloat(speedSel.value));
    });
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
      if (e.code === "Space") { e.preventDefault(); togglePlay(); }
      else if (e.code === "ArrowRight") seekTo(Math.min(1, currentTime / TOTAL + 0.01));
      else if (e.code === "ArrowLeft")  seekTo(Math.max(0, currentTime / TOTAL - 0.01));
    });
  }

  // ---------- init ----------
  buildOverlayElements();
  if (SLIDES.length === 0) {
    pageInfo.textContent = "no slides";
  } else {
    setSlide(0);
  }
  renderTime(0);
  wire();
  tryAudio().then(() => {
    if (useAudio) {
      console.info("[player] audio mode (duration=" + audio.duration.toFixed(2) + "s)");
    } else {
      console.info("[player] timer mode (no audio)");
    }
  });
})();
