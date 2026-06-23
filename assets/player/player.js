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
  const overlayEls = new Map();   // overlayId -> badge HTMLElement
  const overlayRevealEls = new Map(); // overlayId -> in-place content reveal element (bbox overlays only)

  // ---------- overlay rendering ----------
  function buildOverlayElements() {
    overlaysRoot.innerHTML = "";
    overlayEls.clear();
    overlayRevealEls.clear();
    OVERLAYS.forEach((ov) => {
      const key = ov.id + "@" + ov.slide;
      const el = document.createElement("div");
      el.className = "overlay";
      el.dataset.id = ov.id;
      el.dataset.start = ov.start;
      el.dataset.end = ov.end;
      el.dataset.slide = ov.slide;
      el.textContent = ov.label || ov.id;
      el.style.display = "none";
      overlaysRoot.appendChild(el);
      overlayEls.set(key, el);
      overlayState.set(key, false);

      // In-place content reveal: compile_marp.sh blanked this overlay's region in
      // the slide PNG, so here we fade its cropped content (from NN.reveal.png) back
      // in — in place — at the overlay's window. Only overlays with a measured bbox
      // get a reveal layer; otherwise the badge alone marks them.
      if (ov.bbox) {
        const rv = document.createElement("div");
        rv.className = "overlay-reveal";
        rv.dataset.slide = ov.slide;
        rv._bbox = ov.bbox;
        rv.style.display = "none";
        overlaysRoot.appendChild(rv);
        overlayRevealEls.set(key, rv);
      }
    });
  }

  // ---------- mathwrite rendering ----------
  // Each mathwrite is a formula the PNG render left blank; we hand-write it
  // here, segment by segment, in sync with the narration window of each
  // segment. Everything is a pure function of the current time t so seeking
  // lands on the correct partially-drawn state.
  const mwItems = []; // {data, box, row, segs:[{data, span, nodes, total, lastP}]}
  const MW_FALLBACK_BBOX = { x: 0.1, y: 0.35, w: 0.8, h: 0.3 };
  const MW_NIB = { x: 0.16, y: 0.85 };  // where the nib tip sits inside the pen art
  // Single-stroke ("handwriting") rendering: each MathJax glyph outline is replaced
  // by its Hershey single-stroke centerline (window.HERSHEY_FONT) and drawn by
  // sweeping stroke-dashoffset along that centerline — a real pen trajectory, not an
  // outline trace or a fade. The pen width is a constant in MathJax font units
  // (~1000/em) so the ink reads as one uniform pen across the whole formula.
  const MW_PEN_EM = 150;
  const MW_SKIP_CP = new Set([0x2061, 0x2062, 0x2063, 0x2064]); // invisible operators
  let mwPen = null;            // the single moving pen-nib element (rides the frontier)

  function buildMathwrites() {
    if (!mwRoot) return;
    mwRoot.innerHTML = "";
    mwItems.length = 0;
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
    // One shared pen that rides the writing frontier — sells the "a hand is writing
    // this" feel rather than content just appearing.
    mwPen = document.createElement("div");
    mwPen.className = "mw-pen";
    mwPen.style.display = "none";
    mwPen.innerHTML =
      '<svg viewBox="0 0 64 64" width="100%" height="100%" aria-hidden="true">' +
      '<polygon points="9,55 17,41 27,49" fill="#3a3a3a"/>' +          // nib
      '<polygon points="11,53 16,45 20,49" fill="#0f0f0f"/>' +         // nib tip
      '<rect x="18" y="9" width="17" height="34" rx="5" transform="rotate(40 26 26)" fill="#2b6cff"/>' +
      '<rect x="18" y="9" width="17" height="9" rx="4" transform="rotate(40 26 13)" fill="#173a9e"/>' +
      '</svg>';
    mwRoot.appendChild(mwPen);
  }

  // --- Hershey single-stroke conversion ---------------------------------------
  // Map a glyph's MathJax codepoint to a HERSHEY_FONT key: math-alphanumeric
  // letters/digits fold to ASCII, Greek (plain + math-italic) to "g:<slot>", a few
  // math symbols to hand-authored "s:<name>" strokes; plain ASCII passes through.
  const MW_GSEQ = "abgdezhqiklmncoprsstufxyw";        // α..ω (final-sigma at 17)
  const MW_SYM = { 0x222B: "s:int", 0x2211: "s:sum", 0x221A: "s:surd",
                   0x2032: "s:prime", 0x2212: "-", 0x2217: "*", 0x00D7: "x" };
  function mwCodeToKey(cp) {
    const L = [[0x1D400,65],[0x1D41A,97],[0x1D434,65],[0x1D44E,97],[0x1D468,65],[0x1D482,97],
      [0x1D49C,65],[0x1D4B6,97],[0x1D504,65],[0x1D51E,97],[0x1D538,65],[0x1D552,97],
      [0x1D5A0,65],[0x1D5BA,97],[0x1D5D4,65],[0x1D5EE,97],[0x1D608,65],[0x1D622,97],
      [0x1D670,65],[0x1D68A,97]];
    for (const [b, a] of L) { if (cp >= b && cp < b + 26) return String.fromCharCode(a + (cp - b)); }
    for (const b of [0x1D7CE,0x1D7D8,0x1D7E2,0x1D7EC,0x1D7F6]) { if (cp >= b && cp < b + 10) return String.fromCharCode(48 + (cp - b)); }
    if (cp === 0x210E) return "h";
    if (cp >= 0x3B1 && cp <= 0x3C9) return "g:" + MW_GSEQ[cp - 0x3B1];
    if (cp >= 0x1D6FC && cp <= 0x1D714) return "g:" + MW_GSEQ[cp - 0x1D6FC];
    if (cp in MW_SYM) return MW_SYM[cp];
    if (cp >= 32 && cp < 127) return String.fromCharCode(cp);
    return null;
  }
  function mwHersheyStrokes(cp) {
    const F = window.HERSHEY_FONT; if (!F) return null;
    const k = mwCodeToKey(cp); return k ? (F[k] || null) : null;
  }
  function mwStrokesBBox(strokes) {
    let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
    for (const s of strokes) for (const p of s) {
      if (p[0] < a) a = p[0]; if (p[1] < b) b = p[1];
      if (p[0] > c) c = p[0]; if (p[1] > d) d = p[1];
    }
    return { x: a, y: b, w: Math.max(1e-3, c - a), h: Math.max(1e-3, d - b) };
  }
  // Build an array of SVG path 'd' strings placing the Hershey strokes into this
  // glyph's own box. Each Hershey stroke becomes its own single-subpath string.
  // Hershey is y-DOWN while MathJax glyph-local coords are y-UP, so y is flipped.
  function mwHersheyPaths(el, strokes) {
    let B; try { B = el.getBBox(); } catch (_e) { return null; }
    if (!B || B.width <= 0 || B.height <= 0) return null;
    const H = mwStrokesBBox(strokes);
    const sx = B.width / H.w, sy = B.height / H.h;
    const paths = [];
    for (const s of strokes) {
      let d = "M";
      for (let j = 0; j < s.length; j++) {
        const X = B.x + (s[j][0] - H.x) * sx;
        const Y = B.y + B.height - (s[j][1] - H.y) * sy;
        d += (j ? "L" : "") + X.toFixed(1) + " " + Y.toFixed(1) + " ";
      }
      paths.push(d.trim());
    }
    return paths.length ? paths : null;
  }

  // Geometric bbox of an element in its svg's viewBox space — resolves every nested
  // <g transform> (including MathJax's scale(1,-1)) by mapping getBBox()'s local box
  // through svgCTM⁻¹·elCTM. Needs layout (mwCollectNodes guarantees it). Returns null
  // if unavailable, so the caller can fall back to DOM order rather than mix spaces.
  function mwViewBoxBBox(svg, el) {
    let B; try { B = el.getBBox(); } catch (_e) { return null; }
    if (!B) return null;
    try {
      const sctm = svg.getScreenCTM(), ectm = el.getScreenCTM();
      if (!sctm || !ectm) return null;
      const m = sctm.inverse().multiply(ectm);
      const p = svg.createSVGPoint();
      let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
      const cs = [[B.x, B.y], [B.x + B.width, B.y], [B.x, B.y + B.height], [B.x + B.width, B.y + B.height]];
      for (const c of cs) {
        p.x = c[0]; p.y = c[1];
        const q = p.matrixTransform(m);
        if (q.x < x0) x0 = q.x; if (q.x > x1) x1 = q.x;
        if (q.y < y0) y0 = q.y; if (q.y > y1) y1 = q.y;
      }
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    } catch (_e) { return null; }
  }

  // Reorder a segment's glyph/rule elements into natural handwriting order. MathJax
  // emits them in *logical* (DOM) order, which for stacked structures does not match
  // how a hand writes them — most visibly a fraction's bar comes after BOTH the
  // numerator and denominator, so it appears where the pen has already passed. We
  // sort geometrically: left→right by column, and within a column (numerator / bar /
  // denominator, or an operator's limits) top→bottom — so the bar lands between its
  // numerator and denominator. Same-baseline glyphs share a y and a stable sort keeps
  // their left→right order, so plain runs are unaffected. Any geometry failure falls
  // back to DOM order.
  function mwReadingOrder(svg, els) {
    const items = [];
    for (const el of els) {
      const g = mwViewBoxBBox(svg, el);
      if (!g) return els;
      items.push({ el, g });
    }
    items.sort((a, b) => a.g.x - b.g.x || a.g.y - b.g.y);
    const out = [];
    let i = 0;
    while (i < items.length) {
      let lo = items[i].g.x, hi = items[i].g.x + items[i].g.w;
      const col = [items[i]];
      let j = i + 1;
      while (j < items.length) {
        const g = items[j].g;
        const ov = Math.min(hi, g.x + g.w) - Math.max(lo, g.x);
        if (ov <= 0.5 * Math.min(hi - lo, g.w)) break;   // not stacked → next column
        col.push(items[j]);
        lo = Math.min(lo, g.x); hi = Math.max(hi, g.x + g.w);
        j++;
      }
      col.sort((a, b) => a.g.y - b.g.y);                 // top → bottom within a column
      for (const it of col) out.push(it.el);
      i = j;
    }
    return out;
  }

  // Collect a segment's drawable pieces as pen "nodes", in reading order. A glyph
  // mapping to Hershey strokes expands into one "stroke" node per Hershey stroke —
  // its outline replaced by Hershey single-stroke centerlines, revealed strictly in
  // sequence by sweeping stroke-dashoffset (a true pen trajectory). A glyph with
  // no Hershey mapping degrades to a "fade" node (clean opacity ramp, never an
  // outline trace). Fraction rules are "rect" nodes grown left→right.
  function mwCollectNodes(seg) {
    const svg = seg.span.querySelector("svg");
    if (!svg) { seg.nodes = []; seg.total = 0; return; }
    // Measuring each glyph box needs the seg laid out. If the mathwrite box is still
    // collapsed (slide image not yet sized → box display:none), defer — caching now
    // would lock in an all-fallback (no single-stroke) state. Leaving seg.nodes null
    // makes the next frame / slide-load re-collect once layout is ready.
    if (seg.span.getBoundingClientRect().width === 0) return;
    seg.nodes = [];
    seg.total = 0;
    let els = Array.from(svg.querySelectorAll("path[data-c], rect"));
    if (!els.length) els = Array.from(svg.querySelectorAll("path, rect"));
    els = mwReadingOrder(svg, els);   // logical (DOM) order → geometric handwriting order
    els.forEach((el) => {
      const isRect = el.tagName === "rect";
      if (isRect) {
        const nd = { el, svg, isRect, kind: "rect", len: 1, acc: 0 };
        nd.x0 = parseFloat(el.getAttribute("x")) || 0;
        nd.y0 = parseFloat(el.getAttribute("y")) || 0;
        nd.w0 = parseFloat(el.getAttribute("width")) || 0;
        nd.h0 = parseFloat(el.getAttribute("height")) || 0;
        nd.len = Math.max(1, nd.w0 + nd.h0);
        el.setAttribute("width", "0");
        el.style.fillOpacity = "0";
        nd.acc = seg.total;
        seg.total += nd.len;
        seg.nodes.push(nd);
      } else {
        const cp = parseInt(el.getAttribute("data-c") || "0", 16);
        if (MW_SKIP_CP.has(cp)) return;        // invisible operator: draw nothing
        const strokes = mwHersheyStrokes(cp);
        const paths = strokes ? mwHersheyPaths(el, strokes) : null;
        if (paths) {
          let insertAfter = el;
          paths.forEach((d, i) => {
            const pathEl = i === 0 ? el : el.cloneNode(false);
            if (i > 0) {
              insertAfter.parentNode.insertBefore(pathEl, insertAfter.nextSibling);
              insertAfter = pathEl;
            }
            pathEl.setAttribute("d", d);
            pathEl.removeAttribute("mask");
            pathEl.style.fill = "none";
            pathEl.style.stroke = "currentColor";
            pathEl.style.strokeWidth = String(MW_PEN_EM);
            pathEl.style.strokeLinecap = "round";
            pathEl.style.strokeLinejoin = "round";
            const nd = { el: pathEl, svg, isRect: false, kind: "stroke", len: 1, acc: 0 };
            try { nd.len = pathEl.getTotalLength() || 1; } catch (_e) { nd.len = 1; }
            if (!isFinite(nd.len) || nd.len <= 0) nd.len = 1;
            pathEl.style.strokeDasharray = nd.len + " " + nd.len;
            pathEl.style.strokeDashoffset = String(nd.len);
            pathEl.style.visibility = "hidden"; // round caps require the visibility gate to hide the dot
            nd.acc = seg.total;
            seg.total += nd.len;
            seg.nodes.push(nd);
          });
        } else {
          const nd = { el, svg, isRect: false, kind: "fade", len: 1, acc: 0 };
          el.style.fillOpacity = "0";                    // fallback: clean fade-in
          let p = 300; try { const b = el.getBBox(); p = Math.max(60, b.width + b.height); } catch (_e) {}
          nd.len = p;
          nd.acc = seg.total;
          seg.total += nd.len;
          seg.nodes.push(nd);
        }
      }
    });
  }

  // Reveal one node to local progress q∈[0,1].
  function mwPaintNode(nd, q) {
    q = Math.max(0, Math.min(1, q));
    if (nd.kind === "rect") {                  // fraction bar / rule: one pen stroke
      nd.el.setAttribute("width", String(q * nd.w0));
      nd.el.style.fillOpacity = q > 0 ? "1" : "0";
      return;
    }
    if (nd.kind === "stroke") {                // sweep the pen along the centerline
      nd.el.style.visibility = q <= 0 ? "hidden" : "";
      nd.el.style.strokeDashoffset = String(nd.len * (1 - q));
      return;
    }
    nd.el.style.fillOpacity = q <= 0 ? "0" : (q >= 1 ? "1" : (q * q).toFixed(3));  // fade fallback
  }

  // Paint a segment to progress p; return {nd,q} for the node currently under the
  // pen (0<q<1), or null when the segment is unstarted / fully written.
  function mwPaintSeg(seg, p) {
    if (p <= 0) {
      if (seg.lastP !== 0) { seg.span.style.visibility = "hidden"; seg.lastP = 0; }
      return null;
    }
    if (p >= 1 && seg.lastP >= 1) return null;   // already fully written
    // Collect (and hide every glyph) BEFORE revealing the span. If collection
    // defers (box not laid out yet at slide entry), the span stays hidden so the
    // raw fully-inked MathJax SVG never flashes ahead of the pen.
    if (!seg.nodes) { mwCollectNodes(seg); if (!seg.nodes) return null; }
    seg.span.style.visibility = "";
    p = Math.min(1, p);
    seg.lastP = p;
    const target = p * seg.total;
    let active = null, lastPos = null;
    seg.nodes.forEach((nd) => {
      const q = nd.len ? (target - nd.acc) / nd.len : 1;
      mwPaintNode(nd, q);
      if (q > 0) { lastPos = { nd, q: Math.min(q, 1) }; if (q < 1) active = { nd, q }; }
    });
    // Pen rides the glyph being drawn; between glyphs it rests at the end of the
    // last one so it never blinks out mid-segment. When the segment is fully
    // written it releases (null) so the pen moves on to the next segment.
    if (active) return active;
    if (p < 1 && lastPos) return lastPos;
    return null;
  }

  // Screen-space point of the pen nib — the actual frontier of the stroke, not just
  // a left→right sweep of the glyph box. For a glyph we read the true point at length
  // q·len on its outline (`getPointAtLength`); for a rule we take its growing right
  // edge. That point is in the path's local user space, so we map it through
  // svgCTM⁻¹·elCTM into the svg's viewBox space — the two CTMs share the same ancestor
  // chain (the row's CSS translate/scale included), so whatever either omits cancels —
  // then linearly into the svg's real on-screen box (which does reflect that scale).
  function mwPenPoint(nd, q) {
    q = Math.max(0, Math.min(1, q));
    let ux, uy;
    if (nd.isRect) {
      ux = nd.x0 + q * nd.w0;
      uy = nd.y0 + nd.h0 / 2;
    } else {
      try { const p = nd.el.getPointAtLength(q * nd.len); ux = p.x; uy = p.y; }
      catch (_e) { return mwPenPointFallback(nd, q); }
    }
    try {
      const svg = nd.svg;
      const svgCTM = svg && svg.getScreenCTM();
      const elCTM = nd.el.getScreenCTM();
      const r = svg && svg.getBoundingClientRect();
      const vb = svg && svg.viewBox && svg.viewBox.baseVal;
      if (svgCTM && elCTM && r && r.width && vb && vb.width) {
        let p = svg.createSVGPoint();
        p.x = ux; p.y = uy;
        p = p.matrixTransform(svgCTM.inverse().multiply(elCTM));  // local → viewBox space
        return {
          x: r.left + (p.x - vb.x) * r.width / vb.width,
          y: r.top + (p.y - vb.y) * r.height / vb.height,
        };
      }
    } catch (_e) {}
    return mwPenPointFallback(nd, q);
  }

  // Last-resort nib position when the CTM/viewBox path is unavailable: ride the
  // element's client box (the pre-rewrite behaviour).
  function mwPenPointFallback(nd, q) {
    const r = nd.el.getBoundingClientRect();
    if (!(r.width || r.height)) return null;
    return { x: r.left + Math.max(0, Math.min(1, q)) * r.width, y: r.top + r.height * 0.5 };
  }

  function mwUpdatePen(frontier) {
    if (!mwPen) return;
    const pt = frontier ? mwPenPoint(frontier.nd, frontier.q) : null;
    if (!pt) { mwPen.style.display = "none"; return; }
    const stageR = stage.getBoundingClientRect();
    const imgR = slide.getBoundingClientRect();
    const size = Math.max(20, 0.07 * (imgR.height || stageR.height));
    mwPen.style.width = size + "px";
    mwPen.style.height = size + "px";
    mwPen.style.left = (pt.x - stageR.left - size * MW_NIB.x) + "px";
    mwPen.style.top = (pt.y - stageR.top - size * MW_NIB.y) + "px";
    mwPen.style.display = "";
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
      // Natural row size is unaffected by the transform, so this is stable. Fit each
      // block inside its own measured box (the safe min of the width and height
      // ratios) so a tall fraction/∫ can never spill into the line or overlay below.
      const rw = item.row.offsetWidth || 1;
      const rh = item.row.offsetHeight || 1;
      const k = Math.min(w / rw, h / rh);
      const dx = (w - rw * k) / 2;
      const dy = (h - rh * k) / 2;
      item.row.style.transform = "translate(" + dx + "px, " + dy + "px) scale(" + k + ")";
    });
  }

  // Position each overlay reveal over its slide region and set the cropped
  // background (the reveal PNG, shifted so only this overlay's box shows). The
  // crop comes from NN.reveal.png — the same render as the base PNG but with the
  // overlay visible — so it lines up pixel-for-pixel with the blanked base.
  function layoutOverlayReveals() {
    if (!overlayRevealEls.size) return;
    const s = SLIDES[currentSlideIndex];
    if (!s) return;
    const stageR = stage.getBoundingClientRect();
    const imgR = slide.getBoundingClientRect();
    if (imgR.width === 0 || imgR.height === 0) return;
    const src = (s.image || "").replace(/\.png$/i, ".reveal.png");
    const W = imgR.width, H = imgR.height;
    overlayRevealEls.forEach((rv) => {
      if (parseInt(rv.dataset.slide, 10) !== s.index) { rv.style.display = "none"; return; }
      const bb = rv._bbox;
      if (!bb) { rv.style.display = "none"; return; }
      rv.style.display = "";
      rv.style.left = (imgR.left - stageR.left + bb.x * W) + "px";
      rv.style.top = (imgR.top - stageR.top + bb.y * H) + "px";
      rv.style.width = (bb.w * W) + "px";
      rv.style.height = (bb.h * H) + "px";
      rv.style.backgroundImage = 'url("' + src + '")';
      rv.style.backgroundSize = W + "px " + H + "px";
      rv.style.backgroundPosition = (-bb.x * W) + "px " + (-bb.y * H) + "px";
    });
  }

  // Re-layout both time-driven layers (mathwrite + overlay reveals) after a slide
  // change or resize.
  function relayout() {
    layoutMathwrites();
    layoutOverlayReveals();
  }

  function updateMathwrites(t) {
    if (!mwItems.length) return;
    const s = SLIDES[currentSlideIndex];
    if (!s) { if (mwPen) mwPen.style.display = "none"; return; }
    let frontier = null;
    mwItems.forEach((item) => {
      if (item.data.slide !== s.index) return;
      item.segs.forEach((seg) => {
        const d = seg.data.end - seg.data.start;
        const p = d > 0 ? (t - seg.data.start) / d : (t >= seg.data.start ? 1 : 0);
        const active = mwPaintSeg(seg, p);
        if (active) frontier = active;   // segments are sequential, so the last wins
      });
    });
    mwUpdatePen(frontier);
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
    // Reveal layers follow their slide too (cleared off-slide so a stale crop never lingers).
    overlayRevealEls.forEach((rv) => {
      if (parseInt(rv.dataset.slide, 10) !== s.index) {
        rv.style.display = "none";
        rv.classList.remove("visible");
        rv._shown = false;
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
    relayout();
  }

  function updateOverlays(t) {
    const s = SLIDES[currentSlideIndex];
    if (!s) return;
    overlayEls.forEach((el, key) => {
      if (parseInt(el.dataset.slide, 10) !== s.index) return;
      const start = parseFloat(el.dataset.start);
      const end = parseFloat(el.dataset.end);
      // Badge highlight: visible only during [start, end).
      const shouldShow = t >= start && t < end;
      const isShown = overlayState.get(key);
      if (shouldShow && !isShown) {
        el.classList.add("visible");
        overlayState.set(key, true);
      } else if (!shouldShow && isShown) {
        el.classList.remove("visible");
        overlayState.set(key, false);
      }
      // Revealed content was merely delayed: once it appears at `start` it stays
      // (it is part of the slide) until the slide changes — it must not un-write
      // itself when the narration moves on past `end`.
      const rv = overlayRevealEls.get(key);
      if (rv) {
        const reveal = t >= start;
        if (reveal && rv._shown !== true) { rv.classList.add("visible"); rv._shown = true; }
        else if (!reveal && rv._shown !== false) { rv.classList.remove("visible"); rv._shown = false; }
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
        relayout();
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
    // Reposition the time-driven layers AND refresh the pen at the current time:
    // when the slide image finishes loading (or on resize) the boxes move, so the
    // pen must be re-placed too — relayout alone would leave a stale/hidden nib.
    const onLayoutChange = () => { relayout(); updateMathwrites(currentTime); };
    window.addEventListener("resize", onLayoutChange);
    slide.addEventListener("load", onLayoutChange);
  }

  // ---------- init ----------
  buildOverlayElements();
  buildMathwrites();
  // Preload overlay reveal crops so they are decoded before their (often brief)
  // window — important for the deterministic, frame-stepped MP4 export.
  const _revealPreload = [];
  overlayRevealEls.forEach((rv) => {
    const s = SLIDES.find((x) => x.index === parseInt(rv.dataset.slide, 10));
    if (s && s.image) {
      const im = new Image();
      im.src = s.image.replace(/\.png$/i, ".reveal.png");
      _revealPreload.push(im);
    }
  });
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
