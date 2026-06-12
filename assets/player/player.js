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
  const ccBtn = $("cc");
  const captionsRoot = $("captions");
  const captionText = $("caption-text");

  const SLIDES = TIMELINE.slides || [];
  const OVERLAYS = TIMELINE.overlays || [];
  const MATHWRITES = TIMELINE.mathwrites || [];
  const CAPTIONS = TIMELINE.captions || [];
  const TOTAL = TIMELINE.total_duration || 0;
  const mwRoot = $("mathwrites");

  let useAudio = false;
  let currentTime = 0;
  let playing = false;
  let lastFrame = 0;
  let speed = 1;
  let currentSlideIndex = -1;
  let captionsOn = true;
  let captionShown = "";          // text currently in the caption bar
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

  // ---------- mathwrite rendering ----------
  // Each mathwrite is a formula the PNG render left blank; we hand-write it
  // here, segment by segment, in sync with the narration window of each
  // segment. Everything is a pure function of the current time t so seeking
  // lands on the correct partially-drawn state.
  const mwItems = []; // {data, box, row, segs:[{data, span, nodes|null, total}]}
  const MW_FALLBACK_BBOX = { x: 0.1, y: 0.35, w: 0.8, h: 0.3 };
  const MW_STROKE_WIDTH = 35; // in MathJax font units (~1000/em)

  function buildMathwrites() {
    if (!mwRoot) return;
    mwRoot.innerHTML = "";
    MATHWRITES.forEach((mw) => {
      const box = document.createElement("div");
      box.className = "mathwrite-box";
      box.style.display = "none";
      const row = document.createElement("div");
      row.className = "mw-row";
      box.appendChild(row);
      const segs = (mw.segs || []).map((sd, i) => {
        const span = document.createElement("span");
        span.className = "mw-seg";
        span.innerHTML = sd.svg;
        if (i !== mw.segs.length - 1) span.style.marginRight = "0.22em";
        span.style.visibility = "hidden";
        row.appendChild(span);
        return { data: sd, span, nodes: null, total: 0, lastP: -1 };
      });
      mwRoot.appendChild(box);
      mwItems.push({ data: mw, box, row, segs });
    });
  }

  function mwCollectNodes(seg) {
    const svg = seg.span.querySelector("svg");
    seg.nodes = [];
    seg.total = 0;
    if (!svg) return;
    // MathJax emits glyphs as <path data-c> (fontCache:none) or <use data-c>,
    // plus <rect> for fraction bars / rules.
    let els = Array.from(svg.querySelectorAll("path[data-c], use[data-c], rect"));
    if (!els.length) els = Array.from(svg.querySelectorAll("use, path"));
    els.forEach((el) => {
      let len = 0;
      const nd = { el, len: 0, acc: 0, w0: 0 };
      if (el.tagName === "rect") {
        nd.w0 = parseFloat(el.getAttribute("width")) || 0;
        const h = parseFloat(el.getAttribute("height")) || 0;
        len = 2 * (nd.w0 + h);
      } else {
        let target = el;
        if (el.tagName === "use") {
          const href = el.getAttribute("href") || el.getAttribute("xlink:href") || "";
          if (href.charAt(0) === "#") target = document.getElementById(href.slice(1));
        }
        try {
          if (target && target.getTotalLength) len = target.getTotalLength();
        } catch (_e) { /* defs not measurable — fall through to default */ }
      }
      if (!isFinite(len) || len <= 0) len = 600;
      nd.len = len;
      nd.acc = seg.total;
      seg.total += len;
      seg.nodes.push(nd);
    });
  }

  function mwPaintNode(nd, q) {
    const st = nd.el.style;
    if (nd.el.tagName === "rect") {
      nd.el.setAttribute("width", String(Math.max(0, q) * nd.w0));
      return;
    }
    if (q >= 1) {
      st.fill = ""; st.stroke = ""; st.strokeDasharray = ""; st.strokeWidth = "";
    } else if (q <= 0) {
      st.fill = "none"; st.stroke = "none";
    } else {
      st.fill = "none";
      st.stroke = "currentColor";
      st.strokeWidth = String(MW_STROKE_WIDTH);
      st.strokeDasharray = (q * nd.len) + " " + (nd.len + 10);
    }
  }

  function mwPaintSeg(seg, p) {
    if (p === seg.lastP) return;
    if (p <= 0 && seg.lastP === 0) return;
    if (p >= 1 && seg.lastP === 1) return;
    p = Math.max(0, Math.min(1, p));
    seg.lastP = p;
    if (p <= 0) { seg.span.style.visibility = "hidden"; return; }
    seg.span.style.visibility = "";
    if (!seg.nodes) mwCollectNodes(seg);
    const target = p * seg.total;
    seg.nodes.forEach((nd) => {
      const q = nd.len ? (target - nd.acc) / nd.len : 1;
      mwPaintNode(nd, Math.max(0, Math.min(1, q)));
    });
  }

  function layoutMathwrites() {
    if (!mwItems.length) return;
    const s = SLIDES[currentSlideIndex];
    if (!s) return;
    const stageR = stage.getBoundingClientRect();
    const imgR = slide.getBoundingClientRect();
    if (imgR.width === 0 || imgR.height === 0) return;
    mwItems.forEach((item) => {
      if (item.data.slide !== s.index) {
        item.box.style.display = "none";
        return;
      }
      item.box.style.display = "";
      const bb = item.data.bbox || MW_FALLBACK_BBOX;
      const w = bb.w * imgR.width;
      const h = bb.h * imgR.height;
      item.box.style.left = (imgR.left - stageR.left + bb.x * imgR.width) + "px";
      item.box.style.top = (imgR.top - stageR.top + bb.y * imgR.height) + "px";
      item.box.style.width = w + "px";
      item.box.style.height = h + "px";
      // Natural row size is unaffected by the transform, so this is stable.
      const rw = item.row.offsetWidth || 1;
      const rh = item.row.offsetHeight || 1;
      const k = Math.min(w / rw, h / rh);
      const dx = (w - rw * k) / 2;
      const dy = (h - rh * k) / 2;
      item.row.style.transform = "translate(" + dx + "px, " + dy + "px) scale(" + k + ")";
    });
  }

  function updateMathwrites(t) {
    if (!mwItems.length) return;
    const s = SLIDES[currentSlideIndex];
    if (!s) return;
    mwItems.forEach((item) => {
      if (item.data.slide !== s.index) return;
      item.segs.forEach((seg) => {
        const d = seg.data.end - seg.data.start;
        const p = d > 0 ? (t - seg.data.start) / d : (t >= seg.data.start ? 1 : 0);
        mwPaintSeg(seg, p);
      });
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
    layoutMathwrites();
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

  // ---------- captions ----------
  function findCaption(t) {
    // CAPTIONS are in ascending start order; a short linear scan is plenty.
    for (let i = 0; i < CAPTIONS.length; i++) {
      const c = CAPTIONS[i];
      if (t >= c.start && t < c.end) return c.text;
    }
    return "";
  }

  function updateCaptions(t) {
    const text = captionsOn ? findCaption(t) : "";
    if (text === captionShown) return;
    captionShown = text;
    captionText.textContent = text;
  }

  function setCaptions(on) {
    captionsOn = on;
    captionsRoot.classList.toggle("off", !on);
    ccBtn.classList.toggle("on", on);
    ccBtn.setAttribute("aria-pressed", String(on));
    captionShown = "__force__";   // force the next updateCaptions to repaint
    updateCaptions(currentTime);
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
    updateMathwrites(t);
    updateCaptions(t);
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

  // ---------- deterministic export hook ----------
  // Renders exactly one frame at absolute time t for offline MP4 capture
  // (scripts/export_mp4.mjs). Everything the player draws is a pure function of
  // t, so a headless browser can step frame-by-frame and screenshot without
  // playing in real time. Returns a promise that resolves once the frame is
  // visually settled (slide image decoded, fonts ready, stroke styles applied).
  function exportRenderAt(t) {
    if (playing) pause();
    t = Math.max(0, Math.min(TOTAL, t));
    currentTime = t;
    const prevIdx = currentSlideIndex;
    setSlide(findSlide(t));
    const needDecode = (currentSlideIndex !== prevIdx || !slide.complete) && slide.decode;
    const decoded = needDecode ? slide.decode().catch(() => {}) : Promise.resolve();
    return decoded
      .then(() => {
        layoutMathwrites();
        updateOverlays(t);
        updateMathwrites(t);
        updateCaptions(t);
        renderTime(t);
        return (document.fonts && document.fonts.ready) || Promise.resolve();
      })
      .then(() => new Promise((resolve) => {
        // Two rAFs let stroke-dasharray styles and the mathwrite transform
        // settle; the timeout guards against headless rAF throttling.
        let done = false;
        const finish = () => { if (!done) { done = true; resolve(); } };
        requestAnimationFrame(() => requestAnimationFrame(finish));
        setTimeout(finish, 200);
      }));
  }

  // Strip the player chrome so the stage fills the viewport for a clean capture.
  function exportPrepare() {
    const controls = $("controls");
    if (controls) controls.style.display = "none";
    document.documentElement.style.background = "#000";
    document.body.style.background = "#000";
    return TOTAL;
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
    if (ccBtn) ccBtn.addEventListener("click", () => setCaptions(!captionsOn));
    seek.addEventListener("input", () => {
      seekTo(parseInt(seek.value, 10) / 1000);
    });
    speedSel.addEventListener("change", () => {
      setSpeed(parseFloat(speedSel.value));
    });
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
      if (e.code === "Space") { e.preventDefault(); togglePlay(); }
      else if (e.code === "KeyC") setCaptions(!captionsOn);
      else if (e.code === "ArrowRight") seekTo(Math.min(1, currentTime / TOTAL + 0.01));
      else if (e.code === "ArrowLeft")  seekTo(Math.max(0, currentTime / TOTAL - 0.01));
    });
    window.addEventListener("resize", layoutMathwrites);
    slide.addEventListener("load", layoutMathwrites);
  }

  // ---------- init ----------
  buildOverlayElements();
  buildMathwrites();
  if (SLIDES.length === 0) {
    pageInfo.textContent = "no slides";
  } else {
    setSlide(0);
  }
  if (ccBtn && CAPTIONS.length === 0) ccBtn.style.display = "none";
  setCaptions(captionsOn);  // sync button state + paint the t=0 caption
  renderTime(0);
  wire();
  // Export entry point for the headless capture driver (scripts/export_mp4.mjs).
  window.__lectureExport = {
    total: TOTAL,
    slides: SLIDES.length,
    prepare: exportPrepare,
    renderAt: exportRenderAt,
  };
  // Deep-link: index.html#t=42.5 starts paused at that time.
  const hashT = /[#&]t=([\d.]+)/.exec(location.hash);
  if (hashT && TOTAL > 0) seekTo(parseFloat(hashT[1]) / TOTAL);
  tryAudio().then(() => {
    if (useAudio) {
      console.info("[player] audio mode (duration=" + audio.duration.toFixed(2) + "s)");
    } else {
      console.info("[player] timer mode (no audio)");
    }
  });
})();
