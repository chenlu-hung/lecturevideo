#!/usr/bin/env node
// Export a built lecture player to MP4 by stepping it frame-by-frame in a
// headless browser and piping screenshots into ffmpeg.
//
// Usage:
//   node scripts/export_mp4.mjs <topic_dir> [options]
//
// Reads:
//   <topic_dir>/video/index.html   — the player built by build_video.py
//   <topic_dir>/video/narration.wav — muxed in as audio when present (voiced path)
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
// Options:
//   --fps <n>        frames per second (default 30)
//   --width <px>     capture width  (default: auto from the slide aspect ratio)
//   --height <px>    capture height (default: auto, slide height capped at 1080)
//   --crf <n>        libx264 quality, lower = better (default 18)
//   --preset <name>  libx264 preset (default medium)
//   --out <path>     output file (default <topic_dir>/video/lecture.mp4)
//   --chrome <path>  Chrome/Chromium binary (default: auto-detect / $CHROME_PATH)
//   --ffmpeg <path>  ffmpeg binary (default: ffmpeg on PATH)
//   --keep-frames    also write PNG frames to <topic_dir>/video/.frames/

import { spawn } from "node:child_process";
import { existsSync, readdirSync, openSync, readSync, closeSync, readFileSync } from "node:fs";
import { mkdir, rm } from "node:fs/promises";
import { writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

// ---------- arg parsing ----------
function parseArgs(argv) {
  const opts = {
    fps: 30, width: null, height: null, crf: 18, preset: "medium",
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

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help || !opts.topic) { console.log(USAGE); process.exit(opts.help ? 0 : 2); }

  const topicDir = path.resolve(opts.topic);
  const videoDir = path.join(topicDir, "video");
  const indexHtml = path.join(videoDir, "index.html");
  const audioPath = path.join(videoDir, "narration.wav");
  const outPath = opts.out ? path.resolve(opts.out) : path.join(videoDir, "lecture.mp4");
  const framesDir = path.join(videoDir, ".frames");

  if (!existsSync(indexHtml)) {
    console.error(`ERROR: ${indexHtml} not found — run build_video.py first`);
    process.exit(1);
  }
  const chromePath = findChrome(opts.chrome);
  if (!chromePath) {
    console.error("ERROR: no Chrome/Chromium found. Install Google Chrome or pass --chrome / set CHROME_PATH.");
    process.exit(1);
  }
  const hasAudio = existsSync(audioPath);
  if (opts.keepFrames) { await rm(framesDir, { recursive: true, force: true }); await mkdir(framesDir, { recursive: true }); }

  const { width, height } = resolveSize(path.join(videoDir, "slides"), opts.width, opts.height);

  console.log(`[export_mp4] chrome: ${chromePath}`);
  console.log(`[export_mp4] ${width}x${height} @ ${opts.fps}fps, audio: ${hasAudio ? "narration.wav" : "none"}`);

  // ---- launch + attach ----
  const userDataDir = path.join(os.tmpdir(), `lecture-export-${process.pid}`);
  const { proc: chrome, wsUrl } = await launchChrome(chromePath, userDataDir);
  const cleanup = async () => {
    try { chrome.kill("SIGKILL"); } catch {}
    await rm(userDataDir, { recursive: true, force: true });
  };

  try {
    const browserWs = await connect(wsUrl);
    const cdp = new CDP(browserWs);

    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const sess = (method, params) => cdp.send(method, params, sessionId);

    await sess("Page.enable", {});
    await sess("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: 1, mobile: false,
    });

    const loaded = new Promise((res) => cdp.on("Page.loadEventFired", () => res()));
    await sess("Page.navigate", { url: pathToFileURL(indexHtml).href });
    await loaded;

    // Wait for the player to install its export hook, then strip the chrome.
    let total = 0;
    for (let tries = 0; tries < 50; tries++) {
      const { result } = await sess("Runtime.evaluate", {
        expression: "window.__lectureExport ? window.__lectureExport.prepare() : -1",
        returnByValue: true,
      });
      if (result && result.value >= 0) { total = result.value; break; }
      await sleep(100);
    }
    if (!(total > 0)) throw new Error("player export hook unavailable or total_duration is 0");

    const nFrames = Math.max(1, Math.round(total * opts.fps));
    console.log(`[export_mp4] total ${total.toFixed(2)}s → ${nFrames} frames`);

    // ---- prepare timeline for static caching ----
    let timeline = null;
    try {
      const tlPath = path.join(topicDir, "timeline.json");
      if (existsSync(tlPath)) {
        timeline = JSON.parse(readFileSync(tlPath, "utf-8"));
      }
    } catch (e) {
      console.warn(`[export_mp4] WARN: could not read timeline.json, falling back to full render: ${e.message}`);
    }

    const animatedWindows = [];
    const staticSegments = []; // { a, b, tShot, cachedBuf }

    if (timeline) {
      const eps = 1 / opts.fps;
      // 1. Build animated windows
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
        if (!animatedWindows.length) {
          animatedWindows.push([...w]);
        } else {
          const last = animatedWindows[animatedWindows.length - 1];
          if (w[0] <= last[1]) {
            last[1] = Math.max(last[1], w[1]);
          } else {
            animatedWindows.push([...w]);
          }
        }
      }

      const isAnimated = (t) => {
        for (const w of animatedWindows) {
          if (t >= w[0] && t <= w[1]) return true;
        }
        return false;
      };

      // 2. Build static change-point boundaries
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
        const mid = (a + b) / 2;
        if (!isAnimated(mid)) {
          let tShot;
          if (b - a < 0.05) tShot = Math.max(a, b - 1e-3);
          else tShot = Math.min(a + 0.32, (a + b) / 2);
          staticSegments.push({ a, b, tShot, cachedBuf: null });
        }
      }
    }

    // ---- ffmpeg: read PNG frames from stdin, mux audio, encode H.264 ----
    const ff = [
      "-y", "-loglevel", "error", "-nostats",
      "-f", "image2pipe", "-framerate", String(opts.fps), "-i", "pipe:0",
    ];
    if (hasAudio) ff.push("-i", audioPath);
    ff.push(
      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", String(opts.crf),
      "-preset", opts.preset, "-movflags", "+faststart",
      // libx264 + yuv420p needs even dimensions (1365 is odd) — pad to the next even size.
      "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
    );
    if (hasAudio) ff.push("-c:a", "aac", "-b:a", "192k", "-shortest");
    ff.push(outPath);

    const ffmpeg = spawn(opts.ffmpeg, ff, { stdio: ["pipe", "inherit", "inherit"] });
    const ffmpegDone = new Promise((res, rej) => {
      ffmpeg.on("error", rej);
      ffmpeg.on("exit", (code) => (code === 0 ? res() : rej(new Error(`ffmpeg exited ${code}`))));
    });

    const writeFrame = (buf) =>
      new Promise((res) => { ffmpeg.stdin.write(buf) ? res() : ffmpeg.stdin.once("drain", res); });

    // ---- step + screenshot every frame ----
    const t0 = Date.now();
    let screenshotsTaken = 0;
    let currentSegmentIdx = 0;

    for (let i = 0; i < nFrames; i++) {
      const t = i / opts.fps;
      let isAnim = true;
      let seg = null;

      if (timeline) {
        isAnim = false;
        for (const w of animatedWindows) {
          if (t >= w[0] && t <= w[1]) { isAnim = true; break; }
        }
        if (!isAnim) {
          while (currentSegmentIdx < staticSegments.length && staticSegments[currentSegmentIdx].b <= t) {
            currentSegmentIdx++;
          }
          if (currentSegmentIdx < staticSegments.length && staticSegments[currentSegmentIdx].a <= t) {
            seg = staticSegments[currentSegmentIdx];
          } else {
            isAnim = true; // fallback if boundary precision misses
          }
        }
      }

      let buf;
      if (isAnim) {
        await sess("Runtime.evaluate", {
          expression: `window.__lectureExport.renderAt(${t})`,
          awaitPromise: true,
        });
        const { data } = await sess("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
        buf = Buffer.from(data, "base64");
        screenshotsTaken++;
      } else {
        if (!seg.cachedBuf) {
          await sess("Runtime.evaluate", {
            expression: `window.__lectureExport.renderAt(${seg.tShot})`,
            awaitPromise: true,
          });
          const { data } = await sess("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
          seg.cachedBuf = Buffer.from(data, "base64");
          screenshotsTaken++;
        }
        buf = seg.cachedBuf;
      }

      await writeFrame(buf);
      if (opts.keepFrames) await writeFile(path.join(framesDir, `${String(i).padStart(6, "0")}.png`), buf);
      if (i % opts.fps === 0 || i === nFrames - 1) {
        const pct = (((i + 1) / nFrames) * 100).toFixed(0);
        process.stdout.write(`\r[export_mp4] frame ${i + 1}/${nFrames} (${pct}%)   `);
      }
    }
    process.stdout.write("\n");

    ffmpeg.stdin.end();
    await ffmpegDone;
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`[export_mp4] wrote ${outPath} (screenshots ${screenshotsTaken} / ${nFrames} frames in ${secs}s)`);
  } finally {
    await cleanup();
  }
}

main().catch((err) => {
  console.error(`[export_mp4] ERROR: ${err.message}`);
  process.exit(1);
});
