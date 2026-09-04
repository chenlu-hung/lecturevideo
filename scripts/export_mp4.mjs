#!/usr/bin/env node
// Export a built lecture player to MP4 by stepping it frame-by-frame in a
// headless browser and piping screenshots into ffmpeg.
//
// Usage:
//   node scripts/export_mp4.mjs <topic_dir> [options]
//
// Reads:
//   <topic_dir>/video/index.html   — the player built by build_video.py
//   <topic_dir>/video/narration.{mp3,wav} — muxed in as audio when present (voiced path)
//
// Writes:
//   <topic_dir>/video/lecture.mp4  — H.264 (yuv420p) + AAC, or video-only when silent
//
// Why this works: the player draws every slide/overlay/caption/mathwrite frame as
// a pure function of the current time t (window.__lectureExport.renderAt), so we
// can render frame i at t = i/fps deterministically — no real-time playback, exact
// hand-written-math half-stroke states. Drives Chrome over the DevTools Protocol
// using Node's built-in WebSocket (no npm dependency); needs only Chrome + ffmpeg.
//
// Where the time goes (measured on an 8-core M1, 1920x1080): capturing a frame
// costs ~85ms (PNG) while the renderAt CDP round-trip is ~0.5ms and libx264
// keeps up at ~145fps — so screenshotting is ~99% of the wall clock, and one
// Chrome saturates only one core. Two consequences shape this script:
//   * The frame range is split across N Chrome instances, each encoding its own
//     chunk .mp4; the chunks are concatenated (stream copy) and the narration
//     muxed in at the end. 6 workers measured ~4.7x one worker on that M1.
//   * Capture is JPEG by default (~1.25x faster than PNG, and the frames are
//     re-encoded lossily by libx264 anyway). --png restores lossless capture.
// On top of that, it only screenshots frames that actually change: every
// mathwrite (hand-written math) frame is rendered per-frame (the sole true f(t)
// animation), while static stretches reuse one cached screenshot.
//
// Options:
//   --fps <n>        frames per second (default 30)
//   --width <px>     capture width  (default: auto from the slide aspect ratio)
//   --height <px>    capture height (default: auto, slide height capped at 1080)
//   --crf <n>        libx264 quality, lower = better (default 18)
//   --preset <name>  libx264 preset (default medium)
//   --workers <n>    parallel Chrome instances (default: min(6, cores - 2))
//   --jpeg-quality <n>  capture quality 1-100 (default 92)
//   --png            capture lossless PNG instead of JPEG (slower)
//   --out <path>     output file (default <topic_dir>/video/lecture.mp4)
//   --chrome <path>  Chrome/Chromium binary (default: auto-detect / $CHROME_PATH)
//   --ffmpeg <path>  ffmpeg binary (default: ffmpeg on PATH)
//   --keep-frames    also write captured frames to <topic_dir>/video/.frames/

import { spawn } from "node:child_process";
import { existsSync, readdirSync, openSync, readSync, closeSync, readFileSync, rmSync } from "node:fs";
import { mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

// ---------- arg parsing ----------
const DEFAULT_WORKERS = Math.max(1, Math.min(6, (os.availableParallelism?.() ?? os.cpus().length) - 2));

function parseArgs(argv) {
  const opts = {
    fps: 30, width: null, height: null, crf: 18, preset: "medium",
    workers: DEFAULT_WORKERS, jpegQuality: 92, png: false,
    out: null, chrome: null, ffmpeg: "ffmpeg", keepFrames: false, topic: null,
  };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case "--fps": opts.fps = parseFloat(next()); break;
      case "--width": opts.width = parseInt(next(), 10); break;
      case "--height": opts.height = parseInt(next(), 10); break;
      case "--crf": opts.crf = parseInt(next(), 10); break;
      case "--preset": opts.preset = next(); break;
      case "--workers": opts.workers = parseInt(next(), 10); break;
      case "--jpeg-quality": opts.jpegQuality = parseInt(next(), 10); break;
      case "--png": opts.png = true; break;
      case "--out": opts.out = next(); break;
      case "--chrome": opts.chrome = next(); break;
      case "--ffmpeg": opts.ffmpeg = next(); break;
      case "--keep-frames": opts.keepFrames = true; break;
      case "-h": case "--help": opts.help = true; break;
      default:
        if (a.startsWith("--")) { console.error(`unknown option: ${a}`); process.exit(2); }
        rest.push(a);
    }
  }
  opts.topic = rest[0] || null;
  return opts;
}

const USAGE =
  "Usage: node scripts/export_mp4.mjs <topic_dir> " +
  "[--fps 30] [--width PX] [--height PX] [--crf 18] [--preset medium] " +
  `[--workers ${DEFAULT_WORKERS}] [--jpeg-quality 92] [--png] ` +
  "[--out FILE] [--chrome PATH] [--ffmpeg PATH] [--keep-frames]\n" +
  "  default size: auto from slide aspect ratio (height capped at 1080)";

// ---------- Chrome detection (mirrors render_mathwrite.find_chrome) ----------
function findChrome(explicit) {
  if (explicit) return existsSync(explicit) ? explicit : null;
  const env = process.env.CHROME_PATH;
  if (env && existsSync(env)) return env;
  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  return null;
}

// ---------- slide aspect → capture size ----------
const DEFAULT_MAX_HEIGHT = 1080; // cap auto height for sane frame sizes
const even = (n) => Math.max(2, Math.round(n / 2) * 2);

function readPngSize(file) {
  // PNG IHDR: width/height are big-endian uint32 at byte offsets 16 and 20.
  const fd = openSync(file, "r");
  try {
    const b = Buffer.alloc(24);
    readSync(fd, b, 0, 24, 0);
    return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
  } finally { closeSync(fd); }
}

// Resolve capture WxH: honor explicit flags; otherwise match the first slide's
// aspect ratio (height capped at 1080) so 4:3 or 16:9 decks never get letterboxed.
function resolveSize(slidesDir, optW, optH) {
  let ar = 4 / 3;
  try {
    const pngs = readdirSync(slidesDir).filter((f) => f.endsWith(".png")).sort();
    if (pngs.length) { const { w, h } = readPngSize(path.join(slidesDir, pngs[0])); ar = w / h; }
  } catch { /* fall back to 4:3 */ }
  let width = optW, height = optH;
  if (width && !height) height = even(width / ar);
  else if (height && !width) width = even(height * ar);
  else if (!width && !height) { height = DEFAULT_MAX_HEIGHT; width = even(height * ar); }
  return { width: even(width), height: even(height), ar };
}

// ---------- minimal CDP client over the built-in WebSocket ----------
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();   // id -> {resolve, reject}
    this.listeners = new Map(); // method -> Set<fn>
    ws.addEventListener("message", (ev) => this._onMessage(ev.data));
  }
  _onMessage(raw) {
    const msg = JSON.parse(raw);
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const { resolve, reject } = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message || JSON.stringify(msg.error)));
      else resolve(msg.result);
    } else if (msg.method) {
      const set = this.listeners.get(msg.method);
      if (set) for (const fn of set) fn(msg.params, msg.sessionId);
    }
  }
  on(method, fn) {
    if (!this.listeners.has(method)) this.listeners.set(method, new Set());
    this.listeners.get(method).add(fn);
  }
  send(method, params = {}, sessionId) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.addEventListener("open", () => resolve(ws), { once: true });
    ws.addEventListener("error", (e) => reject(new Error("WebSocket error: " + (e.message || url))), { once: true });
  });
}

// ---------- launch Chrome, return {proc, wsUrl} ----------
function launchChrome(chromePath, userDataDir) {
  return new Promise((resolve, reject) => {
    const args = [
      "--headless=new", "--disable-gpu", "--hide-scrollbars",
      "--force-device-scale-factor=1", "--remote-debugging-port=0",
      "--no-first-run", "--no-default-browser-check", "--disable-extensions",
      "--allow-file-access-from-files", "--mute-audio",
      `--user-data-dir=${userDataDir}`, "about:blank",
    ];
    const proc = spawn(chromePath, args, { stdio: ["ignore", "ignore", "pipe"] });
    let buf = "";
    const onData = (d) => {
      buf += d.toString();
      const m = /DevTools listening on (ws:\/\/\S+)/.exec(buf);
      if (m) { proc.stderr.off("data", onData); resolve({ proc, wsUrl: m[1] }); }
    };
    proc.stderr.on("data", onData);
    proc.on("exit", (code) => reject(new Error(`Chrome exited early (code ${code}) before DevTools came up`)));
    setTimeout(() => reject(new Error("timed out waiting for Chrome DevTools endpoint")), 15000);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- teardown ----------
// Every worker owns a Chrome process and a temp profile dir; a Ctrl-C between
// start() and stop() would otherwise leave both behind. Signals get the
// synchronous path — the async cleanup in main()'s finally never gets to run.
const liveWorkers = new Set();
const tempPaths = new Set();

function cleanupSync() {
  for (const w of liveWorkers) { try { w.proc?.kill("SIGKILL"); } catch { /* already gone */ } }
  for (const p of [...liveWorkers].map((w) => w.userDataDir).concat([...tempPaths])) {
    try { rmSync(p, { recursive: true, force: true }); } catch { /* best effort */ }
  }
}

for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => { cleanupSync(); process.exit(130); });
}

// ---------- capture plan: which frames actually change ----------
// Mathwrite segs are the only true f(t) animation, so their windows are captured
// per-frame; every other stretch between two change points (slide/overlay/caption
// boundaries) is one still, screenshotted once and repeated. Shared read-only by
// all workers — each keeps its own screenshot cache, since a worker only ever
// touches the segments its own frame range covers.
function buildPlan(timeline, total, fps) {
  const animatedWindows = [];
  const staticSegments = []; // { a, b, tShot }

  if (timeline) {
    const eps = 1 / fps;
    const rawWindows = [];
    for (const mw of timeline.mathwrites || []) {
      for (const seg of mw.segs || []) {
        if (typeof seg.start === "number" && typeof seg.end === "number") {
          rawWindows.push([seg.start - eps, seg.end + eps]);
        }
      }
    }
    rawWindows.sort((a, b) => a[0] - b[0]);
    for (const w of rawWindows) {
      const last = animatedWindows[animatedWindows.length - 1];
      if (last && w[0] <= last[1]) last[1] = Math.max(last[1], w[1]);
      else animatedWindows.push([...w]);
    }

    const isAnim = (t) => animatedWindows.some((w) => t >= w[0] && t <= w[1]);

    const bounds = new Set([0, total]);
    for (const s of timeline.slides || []) { if (typeof s.start === "number") bounds.add(s.start); if (typeof s.end === "number") bounds.add(s.end); }
    for (const o of timeline.overlays || []) { if (typeof o.start === "number") bounds.add(o.start); if (typeof o.end === "number") bounds.add(o.end); }
    for (const c of timeline.captions || []) { if (typeof c.start === "number") bounds.add(c.start); if (typeof c.end === "number") bounds.add(c.end); }
    for (const mw of timeline.mathwrites || []) {
      for (const seg of mw.segs || []) {
        if (typeof seg.start === "number") bounds.add(seg.start);
        if (typeof seg.end === "number") bounds.add(seg.end);
      }
    }
    const sortedBounds = Array.from(bounds).sort((a, b) => a - b);

    for (let i = 0; i < sortedBounds.length - 1; i++) {
      const a = sortedBounds[i];
      const b = sortedBounds[i + 1];
      if (a === b) continue;
      if (!isAnim((a + b) / 2)) {
        const tShot = b - a < 0.05 ? Math.max(a, b - 1e-3) : Math.min(a + 0.32, (a + b) / 2);
        staticSegments.push({ a, b, tShot });
      }
    }
  }

  const isAnimated = (t) => animatedWindows.some((w) => t >= w[0] && t <= w[1]);

  // Segments are sorted and disjoint; a worker starts mid-array, so seek by
  // bisection once and let it walk forward from there.
  const segmentIndexAt = (t) => {
    let lo = 0, hi = staticSegments.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const s = staticSegments[mid];
      if (t < s.a) hi = mid - 1;
      else if (t >= s.b) lo = mid + 1;
      else return mid;
    }
    return -1;
  };

  return { animatedWindows, staticSegments, isAnimated, segmentIndexAt, enabled: timeline !== null };
}

// Split [0, n) into k contiguous frame ranges, longest first by remainder.
function splitFrames(n, k) {
  const out = [];
  const base = Math.floor(n / k);
  const extra = n % k;
  let s = 0;
  for (let i = 0; i < k; i++) {
    const len = base + (i < extra ? 1 : 0);
    if (len > 0) out.push([s, s + len]);
    s += len;
  }
  return out;
}

// ---------- one Chrome instance rendering one contiguous frame range ----------
class Worker {
  constructor(id, ctx) {
    this.id = id;
    this.ctx = ctx;              // { chromePath, indexHtml, width, height, opts, ffmpegArgs }
    this.proc = null;
    this.sess = null;
    this.userDataDir = path.join(os.tmpdir(), `lecture-export-${process.pid}-${id}`);
    this.total = 0;
    this.segCache = new Map();   // segment index -> captured buffer
    this.shots = 0;
  }

  async start() {
    const { chromePath, indexHtml, width, height } = this.ctx;
    const { proc, wsUrl } = await launchChrome(chromePath, this.userDataDir);
    this.proc = proc;
    liveWorkers.add(this);
    const cdp = new CDP(await connect(wsUrl));
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    this.sess = (method, params) => cdp.send(method, params, sessionId);

    await this.sess("Page.enable", {});
    await this.sess("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });

    const loaded = new Promise((res) => cdp.on("Page.loadEventFired", () => res()));
    await this.sess("Page.navigate", { url: pathToFileURL(indexHtml).href });
    await loaded;

    // Wait for the player to install its export hook, then strip the chrome.
    for (let tries = 0; tries < 50; tries++) {
      const { result } = await this.sess("Runtime.evaluate", {
        expression: "window.__lectureExport ? window.__lectureExport.prepare() : -1",
        returnByValue: true,
      });
      if (result && result.value >= 0) { this.total = result.value; break; }
      await sleep(100);
    }
    if (!(this.total > 0)) throw new Error(`worker ${this.id}: player export hook unavailable or total_duration is 0`);
  }

  async capture(t) {
    await this.sess("Runtime.evaluate", {
      expression: `window.__lectureExport.renderAt(${t})`,
      awaitPromise: true,
    });
    const { data } = await this.sess("Page.captureScreenshot", {
      ...this.ctx.captureFormat, captureBeyondViewport: false,
    });
    this.shots++;
    return Buffer.from(data, "base64");
  }

  // Render [frameStart, frameEnd) into its own chunk file through its own ffmpeg.
  async renderChunk([frameStart, frameEnd], plan, chunkPath, onFrame) {
    const { opts, ffmpegArgs, framesDir, frameExt } = this.ctx;
    const ffmpeg = spawn(opts.ffmpeg, [...ffmpegArgs, chunkPath], { stdio: ["pipe", "inherit", "inherit"] });
    const done = new Promise((res, rej) => {
      ffmpeg.on("error", rej);
      ffmpeg.on("exit", (code) => (code === 0 ? res() : rej(new Error(`ffmpeg (worker ${this.id}) exited ${code}`))));
    });
    const writeFrame = (buf) =>
      new Promise((res) => { ffmpeg.stdin.write(buf) ? res() : ffmpeg.stdin.once("drain", res); });

    let segIdx = plan.enabled ? plan.segmentIndexAt(frameStart / opts.fps) : -1;

    try {
      for (let i = frameStart; i < frameEnd; i++) {
        const t = i / opts.fps;
        let buf;

        if (!plan.enabled || plan.isAnimated(t)) {
          buf = await this.capture(t);
        } else {
          // Walk forward to the segment holding t; a boundary-precision miss
          // (no segment covers t) falls back to capturing the frame outright.
          while (segIdx >= 0 && segIdx < plan.staticSegments.length && plan.staticSegments[segIdx].b <= t) segIdx++;
          const seg = segIdx >= 0 && segIdx < plan.staticSegments.length && plan.staticSegments[segIdx].a <= t
            ? plan.staticSegments[segIdx] : null;
          if (!seg) {
            buf = await this.capture(t);
          } else {
            if (!this.segCache.has(segIdx)) this.segCache.set(segIdx, await this.capture(seg.tShot));
            buf = this.segCache.get(segIdx);
          }
        }

        await writeFrame(buf);
        if (framesDir) await writeFile(path.join(framesDir, `${String(i).padStart(6, "0")}.${frameExt}`), buf);
        onFrame();
      }
    } finally {
      ffmpeg.stdin.end();
    }
    await done;
  }

  async stop() {
    liveWorkers.delete(this);
    try { this.proc?.kill("SIGKILL"); } catch { /* already gone */ }
    await rm(this.userDataDir, { recursive: true, force: true });
  }
}

function run(bin, args) {
  return new Promise((resolve, reject) => {
    const p = spawn(bin, args, { stdio: ["ignore", "inherit", "inherit"] });
    p.on("error", reject);
    p.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${path.basename(bin)} exited ${code}`))));
  });
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help || !opts.topic) { console.log(USAGE); process.exit(opts.help ? 0 : 2); }

  const topicDir = path.resolve(opts.topic);
  const videoDir = path.join(topicDir, "video");
  const indexHtml = path.join(videoDir, "index.html");
  // build_video.py copies exactly one narration track in; mp3 is the default delivery
  // format, wav the --audio-format wav/both legacy. Take whichever is there.
  const audioPath = ["narration.mp3", "narration.wav"]
    .map((f) => path.join(videoDir, f))
    .find((p) => existsSync(p)) ?? null;
  const outPath = opts.out ? path.resolve(opts.out) : path.join(videoDir, "lecture.mp4");
  const framesDir = opts.keepFrames ? path.join(videoDir, ".frames") : null;
  const chunkDir = path.join(videoDir, ".export-chunks");

  if (!existsSync(indexHtml)) {
    console.error(`ERROR: ${indexHtml} not found — run build_video.py first`);
    process.exit(1);
  }
  const chromePath = findChrome(opts.chrome);
  if (!chromePath) {
    console.error("ERROR: no Chrome/Chromium found. Install Google Chrome or pass --chrome / set CHROME_PATH.");
    process.exit(1);
  }
  const hasAudio = audioPath !== null;
  if (framesDir) { await rm(framesDir, { recursive: true, force: true }); await mkdir(framesDir, { recursive: true }); }
  await rm(chunkDir, { recursive: true, force: true });
  await mkdir(chunkDir, { recursive: true });
  tempPaths.add(chunkDir);

  const { width, height } = resolveSize(path.join(videoDir, "slides"), opts.width, opts.height);
  const captureFormat = opts.png
    ? { format: "png" }
    : { format: "jpeg", quality: Math.max(1, Math.min(100, opts.jpegQuality)) };
  const frameExt = opts.png ? "png" : "jpg";

  // Every chunk is encoded with identical parameters so the concat demuxer can
  // stream-copy them into one file without re-encoding. -threads is filled in
  // once the worker count is known (below): N concurrent libx264 encoders each
  // defaulting to every core oversubscribes the machine badly.
  const buildFfmpegArgs = (threads) => [
    "-y", "-loglevel", "error", "-nostats",
    "-f", "image2pipe", "-framerate", String(opts.fps), "-i", "pipe:0",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", String(opts.crf),
    "-preset", opts.preset, "-threads", String(threads),
    // libx264 + yuv420p needs even dimensions (1365 is odd) — pad to the next even size.
    "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
  ];

  console.log(`[export_mp4] chrome: ${chromePath}`);
  console.log(`[export_mp4] ${width}x${height} @ ${opts.fps}fps, capture: ${opts.png ? "png" : `jpeg q${captureFormat.quality}`}, audio: ${hasAudio ? path.basename(audioPath) : "none"}`);

  const ctx = { chromePath, indexHtml, width, height, opts, ffmpegArgs: null, framesDir, frameExt, captureFormat };
  const workers = [];

  try {
    // The first worker doubles as the probe for the timeline total.
    const lead = new Worker(0, ctx);
    workers.push(lead);
    await lead.start();

    const total = lead.total;
    const nFrames = Math.max(1, Math.round(total * opts.fps));

    let timeline = null;
    try {
      const tlPath = path.join(topicDir, "timeline.json");
      if (existsSync(tlPath)) timeline = JSON.parse(readFileSync(tlPath, "utf-8"));
    } catch (e) {
      console.warn(`[export_mp4] WARN: could not read timeline.json, falling back to full render: ${e.message}`);
    }
    const plan = buildPlan(timeline, total, opts.fps);

    const nWorkers = Math.max(1, Math.min(opts.workers, nFrames));
    const ranges = splitFrames(nFrames, nWorkers);
    console.log(`[export_mp4] total ${total.toFixed(2)}s → ${nFrames} frames across ${ranges.length} worker(s)`);

    const cores = os.availableParallelism?.() ?? os.cpus().length;
    ctx.ffmpegArgs = buildFfmpegArgs(Math.max(1, Math.floor(cores / ranges.length)));

    for (let i = workers.length; i < ranges.length; i++) workers.push(new Worker(i, ctx));
    await Promise.all(workers.slice(1).map((w) => w.start()));

    const t0 = Date.now();
    let doneFrames = 0;
    const onFrame = () => {
      doneFrames++;
      if (doneFrames % opts.fps === 0 || doneFrames === nFrames) {
        const pct = ((doneFrames / nFrames) * 100).toFixed(0);
        process.stdout.write(`\r[export_mp4] frame ${doneFrames}/${nFrames} (${pct}%)   `);
      }
    };

    const chunkPaths = ranges.map((_, i) => path.join(chunkDir, `chunk_${String(i).padStart(3, "0")}.mp4`));
    await Promise.all(ranges.map((r, i) => workers[i].renderChunk(r, plan, chunkPaths[i], onFrame)));
    process.stdout.write("\n");

    // ---- concat the chunks (stream copy) and mux the narration ----
    const listPath = path.join(chunkDir, "chunks.txt");
    await writeFile(listPath, chunkPaths.map((p) => `file '${p.replace(/'/g, "'\\''")}'`).join("\n") + "\n");
    const muxArgs = ["-y", "-loglevel", "error", "-nostats", "-f", "concat", "-safe", "0", "-i", listPath];
    if (hasAudio) muxArgs.push("-i", audioPath);
    muxArgs.push("-c:v", "copy", "-movflags", "+faststart");
    if (hasAudio) muxArgs.push("-c:a", "aac", "-b:a", "192k", "-shortest");
    muxArgs.push(outPath);
    await run(opts.ffmpeg, muxArgs);

    const shots = workers.reduce((n, w) => n + w.shots, 0);
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`[export_mp4] wrote ${outPath} (screenshots ${shots} / ${nFrames} frames in ${secs}s)`);
  } finally {
    await Promise.all(workers.map((w) => w.stop()));
    await rm(chunkDir, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(`[export_mp4] ERROR: ${err.message}`);
  process.exit(1);
});
