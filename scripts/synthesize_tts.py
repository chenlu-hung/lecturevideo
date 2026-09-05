#!/usr/bin/env python3
"""Synthesize narration audio with IndexTTS-2 (MLX) and rebuild the timeline from real audio.

This is the TTS-enabled alternative to `derive_timeline.py`. Where derive_timeline
trusts the sub-agents' estimated SRT timestamps, this script synthesizes the actual
narration and rebuilds `timeline.json` so that slides switch and overlays fade exactly
when the *spoken* audio crosses each boundary.

Usage:
    python3 synthesize_tts.py <topic_dir> --ref <speaker.wav> [options]

Reads:
    <topic_dir>/.slides.json        — page list + overlay declarations (from split_slides.py)
    <topic_dir>/scripts/NN.srt      — per-page narration, each starting at 00:00:00,000

Writes:
    <topic_dir>/narration.mp3       — the delivered track (default; --audio-format wav/both
                                      keeps narration.wav, 16-bit PCM mono 22.05 kHz, instead
                                      of/alongside it). Whichever is delivered is named by
                                      timeline.json's "audio" field.
    <topic_dir>/timeline.json       — global timeline whose times match the narration track
    <topic_dir>/.tts_segments/      — combined.srt + per-cue wavs (kept for incremental reruns)

How it stays in sync with the rest of the pipeline:
    - Overlay timing reuses the same contract as derive_timeline: the cue carrying
      `[overlay:id]` opens it, the cue carrying `[/overlay:id]` closes it — but the
      start/end come from where those cues actually land in the synthesized audio.
    - The `[overlay:*]` markers are stripped before a cue is sent to the TTS engine so
      they are never spoken.
    - Slide windows are made contiguous (slide N ends where slide N+1 begins) so the
      player never lands in a gap; inter-page silence plays under the outgoing slide.

The IndexTTS-2 CLI loads its model once per invocation, so every cue is synthesized in a
single `--srt` batch call rather than one process per cue.

`--remote-host` moves that one batch call to another machine over ssh + rsync (see
`synthesize_remote` and `scripts/remote/indextts2_onnx_batch.py`) — useful when the CUDA
box is faster than the laptop, or simply to keep the laptop free. Everything else in this
script is unchanged by that choice.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import wave
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from derive_timeline import (  # noqa: E402  (local sibling module)
    OVERLAY_CLOSE_RE,
    OVERLAY_OPEN_RE,
    build_page_mathwrites,
    load_mathwrite_meta,
    load_overlay_meta,
    parse_srt,
)


def strip_markers(text: str) -> str:
    """Remove `[overlay:*]` / `[/overlay:*]` markers and collapse whitespace for TTS."""
    text = OVERLAY_OPEN_RE.sub("", text)
    text = OVERLAY_CLOSE_RE.sub("", text)
    return " ".join(text.split())


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="synthesize_tts.py", add_help=True)
    p.add_argument("topic_dir")
    p.add_argument("--ref", required=True, help="reference speaker wav (zero-shot clone target)")
    p.add_argument("--emo-ref", default=None, help="separate emotion reference wav")
    p.add_argument(
        "--indextts2-dir",
        default=os.environ.get("INDEXTTS2_DIR"),
        help="IndexTTS-2 MLX checkout (default: $INDEXTTS2_DIR)",
    )
    p.add_argument("--indextts2-bin", default=None, help="override CLI binary path")
    p.add_argument("--model", default=None, help="override generation model dir")
    p.add_argument("--preproc-dir", default=None, help="override preprocessing weights dir")
    p.add_argument("--cue-gap", type=float, default=0.25, help="silence between cues (s)")
    p.add_argument("--page-gap", type=float, default=0.6, help="silence between pages (s)")
    p.add_argument("--empty-page-secs", type=float, default=3.0,
                   help="silent duration for a page with no narration (s)")
    # Forwarded verbatim to the CLI.
    p.add_argument("--seed", default=None)
    p.add_argument("--steps", default=None)
    p.add_argument("--cfg", default=None)
    p.add_argument("--speed", default=None)
    p.add_argument("--precision", default=None, choices=["fp16", "fp32", "bf16"])
    # Text frontend.
    p.add_argument("--zh-convert", choices=["auto", "t2s", "off"], default="auto",
                   help="Traditional→Simplified before TTS. IndexTTS-2's tokenizer is "
                        "Simplified-only, so Traditional chars are out-of-vocab and "
                        "mispronounced. 'auto' converts via opencc when available.")
    # Delivered narration track.
    p.add_argument("--audio-format", choices=["mp3", "wav", "both"], default="mp3",
                   help="format of the narration track handed to the player. 'mp3' "
                        "(default) transcodes and drops the intermediate wav — a 50-min "
                        "lecture goes from ~135 MB to ~25 MB, which matters when the topic "
                        "folder lives in cloud storage. 'wav' keeps the legacy lossless-only "
                        "behaviour; 'both' keeps each. Falls back to wav with a warning when "
                        "ffmpeg is unavailable.")
    p.add_argument("--mp3-bitrate", default="96k",
                   help="libmp3lame bitrate for --audio-format mp3/both "
                        "(default 96k; ample for single-voice mono speech)")
    p.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary (default: ffmpeg on PATH)")
    # Workflow.
    p.add_argument("--skip-synth", action="store_true",
                   help="reuse existing per-cue wavs; only re-concat + re-time")
    # Remote synthesis. Only the cue-level TTS moves; the SRT is still built here and
    # the concat + timeline are still derived here from the wavs that come back.
    p.add_argument("--remote-host", default=os.environ.get("LECTUREVIDEO_TTS_HOST"),
                   help="ssh destination that synthesizes instead of the local binary "
                        "(default: $LECTUREVIDEO_TTS_HOST). Needs ssh + rsync both ends.")
    p.add_argument("--remote-cmd", default="~/bin/indextts2-batch",
                   help="remote launcher; must accept the same --ref/--srt/--out flags")
    p.add_argument("--remote-dir", default="~/lecturevideo-tts/jobs",
                   help="remote scratch root; each topic gets a subdirectory under it")
    p.add_argument("--remote-worker", default="~/lecturevideo-tts/bin/indextts2_onnx_batch.py",
                   help="where the worker script lives on the remote")
    p.add_argument("--no-push-worker", action="store_true",
                   help="do not rsync scripts/remote/indextts2_onnx_batch.py to "
                        "--remote-worker before running (default: push, so the remote "
                        "never drifts from this checkout)")
    p.add_argument("--remote-providers", choices=["auto", "cpu", "cuda"], default=None,
                   help="forwarded to the remote worker's execution-provider policy")
    p.add_argument("--remote-resume", action="store_true",
                   help="let the remote skip cues whose wav it already has. Only safe "
                        "when the narration text has not changed since that run.")
    return p.parse_args(argv)


def encode_mp3(wav_path: Path, mp3_path: Path, ffmpeg: str, bitrate: str) -> bool:
    """Transcode the concatenated narration to mp3. Returns True on success.

    Deliberately non-fatal: the wav is already on disk and the pipeline works fine
    with it, so a missing or failing ffmpeg degrades to the wav rather than losing
    a multi-hour synthesis run.
    """
    if shutil.which(ffmpeg) is None:
        print(f"[synthesize_tts] WARN: {ffmpeg} not found — keeping {wav_path.name} "
              f"(pass --audio-format wav to silence this)", file=sys.stderr)
        return False
    print(f"[synthesize_tts] encoding mp3 ({bitrate}) …")
    result = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-nostats",
                             "-i", str(wav_path), "-c:a", "libmp3lame",
                             "-b:a", bitrate, str(mp3_path)])
    if result.returncode != 0 or not mp3_path.is_file():
        print(f"[synthesize_tts] WARN: ffmpeg exited {result.returncode} — "
              f"keeping {wav_path.name}", file=sys.stderr)
        mp3_path.unlink(missing_ok=True)
        return False
    return True


def to_simplified(texts: list[str], mode: str) -> list[str]:
    """Convert Traditional Chinese → Simplified for the TTS engine only.

    IndexTTS-2's SentencePiece vocab contains Simplified forms but not their
    Traditional counterparts (e.g. 檢/樣/標/統 are absent), so feeding Traditional
    text yields out-of-vocab tokens and badly mispronounced audio. The slides and
    subtitles keep their original Traditional text — only what is spoken is converted.
    """
    if mode == "off" or not texts:
        return texts
    opencc = shutil.which("opencc")
    if not opencc:
        if mode == "t2s":
            raise SystemExit("ERROR: --zh-convert t2s but `opencc` is not on PATH "
                             "(install: brew install opencc)")
        print("[synthesize_tts] WARN: opencc not found — sending text unconverted; "
              "Traditional Chinese may be mispronounced (brew install opencc)",
              file=sys.stderr)
        return texts
    try:
        out = subprocess.run([opencc, "-c", "t2s"], input="\n".join(texts),
                             capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        print(f"[synthesize_tts] WARN: opencc failed ({exc}); sending text unconverted",
              file=sys.stderr)
        return texts
    lines = out.rstrip("\n").split("\n")
    if len(lines) == len(texts):
        return lines
    # Line count drifted (unexpected) — convert cue-by-cue to stay aligned.
    print("[synthesize_tts] WARN: opencc line-count drift; converting per cue", file=sys.stderr)
    return [subprocess.run([opencc, "-c", "t2s"], input=tx, capture_output=True,
                           text=True).stdout.strip("\n") for tx in texts]


def rq(path: str) -> str:
    """Quote a path for the remote shell while leaving a leading `~/` expandable.

    The job directory is named after the topic slug, which may be CJK (slugify keeps it),
    so the ssh command line has to survive whatever the slug contains — but quoting the
    whole thing would also kill the tilde the remote paths rely on.
    """
    if path.startswith("~/"):
        return "~/" + shlex.quote(path[2:])
    return shlex.quote(path)


def synthesize_remote(args: argparse.Namespace, ref_path: Path, combined_srt: Path,
                      seg_dir: Path, forwarded: list[str]) -> int:
    """Run the cue batch on `--remote-host` and bring the wavs back.

    Only the TTS itself moves. The combined SRT is built here, the per-cue wavs land in
    the same `.tts_segments/` the local path uses, and everything downstream — concat,
    real-audio timing, captions — runs here unchanged, so a remote run and a local run
    produce the same `timeline.json` shape.

    The transport is plain ssh + rsync: no daemon on the remote, nothing to keep running
    between jobs, and an interrupted run leaves the finished wavs on both ends (see
    --remote-resume).
    """
    host = args.remote_host
    job = f"{args.remote_dir.rstrip('/')}/{seg_dir.parent.name}"

    def ssh(command: str, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", host, command], **kw)

    made = ssh(f"mkdir -p {rq(job)}/segments", capture_output=True, text=True)
    if made.returncode != 0:
        print(f"ERROR: cannot create {host}:{job}\n{made.stderr.strip()}", file=sys.stderr)
        return 1

    if not args.no_push_worker:
        worker = SCRIPT_DIR / "remote" / "indextts2_onnx_batch.py"
        if not worker.is_file():
            print(f"ERROR: worker script missing: {worker}", file=sys.stderr)
            return 1
        dest_dir = args.remote_worker.rsplit("/", 1)[0]
        ssh(f"mkdir -p {rq(dest_dir)}", capture_output=True, text=True)
        if subprocess.run(["rsync", "-q", str(worker),
                           f"{host}:{args.remote_worker}"]).returncode != 0:
            print(f"ERROR: failed to push {worker.name} to {host}", file=sys.stderr)
            return 1

    uploads = [(ref_path, "ref.wav"), (combined_srt, "combined.srt")]
    emo = Path(args.emo_ref).resolve() if args.emo_ref else None
    if emo:
        uploads.append((emo, "emo.wav"))
    for src, name in uploads:
        if subprocess.run(["rsync", "-q", str(src), f"{host}:{job}/{name}"]).returncode != 0:
            print(f"ERROR: failed to upload {src} to {host}:{job}/{name}", file=sys.stderr)
            return 1

    # Rewrite the paths the caller resolved locally to their uploaded counterparts.
    remote_opts: list[str] = []
    skip_next = False
    for tok in forwarded:
        if skip_next:
            skip_next = False
            continue
        if tok == "--emo-ref":
            remote_opts += ["--emo-ref", rq(f"{job}/emo.wav")]
            skip_next = True
        else:
            remote_opts.append(shlex.quote(tok))
    if args.remote_providers:
        remote_opts += ["--providers", args.remote_providers]
    if args.remote_resume:
        remote_opts.append("--resume")

    remote_cmd = " ".join([
        rq(args.remote_cmd),
        "--ref", rq(f"{job}/ref.wav"),
        "--srt", rq(f"{job}/combined.srt"),
        "--out", rq(f"{job}/segments"),
    ] + remote_opts)
    print(f"[synthesize_tts] {host}$ {remote_cmd}")
    rc = ssh(remote_cmd).returncode        # stream the worker's progress to the terminal
    if rc != 0:
        print(f"ERROR: remote synthesis exited {rc}", file=sys.stderr)
        return rc

    print(f"[synthesize_tts] fetching segment wavs from {host} …")
    # Plain -a only: macOS ships openrsync, which rejects --info and most long flags.
    pull = subprocess.run(["rsync", "-a", f"{host}:{job}/segments/", f"{seg_dir}/"])
    if pull.returncode != 0:
        print(f"ERROR: failed to fetch segments from {host}:{job}/segments", file=sys.stderr)
        return 1
    return 0


def resolve_indextts2(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Resolve (binary, model_dir, preproc_dir), defaulting from --indextts2-dir."""
    base = Path(args.indextts2_dir).resolve() if args.indextts2_dir else None
    if args.indextts2_bin:
        binary = Path(args.indextts2_bin).resolve()
    elif base:
        # Prefer the Release build: same output, ~20% less wall clock on a batch.
        # Fall back to Debug so a checkout that only ran `./build.sh Debug` works.
        built = base / ".build/xcode/Build/Products"
        release = built / "Release/indextts2"
        binary = release if release.is_file() else built / "Debug/indextts2"
    else:
        raise SystemExit(
            "ERROR: provide --indextts2-dir (or $INDEXTTS2_DIR) or --indextts2-bin"
        )
    model = Path(args.model).resolve() if args.model else (base / "models/mlx-indextts2-standard-8bit" if base else None)
    preproc = Path(args.preproc_dir).resolve() if args.preproc_dir else (base / "models/preprocessing" if base else None)
    if model is None or preproc is None:
        raise SystemExit("ERROR: provide --indextts2-dir or both --model and --preproc-dir")
    return binary, model, preproc


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    topic_dir = Path(args.topic_dir).resolve()
    slides_json = topic_dir / ".slides.json"
    scripts_dir = topic_dir / "scripts"
    seg_dir = topic_dir / ".tts_segments"
    combined_srt = seg_dir / "combined.srt"
    narration_path = topic_dir / "narration.wav"
    timeline_path = topic_dir / "timeline.json"

    if not slides_json.is_file():
        print(f"ERROR: missing {slides_json} (run split_slides.py first)", file=sys.stderr)
        return 1
    ref_path = Path(args.ref).resolve()
    if not ref_path.is_file():
        print(f"ERROR: reference voice not found: {ref_path}", file=sys.stderr)
        return 1

    binary = model_dir = preproc_dir = None
    if not args.remote_host:
        binary, model_dir, preproc_dir = resolve_indextts2(args)
        if not args.skip_synth and not binary.is_file():
            print(f"ERROR: IndexTTS-2 binary not found: {binary}", file=sys.stderr)
            print("       build it with `./build.sh Release` in the IndexTTS-2 MLX checkout.",
                  file=sys.stderr)
            return 1

    pages = json.loads(slides_json.read_text(encoding="utf-8"))["pages"]
    try:
        mw_meta = load_mathwrite_meta(topic_dir, pages)
        ov_meta = load_overlay_meta(topic_dir, pages)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    # ---- 1. Flatten every page's cues into one globally-indexed list. ----
    # Each cue gets a global index used both as the combined-SRT entry number and as the
    # CLI's output filename suffix (combined_<gidx:03d>.wav).
    gidx = 0
    page_cues: list[dict] = []          # per page: {"page": <page>, "cues": [(gidx, cue), ...]}
    synth_entries: list[tuple[int, str]] = []   # (gidx, clean_text) actually sent to the CLI
    for page in pages:
        idx = page["index"]
        cues = parse_srt(scripts_dir / f"{idx:02d}.srt")
        tagged: list[tuple[int, dict]] = []
        for cue in cues:
            gidx += 1
            tagged.append((gidx, cue))
            clean = strip_markers(cue["text"])
            if clean:
                synth_entries.append((gidx, clean))
        page_cues.append({"page": page, "cues": tagged})

    if not synth_entries:
        print("ERROR: no narration cues found in any page SRT", file=sys.stderr)
        return 1

    # Convert the spoken text to Simplified for the engine (overlay markers were
    # already stripped above; the page SRTs and slides keep their Traditional text).
    converted = to_simplified([txt for _g, txt in synth_entries], args.zh_convert)
    synth_entries = [(g, c) for (g, _txt), c in zip(synth_entries, converted)]

    # ---- 2. Synthesize all cues in one batch (model loads once). ----
    seg_dir.mkdir(parents=True, exist_ok=True)
    if not args.skip_synth:
        with combined_srt.open("w", encoding="utf-8") as f:
            for g, text in synth_entries:
                f.write(f"{g}\n00:00:00,000 --> 00:00:01,000\n{text}\n\n")

        forwarded: list[str] = []
        if args.emo_ref:
            forwarded += ["--emo-ref", str(Path(args.emo_ref).resolve())]
        for opt, val in (("--seed", args.seed), ("--steps", args.steps),
                         ("--cfg", args.cfg), ("--speed", args.speed),
                         ("--precision", args.precision)):
            if val is not None:
                forwarded += [opt, str(val)]

        print(f"[synthesize_tts] synthesizing {len(synth_entries)} cues via IndexTTS-2 …")
        if args.remote_host:
            rc = synthesize_remote(args, ref_path, combined_srt, seg_dir, forwarded)
        else:
            cmd = [
                str(binary),
                "--model", str(model_dir),
                "--ref", str(ref_path),
                "--srt", str(combined_srt),
                "--out", str(seg_dir),
                "--preproc-dir", str(preproc_dir),
            ] + forwarded
            print(f"[synthesize_tts] $ {' '.join(cmd)}")
            # stream the CLI's own progress to the terminal
            rc = subprocess.run(cmd).returncode
        if rc != 0:
            print(f"ERROR: IndexTTS-2 exited {rc}", file=sys.stderr)
            return 1
    else:
        print("[synthesize_tts] --skip-synth: reusing existing segment wavs")

    # ---- 3. Determine the common wav format from the first produced segment. ----
    def seg_path(g: int) -> Path:
        return seg_dir / f"combined_{g:03d}.wav"

    params = None
    for g, _ in synth_entries:
        p = seg_path(g)
        if p.is_file():
            with wave.open(str(p), "rb") as w:
                params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            break
    if params is None:
        print("ERROR: no segment wavs were produced by IndexTTS-2", file=sys.stderr)
        return 1
    nch, sw, fr = params
    frame_bytes = sw * nch
    cue_gap = int(round(args.cue_gap * fr))
    page_gap = int(round(args.page_gap * fr))
    empty_page = int(round(args.empty_page_secs * fr))
    silence_cue = b"\x00" * (cue_gap * frame_bytes)
    silence_page = b"\x00" * (page_gap * frame_bytes)
    silence_empty = b"\x00" * (empty_page * frame_bytes)

    # ---- 4. Concatenate into narration.wav, recording each cue's real frame span. ----
    real: dict[int, tuple[int, int]] = {}   # gidx -> (start_frame, end_frame)
    page_start: list[int] = []              # frame where each page's audio begins
    total = 0
    missing = 0

    out = wave.open(str(narration_path), "wb")
    out.setnchannels(nch)
    out.setsampwidth(sw)
    out.setframerate(fr)
    try:
        for pi, entry in enumerate(page_cues):
            page_start.append(total)
            cues = entry["cues"]
            if not cues:
                out.writeframes(silence_empty)
                total += empty_page
            for ci, (g, _cue) in enumerate(cues):
                p = seg_path(g)
                start = total
                if p.is_file():
                    with wave.open(str(p), "rb") as w:
                        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != params:
                            print(f"ERROR: format mismatch in {p.name}", file=sys.stderr)
                            return 1
                        frames = w.readframes(w.getnframes())
                    out.writeframes(frames)
                    total += len(frames) // frame_bytes
                else:
                    missing += 1
                real[g] = (start, total)
                if ci != len(cues) - 1 and cue_gap:
                    out.writeframes(silence_cue)
                    total += cue_gap
            if pi != len(page_cues) - 1 and page_gap:
                out.writeframes(silence_page)
                total += page_gap
    finally:
        out.close()
    page_start.append(total)  # sentinel: end of the last page

    # ---- 4b. Deliver the track in the requested format. ----
    # The timeline's "audio" field, the player's <source>, build_video.py and
    # export_mp4.mjs must all name the same file, so settle it here and let the
    # name flow downstream. mp3 keeps the wav only when asked for 'both'.
    mp3_path = narration_path.with_suffix(".mp3")
    audio_name = narration_path.name
    if args.audio_format in ("mp3", "both"):
        if encode_mp3(narration_path, mp3_path, args.ffmpeg, args.mp3_bitrate):
            audio_name = mp3_path.name
            if args.audio_format == "mp3":
                narration_path.unlink()
        elif args.audio_format == "mp3":
            pass  # encode_mp3 already warned; the wav stands in
    if args.audio_format == "wav":
        mp3_path.unlink(missing_ok=True)   # drop a stale mp3 from an earlier run

    # ---- 5. Build the timeline from real audio frames. ----
    def t(frame: int) -> float:
        return round(frame / fr, 3)

    timeline_slides = []
    timeline_overlays = []
    timeline_mathwrites = []
    timeline_captions = []
    warnings: list[str] = []

    # Captions show the original (Traditional) spoken text at the cue's *real*
    # audio span — never the Simplified text that was only fed to the engine.
    for entry in page_cues:
        for g, cue in entry["cues"]:
            ctext = strip_markers(cue["text"])
            if not ctext:
                continue
            start_frame, end_frame = real.get(g, (None, None))
            if start_frame is None:
                continue
            timeline_captions.append({
                "start": t(start_frame),
                "end": t(end_frame),
                "text": ctext,
            })

    for pi, entry in enumerate(page_cues):
        page = entry["page"]
        idx = page["index"]
        timeline_slides.append({
            "index": idx,
            "start": t(page_start[pi]),
            "end": t(page_start[pi + 1]),   # contiguous: ends where the next page begins
            "image": page.get("image", f"slides.images/{idx:02d}.png"),
        })

        cues = entry["cues"]
        for overlay in page.get("overlays", []):
            oid = overlay["id"]
            open_g, close_g = _find_overlay_cues(cues, oid)
            if open_g is None or close_g is None:
                warnings.append(f"page {idx}: overlay '{oid}' missing/unbalanced markers")
                continue
            start_frame = real.get(open_g, (page_start[pi], page_start[pi]))[0]
            end_frame = real.get(close_g, (start_frame, start_frame))[1]
            ov_entry = {
                "slide": idx,
                "id": oid,
                "label": overlay.get("label", oid),
                "start": t(start_frame),
                "end": t(end_frame),
            }
            bbox = ov_meta.get((idx, oid))
            if bbox:
                ov_entry["bbox"] = bbox
            timeline_overlays.append(ov_entry)

        if mw_meta is not None:
            def resolve(marker: str, _cues=cues, _pstart=page_start[pi]):
                open_g, close_g = _find_overlay_cues(_cues, marker)
                if open_g is None or close_g is None:
                    return None
                start_frame = real.get(open_g, (_pstart, _pstart))[0]
                end_frame = real.get(close_g, (start_frame, start_frame))[1]
                return t(start_frame), t(end_frame)

            timeline_mathwrites.extend(
                build_page_mathwrites(page, t(page_start[pi]), mw_meta, resolve, warnings)
            )

    timeline = {
        "total_duration": t(total),
        "audio": audio_name,
        "slides": timeline_slides,
        "overlays": timeline_overlays,
        "captions": timeline_captions,
    }
    if timeline_mathwrites:
        timeline["mathwrites"] = timeline_mathwrites
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    delivered = mp3_path if audio_name.endswith(".mp3") else narration_path
    print(f"[synthesize_tts] wrote {delivered} ({t(total)}s, {nch}ch/{sw*8}bit/{fr}Hz source"
          f"{', mp3 ' + args.mp3_bitrate if delivered is mp3_path else ''})")
    print(f"[synthesize_tts] wrote {timeline_path}: {len(timeline_slides)} slides, "
          f"{len(timeline_overlays)} overlays, {len(timeline_captions)} captions")
    if missing:
        warnings.append(f"{missing} cue(s) had no audio (empty/failed synthesis)")
    for w in warnings:
        print(f"[synthesize_tts] WARN: {w}", file=sys.stderr)
    return 0


def _find_overlay_cues(cues: list[tuple[int, dict]], oid: str):
    """Return (opener_gidx, closer_gidx) for an overlay id within a page's cues.

    Mirrors derive_timeline.find_overlay_times but yields the cues' global indices so
    the caller can look up where they landed in the synthesized audio.
    """
    import re
    open_re = re.compile(r"\[overlay:" + re.escape(oid) + r"\]")
    close_re = re.compile(r"\[/overlay:" + re.escape(oid) + r"\]")
    open_g = close_g = None
    for g, cue in cues:
        if open_g is None and open_re.search(cue["text"]):
            open_g = g
        if close_re.search(cue["text"]):
            close_g = g
            if open_g is not None:
                break
    return open_g, close_g


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
