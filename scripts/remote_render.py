#!/usr/bin/env python3
"""Render narration and MP4 on a remote GPU box, keeping this machine free.

Usage:
    python3 remote_render.py <topic_dir> --ref <voice.wav> [--remote-host HOST] [options]

`synthesize_tts.py --remote-host` moves only the per-cue TTS and pulls every wav
back, leaving the concat, the timeline and the whole MP4 export here. This moves
the rest: the remote runs the same three scripts this repo already has, and only
the finished artefacts come home.

    local                                   remote
    -----                                   ------
    slides.md, scripts/NN.srt
    compile_marp.sh  ->  slides.images/
                       --- upload --->      synthesize_tts.py   (GPU: cues, concat,
                                                                 narration.mp3, timeline)
                                            build_video.py      (player + slides)
                                            export_mp4.mjs      (headless Chrome + ffmpeg)
                       <-- download ---     lecture.mp4, narration.mp3, timeline.json
                                            job dir removed

Marp compilation deliberately stays local. The slides are typeset in macOS fonts
(PingFang TC / Helvetica), and re-rendering them on Linux would reflow every page
— which is exactly how a table silently loses its last row (see check_fit.py's
CLIPPED check). The PNGs this uploads are the ones the handout PDF was made from,
so the video and the PDF cannot disagree.

What the remote needs (see references/remote-tts.md §"Rendering on the remote"):
Node >= 22, a Chrome binary, ffmpeg, an `opencc` on PATH, the IndexTTS-2 worker,
and — because the player draws captions as live HTML text — the same CJK font the
local player uses, or the captions will not match locally-rendered parts.

Exit codes: 0 success; 1 setup/runtime failure; 2 usage error.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

# Pushed to the remote so it can run the pipeline itself. Paths are relative to
# SKILL_ROOT and keep their layout there, because build_video.py resolves its
# player assets as <script>/../assets/player and synthesize_tts.py imports
# derive_timeline as a sibling module.
RUNTIME_FILES = [
    "scripts/synthesize_tts.py",
    "scripts/derive_timeline.py",
    "scripts/build_video.py",
    "scripts/export_mp4.mjs",
    "assets/player/index.html",
    "assets/player/player.css",
    "assets/player/player.js",
    "assets/player/hershey-font.js",
]

# Uploaded per job. `.slides.json` drives the cue/page mapping, `scripts/` holds
# the narration, `slides.images/` is the rendered deck the player shows.
JOB_INPUTS = ["scripts", "slides.images", ".slides.json"]

# Brought home. The wavs stay on the remote — they are an intermediate, and at
# ~170 MB per deck they are the reason this script exists.
JOB_OUTPUTS = ["video/lecture.mp4", "narration.mp3", "narration.wav", "timeline.json"]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="remote_render.py", add_help=True)
    p.add_argument("topic_dir")
    p.add_argument("--ref", required=True, help="reference speaker wav (zero-shot clone target)")
    p.add_argument("--emo-ref", default=None, help="separate emotion reference wav")
    p.add_argument("--remote-host", default=None,
                   help="ssh destination (default: $LECTUREVIDEO_TTS_HOST)")
    p.add_argument("--remote-dir", default="/data/lecturevideo-tts/render",
                   help="remote working root. Keep this on the big disk: a job holds the "
                        "deck PNGs, every cue wav and the export's frame chunks, and the "
                        "home partition is usually the small one.")
    p.add_argument("--remote-node", default="~/.local/bin/node")
    p.add_argument("--remote-chrome", default="~/.local/chrome-linux64/chrome")
    p.add_argument("--remote-ffmpeg", default="~/.local/bin/ffmpeg")
    p.add_argument("--remote-tts-bin", default="~/bin/indextts2-batch")
    p.add_argument("--remote-tts-model", default="/data/lecturevideo-tts/models-fp16",
                   help="ONNX model dir. Must match what ~/bin/indextts2-batch would "
                        "pick: synthesize_tts.py always passes --model, which overrides "
                        "the launcher's own choice.")
    # Forwarded to the remote steps.
    p.add_argument("--seed", default=None)
    p.add_argument("--steps", default=None)
    p.add_argument("--cfg", default=None)
    p.add_argument("--cue-gap", default=None)
    p.add_argument("--page-gap", default=None)
    p.add_argument("--audio-format", choices=["mp3", "wav", "both"], default="mp3")
    p.add_argument("--fps", default=None)
    p.add_argument("--crf", default=None)
    p.add_argument("--workers", default=None, help="parallel Chrome workers for the export")
    # Flow control.
    p.add_argument("--keep-remote", action="store_true",
                   help="leave the job dir on the remote (default: delete it on success)")
    p.add_argument("--skip-tts", action="store_true",
                   help="reuse the narration already in the remote job dir")
    p.add_argument("--no-push-runtime", action="store_true",
                   help="do not refresh the scripts/assets copy on the remote")
    return p.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    import os
    host = args.remote_host or os.environ.get("LECTUREVIDEO_TTS_HOST")
    if not host:
        print("ERROR: no remote host (pass --remote-host or set $LECTUREVIDEO_TTS_HOST)",
              file=sys.stderr)
        return 2

    topic = Path(args.topic_dir).resolve()
    ref = Path(args.ref).resolve()
    if not topic.is_dir():
        print(f"ERROR: not a directory: {topic}", file=sys.stderr)
        return 2
    if not ref.is_file():
        print(f"ERROR: reference voice not found: {ref}", file=sys.stderr)
        return 2
    for needed in JOB_INPUTS:
        if not (topic / needed).exists():
            print(f"ERROR: missing {topic / needed} — run compile_marp.sh and "
                  "split_slides.py first", file=sys.stderr)
            return 1

    slug = topic.name
    root = args.remote_dir.rstrip("/")
    job = f"{root}/jobs/{slug}"

    def ssh(command: str, *, check: bool = True, stream: bool = False) -> int:
        """Run one command on the remote through a login-ish PATH.

        ~/.local/bin holds node, ffmpeg and the opencc shim; a non-interactive ssh
        shell does not necessarily have it.
        """
        wrapped = f'export PATH="$HOME/.local/bin:$PATH"; {command}'
        proc = subprocess.run(["ssh", host, wrapped],
                              **({} if stream else {"capture_output": True, "text": True}))
        if check and proc.returncode != 0:
            print(f"ERROR: remote command failed ({proc.returncode}): {command}",
                  file=sys.stderr)
            if not stream and proc.stderr:
                print(proc.stderr.strip()[-2000:], file=sys.stderr)
        return proc.returncode

    def rsync(src: str, dst: str, *, extra: list[str] | None = None) -> int:
        cmd = ["rsync", "-a", "--partial"] + (extra or []) + [src, dst]
        return subprocess.run(cmd).returncode

    print(f"[remote_render] host={host}  job={job}")

    if ssh(f"mkdir -p {shlex.quote(job)}/video") != 0:
        return 1

    # ---- 1. Runtime: the pipeline scripts and the player assets. ----
    if not args.no_push_runtime:
        print("[remote_render] pushing runtime …")
        if ssh(f"mkdir -p {shlex.quote(root)}/scripts {shlex.quote(root)}/assets/player") != 0:
            return 1
        for rel in RUNTIME_FILES:
            if rsync(str(SKILL_ROOT / rel), f"{host}:{root}/{rel}") != 0:
                print(f"ERROR: failed to push {rel}", file=sys.stderr)
                return 1

    # ---- 2. Job inputs. ----
    print("[remote_render] uploading deck …")
    for rel in JOB_INPUTS:
        src = topic / rel
        # Trailing slash on a directory copies its contents, not the dir itself.
        src_arg = f"{src}/" if src.is_dir() else str(src)
        dst = f"{host}:{job}/{rel}/" if src.is_dir() else f"{host}:{job}/{rel}"
        if src.is_dir() and ssh(f"mkdir -p {shlex.quote(job)}/{rel}") != 0:
            return 1
        if rsync(src_arg, dst, extra=["--delete"] if src.is_dir() else None) != 0:
            print(f"ERROR: failed to upload {rel}", file=sys.stderr)
            return 1
    if rsync(str(ref), f"{host}:{job}/ref.wav") != 0:
        return 1
    if args.emo_ref and rsync(str(Path(args.emo_ref).resolve()), f"{host}:{job}/emo.wav") != 0:
        return 1

    q = shlex.quote
    rjob, rroot = q(job), q(root)

    # ---- 3. Narration: TTS, concat and the audio-accurate timeline, all there. ----
    if not args.skip_tts:
        tts = [
            "python3", f"{rroot}/scripts/synthesize_tts.py", rjob,
            "--ref", f"{rjob}/ref.wav",
            "--indextts2-bin", args.remote_tts_bin,
            "--model", q(args.remote_tts_model),
            # The ONNX worker accepts and ignores this; synthesize_tts.py refuses
            # to resolve without it.
            "--preproc-dir", q(args.remote_tts_model),
            "--ffmpeg", args.remote_ffmpeg,
            "--audio-format", args.audio_format,
        ]
        if args.emo_ref:
            tts += ["--emo-ref", f"{rjob}/emo.wav"]
        for flag in ("seed", "steps", "cfg", "cue_gap", "page_gap"):
            val = getattr(args, flag)
            if val is not None:
                tts += [f"--{flag.replace('_', '-')}", q(str(val))]
        print("[remote_render] narration (GPU) …")
        if ssh(" ".join(tts), stream=True) != 0:
            print(f"NOTE: job kept at {host}:{job} for inspection", file=sys.stderr)
            return 1

    # ---- 4. Player, then the MP4. ----
    print("[remote_render] building player …")
    if ssh(f"python3 {rroot}/scripts/build_video.py {rjob}", stream=True) != 0:
        print(f"NOTE: job kept at {host}:{job}", file=sys.stderr)
        return 1

    export = [args.remote_node, f"{rroot}/scripts/export_mp4.mjs", rjob,
              "--chrome", args.remote_chrome, "--ffmpeg", args.remote_ffmpeg]
    for flag in ("fps", "crf", "workers"):
        val = getattr(args, flag)
        if val is not None:
            export += [f"--{flag}", q(str(val))]
    print("[remote_render] exporting MP4 …")
    if ssh(" ".join(export), stream=True) != 0:
        print(f"NOTE: job kept at {host}:{job}", file=sys.stderr)
        return 1

    # ---- 5. Bring home only the artefacts. ----
    print("[remote_render] downloading …")
    (topic / "video").mkdir(exist_ok=True)
    got = []
    for rel in JOB_OUTPUTS:
        probe = subprocess.run(["ssh", host, f"test -f {q(job + '/' + rel)}"])
        if probe.returncode != 0:
            continue
        if rsync(f"{host}:{job}/{rel}", str(topic / rel)) != 0:
            print(f"ERROR: failed to download {rel} — job kept at {host}:{job}",
                  file=sys.stderr)
            return 1
        got.append(rel)
    if "video/lecture.mp4" not in got:
        print(f"ERROR: remote produced no video/lecture.mp4 — job kept at {host}:{job}",
              file=sys.stderr)
        return 1
    for rel in got:
        size = (topic / rel).stat().st_size
        print(f"[remote_render] {rel}  {size / 1e6:.1f} MB")

    # ---- 6. Clean up. Only after everything above succeeded. ----
    if args.keep_remote:
        print(f"[remote_render] --keep-remote: job left at {host}:{job}")
    else:
        if ssh(f"rm -rf {rjob}") != 0:
            print(f"WARN: could not remove {host}:{job}", file=sys.stderr)
        else:
            print(f"[remote_render] removed {host}:{job}")

    print(f"[remote_render] done → {topic / 'video/lecture.mp4'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
