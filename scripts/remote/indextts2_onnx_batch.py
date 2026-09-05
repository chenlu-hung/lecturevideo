#!/usr/bin/env python3
"""Batch IndexTTS-2 narration on a CUDA box, torch-free, via ONNX Runtime.

This is the *remote worker* behind `synthesize_tts.py --remote-host`. It exists so a
Linux/NVIDIA machine can stand in for the Apple-Silicon MLX `indextts2` binary without
changing the pipeline's contract: same flags in, same `<srt-stem>_<NNN>.wav` files out.

Engine: `indextts-onnx` (https://github.com/vra/indextts-onnx) — the IndexTTS-2 stack
exported to 10 ONNX graphs and driven by ONNX Runtime + numpy. No PyTorch at inference.

Execution-provider policy (`--providers`, default `auto`)
    The published graphs are a mix: the GPT-2 stack, w2v-BERT, CAMPPlus, the semantic
    codec and the two s2mel graphs ship int8 (`quantize_dynamic`, i.e. MatMulInteger),
    while `dit_step` and `bigvgan` ship fp32. ONNX Runtime's CUDA EP has no MatMulInteger
    kernel, so an int8 graph placed on CUDA runs its matmuls on CPU *and* pays hundreds of
    device copies per step — measured on an RTX 4080 that made the GPT-2 loop 24% slower
    than plain CPU. So `auto` routes by dtype: int8 graphs → CPU, fp32 graphs → CUDA.
    That is what makes the fp32 DiT and vocoder ~28x and ~128x faster while leaving the
    AR loop where it is actually fastest today. Drop in fp16/fp32 `gpt2_*.onnx` and `auto`
    picks them up on CUDA with no code change.

Deliberate gaps vs. the MLX binary: `--speed` and `--precision` have no equivalent in this
engine. They are accepted and warned about rather than rejected, so the caller can forward
its flags verbatim. `--emo-ref` *is* supported — see `_prepare_reference` below.

Usage:
    indextts2_onnx_batch.py --ref speaker.wav --srt combined.srt --out <dir>
    indextts2_onnx_batch.py --ref speaker.wav --text "…" --out out.wav
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_MODEL_DIR = os.environ.get(
    "INDEXTTS_ONNX_MODELS", os.path.expanduser("~/.cache/indextts-onnx/models")
)

# SRT block: "<n>\n<start> --> <end>\n<text…>"
SRT_INDEX_RE = re.compile(r"^\d+$")


def parse_srt(path: Path) -> list[tuple[int, str]]:
    """Return [(entry_number, text)] — the number is the caller's global cue index."""
    entries: list[tuple[int, str]] = []
    block: list[str] = []

    def flush() -> None:
        if len(block) < 3 or not SRT_INDEX_RE.match(block[0].strip()):
            return
        text = " ".join(line.strip() for line in block[2:] if line.strip())
        if text:
            entries.append((int(block[0].strip()), text))

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            block.append(line)
        else:
            flush()
            block = []
    flush()
    return entries


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="indextts2_onnx_batch.py", add_help=True)
    p.add_argument("--ref", required=True, help="reference speaker wav (zero-shot clone target)")
    p.add_argument("--srt", default=None, help="batch mode: one wav per SRT entry")
    p.add_argument("--text", default=None, help="single-shot mode")
    p.add_argument("--out", required=True, help="output dir (--srt) or wav path (--text)")
    p.add_argument("--model", default=DEFAULT_MODEL_DIR, help="ONNX model dir")
    p.add_argument("--providers", choices=["auto", "cpu", "cuda"], default="auto",
                   help="auto: int8 graphs on CPU, fp32 on CUDA (see module docstring)")
    p.add_argument("--steps", type=int, default=10, help="DiT flow-matching steps")
    p.add_argument("--cfg", type=float, default=0.7, help="classifier-free guidance rate")
    p.add_argument("--seed", type=int, default=None, help="base RNG seed (cue N uses seed+N)")
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--top-p", type=float, default=0.8)
    p.add_argument("--top-k", type=int, default=30)
    p.add_argument("--max-mel-tokens", type=int, default=1500)
    p.add_argument("--threads", type=int, default=0,
                   help="ORT CPU threads per worker (0 = split all cores across --workers)")
    p.add_argument("--workers", type=int, default=1,
                   help="cues synthesized concurrently. The AR loop is the bottleneck and "
                        "it sits on the CPU, so N workers on an N-core box is close to a "
                        "throughput multiplier; sessions and the reference conditioning are "
                        "shared, so memory does not multiply. --seed is process-wide once "
                        "N > 1 (numpy's global RNG is not per-cue under threads).")
    p.add_argument("--io-binding", choices=["auto", "on", "off"], default="auto",
                   help="keep the AR loop's KV cache on the GPU between tokens instead of "
                        "round-tripping it through numpy. 'auto' turns it on exactly when "
                        "gpt2_step landed on CUDA, which is the only case where it applies.")
    p.add_argument("--resume", action="store_true",
                   help="skip cues whose wav already exists (restartable long jobs)")
    p.add_argument("--emo-ref", default=None,
                   help="separate wav whose emotion is transferred onto the cloned voice")
    # Accepted for flag-compatibility with the MLX binary; no effect here.
    p.add_argument("--speed", default=None)
    p.add_argument("--precision", default=None)
    p.add_argument("--preproc-dir", default=None)
    return p.parse_args(argv)


def make_session_factory(policy: str, threads: int):
    """Patch ort.InferenceSession so each graph lands on the right execution provider."""
    import onnxruntime as ort

    try:
        ort.preload_dlls()      # find the CUDA/cuDNN shipped by the nvidia-* pip wheels
    except Exception as exc:    # older ORT, or libs already on LD_LIBRARY_PATH
        print(f"[indextts2-onnx] preload_dlls skipped: {exc}", file=sys.stderr)

    have_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
    if policy == "cuda" and not have_cuda:
        raise SystemExit("ERROR: --providers cuda but CUDAExecutionProvider is unavailable")
    if policy == "auto" and not have_cuda:
        print("[indextts2-onnx] WARN: no CUDA EP — falling back to CPU for every graph",
              file=sys.stderr)

    original = ort.InferenceSession
    placements: list[tuple[str, str]] = []

    def choose(path: str) -> list[str]:
        if policy == "cpu" or not have_cuda:
            return ["CPUExecutionProvider"]
        if policy == "cuda":
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        # auto: int8 graphs have no CUDA kernels for MatMulInteger — keep them on CPU.
        if "_int8" in os.path.basename(path):
            return ["CPUExecutionProvider"]
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]

    def patched(path, sess_options=None, providers=None, **kwargs):
        chosen = choose(str(path))
        placements.append((os.path.basename(str(path)), chosen[0]))
        if sess_options is not None and threads:
            sess_options.intra_op_num_threads = threads
            sess_options.inter_op_num_threads = threads
        return original(path, sess_options, providers=chosen, **kwargs)

    ort.InferenceSession = patched
    return placements


def build_engine(args: argparse.Namespace, emo_ref=None):
    """Load IndexTTSInfer with cfg_rate wired through and, on CUDA, a device-resident KV cache."""
    import numpy as np
    from indextts_onnx.infer import IndexTTSInfer, STOP_MEL_TOKEN
    from indextts_onnx.sampling import top_k_top_p_sample

    cfg_rate = args.cfg
    want_binding = args.io_binding

    class Engine(IndexTTSInfer):
        # infer() does not expose cfg_rate; pin it here rather than duplicating infer().
        def _s2mel_inference(self, *a, **kw):
            kw.setdefault("cfg_rate", cfg_rate)
            return super()._s2mel_inference(*a, **kw)

        def _prepare_reference(self, ref_audio_path):
            """Add the emotion reference upstream leaves on the table.

            `gpt2_init`/`gpt2_forward` take `emo_cond` as a real, separately-shaped input
            ([1, emo_len, 1024] of w2v-BERT hidden states) and run it through the GPT's own
            emotion perceiver — but the ONNX driver simply assigns the *speaker's* embedding
            to it, so every voice speaks in its own reference's affect. Feeding a second
            wav's embedding is all that emotion transfer needs; the graph does the rest.

            Only the audio-derived path is available this way. IndexTTS-2's other two
            controls — an 8-dim emotion vector via `emo_matrix.npy`, and text-prompt emotion
            via the QwenEmotion LLM — need weights this export does not ship.
            """
            super()._prepare_reference(ref_audio_path)
            if emo_ref is None or getattr(self, "_cache_emo_ref", None) == str(emo_ref):
                return
            from indextts_onnx.audio import load_and_cut, resample
            audio, sr = load_and_cut(str(emo_ref), max_seconds=15.0)
            self._cache_emo_cond = self._run_wav2vec2bert(resample(audio, sr, 16000))
            self._cache_emo_ref = str(emo_ref)
            print(f"[indextts2-onnx] emotion reference: {emo_ref.name} "
                  f"({self._cache_emo_cond.shape[1]} frames)")

        def _gpt_dtype(self):
            """float16 when the GPT-2 graphs were exported half, else float32."""
            declared = self.gpt2_init.get_inputs()[0].type
            return np.float16 if "float16" in declared else np.float32

        def _use_binding(self) -> bool:
            if want_binding == "off":
                if self._gpt_dtype() == np.float16:
                    raise SystemExit(
                        "ERROR: --io-binding off cannot drive fp16 GPT-2 graphs — the "
                        "upstream loop feeds float32. Use --io-binding auto/on, or point "
                        "INDEXTTS_ONNX_MODELS at the fp32 graphs.")
                return False
            on_cuda = "CUDAExecutionProvider" in self.gpt2_step.get_providers()[:1]
            if want_binding == "on" and not on_cuda:
                raise SystemExit("ERROR: --io-binding on, but gpt2_step is not on CUDA")
            return on_cuda

        def _gpt2_generate(self, spk_cond, text_token_ids, emo_cond, max_mel_tokens=1500,
                           top_k=30, top_p=0.8, temperature=0.8, repetition_penalty=10.0):
            """Upstream's AR loop, but the KV cache never leaves the GPU.

            Upstream passes `past_*` in and takes `present_*` out as numpy, so every token
            round-trips the whole cache across PCIe. This model is 24 layers x 20 heads x 64,
            i.e. ~245 KB per token per direction: by 500 tokens that is ~250 MB of copying per
            step, which measured at ~25 of the ~30 ms each token cost. Binding `present_*`
            straight back onto `past_*` as device tensors removes it; only `logits` (a few
            KB) comes back to the host, because sampling happens in numpy.
            """
            if not self._use_binding():
                return super()._gpt2_generate(
                    spk_cond, text_token_ids, emo_cond, max_mel_tokens=max_mel_tokens,
                    top_k=top_k, top_p=top_p, temperature=temperature,
                    repetition_penalty=repetition_penalty)

            import onnxruntime as ort

            # fp16 graphs declare fp16 conditioning inputs and hand back an fp16 latent,
            # while the rest of the pipeline (s2mel, sampling) is float32. Both crossings
            # are here, so this is the only place that has to know.
            ft = self._gpt_dtype()

            text_tokens = np.array([text_token_ids], dtype=np.int32)
            cond_lengths = np.array([spk_cond.shape[1]], dtype=np.int64)
            emo_cond_lengths = np.array([emo_cond.shape[1]], dtype=np.int64)

            n_pairs = (len(self.gpt2_step.get_outputs()) - 1) // 2

            def dev(a):
                return ort.OrtValue.ortvalue_from_numpy(a, "cuda", 0)

            # ---- prefill: keep every present_* on the device ----
            b = self.gpt2_init.io_binding()
            for name, arr in (("spk_cond", spk_cond.astype(ft)),
                              ("text_tokens", text_tokens),
                              ("emo_cond", emo_cond.astype(ft)),
                              ("cond_lengths", cond_lengths),
                              ("emo_cond_lengths", emo_cond_lengths)):
                b.bind_cpu_input(name, arr)
            b.bind_output("logits", "cpu")
            for i in range(n_pairs):
                b.bind_output(f"present_key.{i}", "cuda", 0)
                b.bind_output(f"present_value.{i}", "cuda", 0)
            self.gpt2_init.run_with_iobinding(b)
            outs = b.get_outputs()
            logits = outs[0].numpy()
            past = outs[1:]

            seq_len = past[0].shape()[2]
            mel_len = np.array(seq_len - 1, dtype=np.int64)

            generated = []
            cur_token = top_k_top_p_sample(
                logits[0, -1], top_k=top_k, top_p=top_p, temperature=temperature,
                repetition_penalty=repetition_penalty)
            generated.append(cur_token)

            # `past` are the previous binding's output buffers, so that binding has to stay
            # referenced until the run that consumes them is done — clearing or dropping it
            # first frees the device memory underneath and the next step sees rank-0 inputs.
            holder = b
            for _step in range(max_mel_tokens - 1):
                if cur_token == STOP_MEL_TOKEN:
                    break
                step_b = self.gpt2_step.io_binding()
                step_b.bind_cpu_input("input_ids", np.array([[cur_token]], dtype=np.int64))
                step_b.bind_cpu_input("attention_mask",
                                      np.ones((1, seq_len + 1), dtype=np.int64))
                step_b.bind_cpu_input("mel_len", mel_len)
                for i in range(n_pairs):
                    step_b.bind_ortvalue_input(f"past_key_values.{i}", past[i * 2])
                    step_b.bind_ortvalue_input(f"past_values.{i}", past[i * 2 + 1])
                step_b.bind_output("logits", "cpu")
                for i in range(n_pairs):
                    step_b.bind_output(f"present_key.{i}", "cuda", 0)
                    step_b.bind_output(f"present_value.{i}", "cuda", 0)
                self.gpt2_step.run_with_iobinding(step_b)
                outs = step_b.get_outputs()
                logits = outs[0].numpy()
                past = outs[1:]
                holder = step_b     # the run is done; the older buffers can go now
                seq_len += 1
                cur_token = top_k_top_p_sample(
                    logits[0, -1], top_k=top_k, top_p=top_p, temperature=temperature,
                    repetition_penalty=repetition_penalty,
                    generated_tokens=np.array(generated))
                generated.append(cur_token)

            mel_codes = np.array([generated], dtype=np.int64)
            if mel_codes[0, -1] == STOP_MEL_TOKEN:
                mel_codes = mel_codes[:, :-1]

            latent = self.gpt2_forward.run(None, {
                "spk_cond": spk_cond.astype(ft),
                "text_tokens": text_tokens,
                "mel_codes": mel_codes,
                "emo_cond": emo_cond.astype(ft),
                "cond_lengths": cond_lengths,
                "emo_cond_lengths": emo_cond_lengths,
            })[0]
            return mel_codes, latent.astype(np.float32)

    return Engine(args.model, num_threads=args.threads)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if bool(args.srt) == bool(args.text):
        print("ERROR: pass exactly one of --srt or --text", file=sys.stderr)
        return 2
    for unsupported in ("speed", "precision"):
        if getattr(args, unsupported) is not None:
            print(f"[indextts2-onnx] WARN: --{unsupported.replace('_', '-')} is not supported "
                  f"by the ONNX engine; ignoring", file=sys.stderr)
    emo_ref = None
    if args.emo_ref:
        emo_ref = Path(args.emo_ref).expanduser().resolve()
        if not emo_ref.is_file():
            print(f"ERROR: emotion reference not found: {emo_ref}", file=sys.stderr)
            return 1

    ref = Path(args.ref).expanduser().resolve()
    if not ref.is_file():
        print(f"ERROR: reference voice not found: {ref}", file=sys.stderr)
        return 1
    model_dir = Path(args.model).expanduser().resolve()
    if not model_dir.is_dir():
        print(f"ERROR: model dir not found: {model_dir}", file=sys.stderr)
        return 1

    if args.srt:
        srt_path = Path(args.srt).expanduser().resolve()
        if not srt_path.is_file():
            print(f"ERROR: srt not found: {srt_path}", file=sys.stderr)
            return 1
        entries = parse_srt(srt_path)
        if not entries:
            print(f"ERROR: no cues parsed from {srt_path}", file=sys.stderr)
            return 1
        out_dir = Path(args.out).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = srt_path.stem
        jobs = [(n, text, out_dir / f"{stem}_{n:03d}.wav") for n, text in entries]
    else:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        jobs = [(1, args.text, out_path)]

    workers = max(1, args.workers)
    if args.threads:
        per_worker_threads = args.threads
    else:
        per_worker_threads = max(1, (os.cpu_count() or 1) // workers)
    placements = make_session_factory(args.providers, per_worker_threads)

    import numpy as np
    import soundfile as sf

    t_load = time.perf_counter()
    engine = build_engine(args, emo_ref)
    print(f"[indextts2-onnx] loaded {len(placements)} graphs in "
          f"{time.perf_counter() - t_load:.1f}s (--providers {args.providers}, "
          f"{workers} worker(s) x {per_worker_threads} thread(s))")
    for name, ep in placements:
        print(f"[indextts2-onnx]   {name:28s} {ep}")

    pending: list[tuple[int, int, str, Path]] = []
    for i, (num, text, dest) in enumerate(jobs, 1):
        if args.resume and dest.is_file():
            print(f"[indextts2-onnx] [{i}/{len(jobs)}] skip {dest.name} (exists)")
        else:
            pending.append((i, num, text, dest))

    if args.seed is not None:
        np.random.seed(args.seed)
        if workers > 1:
            print("[indextts2-onnx] WARN: --seed is process-wide with --workers > 1; "
                  "pass --workers 1 for per-cue reproducibility", file=sys.stderr)

    # Warm the reference conditioning once, on this thread: it is cached on the engine and
    # every worker then reads it. Doing it concurrently would run w2v-BERT N times.
    if pending:
        engine._prepare_reference(str(ref))

    lock = threading.Lock()
    results: list[tuple[int, str, float, float]] = []

    def run_cue(job: tuple[int, int, str, Path]) -> None:
        i, num, text, dest = job
        if args.seed is not None and workers == 1:
            np.random.seed(args.seed + num)
        t0 = time.perf_counter()
        engine.infer(
            str(ref), text, str(dest),
            max_mel_tokens=args.max_mel_tokens,
            top_k=args.top_k, top_p=args.top_p, temperature=args.temperature,
            n_steps=args.steps,
        )
        elapsed = time.perf_counter() - t0
        secs = sf.info(str(dest)).duration
        with lock:
            results.append((i, dest.name, secs, elapsed))
            print(f"[indextts2-onnx] [{len(results)}/{len(pending)}] {dest.name} "
                  f"{secs:.2f}s audio in {elapsed:.1f}s "
                  f"(RTF {elapsed / max(secs, 1e-6):.2f})")

    t_all = time.perf_counter()
    if workers == 1:
        for job in pending:
            run_cue(job)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(run_cue, pending):
                pass

    wall = time.perf_counter() - t_all
    total_audio = sum(r[2] for r in results)
    if total_audio:
        print(f"[indextts2-onnx] {len(results)} cue(s), {total_audio:.1f}s audio in "
              f"{wall:.1f}s (RTF {wall / total_audio:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
