#!/usr/bin/env python3
"""Re-export IndexTTS-2's GPT-2 stack to fp16 ONNX so it can run on the GPU.

Why this exists
---------------
`indextts-onnx` publishes its GPT-2 graphs as `quantize_dynamic` int8, i.e. `MatMulInteger`.
ONNX Runtime's CUDA EP has no kernel for that op, so those graphs are stuck on the CPU — and
the autoregressive loop is ~94% of a cue's wall clock. Everything else in the stack already
runs on CUDA. Re-exporting just `gpt2_{init,step,forward}` in fp16 is what moves that last
94% onto the GPU.

PyTorch is needed **here and only here**. This is a one-off, offline step; the inference side
(`indextts2_onnx_batch.py`) stays torch-free, which is the whole point of the ONNX engine.

What it does
------------
Loads IndexTTS-2 through the upstream `indextts` package (v2.0.0 — the version these
checkpoints and `indextts_onnx.export` were written against) and calls only the two GPT-2
exporters, skipping the w2v-BERT / s2mel / BigVGAN ones: the published int8 graphs for those
are either already on CUDA (`dit_step`, `bigvgan` ship fp32) or run once per job and cost
nothing. Nothing is quantized.

Precision variants
------------------
The graphs are always traced from torch in **fp32** — that is the exporters' tested path, and
`torch.onnx.export` on a half model is the fiddlier one. fp16 is then produced by converting
the ONNX, which also decides what the graph's *boundary* dtype is:

    fp32   traced output, fp32 weights and I/O. Peaks at ~13.8 GB of VRAM on this deck —
           workable on a 16 GB card but with no headroom.
    fp16   fp16 weights and I/O; ~8.7 GB peak and a few percent faster. The worker feeds and
           reads fp16 at the GPT-2 boundary (see `_gpt_dtype` in indextts2_onnx_batch.py).

`fp16-io32` (fp16 weights, fp32 I/O via `keep_io_types=True`) is also selectable but does not
load: the converter leaves MatMul operands mixed (`Type parameter (T) of Optype (MatMul) bound
to different types`). It was worth trying only because it would have needed no driver change;
plain fp16 does the job with a few casts, so it is not in the default set.

Export both defaults and measure; they cost only disk. Point the worker at one dir at a time
with `INDEXTTS_ONNX_MODELS` — `scripts/remote/use_gpt2_variant.sh` assembles them.

Usage:
    export_gpt2_fp16.py --checkpoint-dir <dir> --output-dir <dir> [--variants fp32,fp16]

Then drop one variant's graphs into the worker's model dir and remove the int8 GPT-2 files —
the loader prefers `<name>_int8.onnx` whenever it exists, and `--providers auto` routes
anything *not* named `_int8` to CUDA, so the swap needs no code change:

    cp <out>/<variant>/gpt2_{init,step,forward}.onnx*  <models>/
    rm <models>/gpt2_{init,step,forward}_int8.onnx
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import types
from pathlib import Path


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="export_gpt2_fp16.py", add_help=True)
    p.add_argument("--checkpoint-dir", required=True,
                   help="IndexTTS-2 checkpoints (config.yaml, gpt.pth, s2mel.pth, …)")
    p.add_argument("--output-dir", required=True, help="where the .onnx files land")
    p.add_argument("--device", default="cuda:0",
                   help="torch device the model is traced on (default cuda:0)")
    p.add_argument("--variants", default="fp32,fp16",
                   help="comma-separated subset of fp32,fp16,fp16-io32 (see module docstring; "
                        "fp16-io32 is kept selectable but does not currently load)")
    p.add_argument("--reuse-fp32", action="store_true",
                   help="skip the torch export and convert from an existing <output-dir>/fp32. "
                        "Loading IndexTTS-2 costs minutes; iterating on the fp16 conversion "
                        "does not need it.")
    p.add_argument("--opset", type=int, default=17)
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    ckpt = Path(args.checkpoint_dir).expanduser().resolve()
    out = Path(args.output_dir).expanduser().resolve()
    cfg_path = ckpt / "config.yaml"
    if not cfg_path.is_file():
        print(f"ERROR: {cfg_path} not found — is --checkpoint-dir right?", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)

    # infer_v2 pins HF_HUB_CACHE to './checkpoints/hf_cache' at import time, so run from the
    # checkpoint dir's parent and let the auxiliary weights (w2v-BERT, MaskGCT, CAMPPlus,
    # BigVGAN) land beside the rest instead of in the home cache.
    os.chdir(ckpt.parent)
    os.environ.setdefault("HF_HUB_CACHE", str(ckpt / "hf_cache"))

    try:
        import indextts.infer_v2 as infer_mod
    except ImportError as exc:
        print(f"ERROR: the `indextts` package is not importable ({exc}).", file=sys.stderr)
        print("       Install index-tts v2.0.0 into this venv "
              "(git clone --branch v2.0.0 …; uv pip install -e .)", file=sys.stderr)
        return 1

    # QwenEmotion only serves text→emotion prompting, which no exported graph touches.
    # Stubbing it out saves loading a 1.2 GB LLM for nothing.
    def _noop_qwen_init(self, model_dir):
        self.model_dir = model_dir
        self.model = None
        self.tokenizer = None
    infer_mod.QwenEmotion.__init__ = _noop_qwen_init
    sys.modules.setdefault("modelscope", types.ModuleType("modelscope"))

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = set(variants) - {"fp32", "fp16-io32", "fp16"}
    if unknown:
        print(f"ERROR: unknown variant(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    fp32_dir = out / "fp32"
    fp32_dir.mkdir(parents=True, exist_ok=True)

    if args.reuse_fp32:
        missing = [n for n in ("gpt2_init", "gpt2_step", "gpt2_forward")
                   if not (fp32_dir / f"{n}.onnx").is_file()]
        if missing:
            print(f"ERROR: --reuse-fp32 but {fp32_dir} lacks: {', '.join(missing)}",
                  file=sys.stderr)
            return 1
        print(f"[export] reusing {fp32_dir}")
        report(fp32_dir)
        return convert_variants(variants, out, fp32_dir)

    from indextts.infer_v2 import IndexTTS2
    from indextts_onnx.export.export_gpt2 import export_gpt2, export_gpt2_autoregressive

    print(f"[export] loading IndexTTS-2 on {args.device} …")
    t0 = time.perf_counter()
    tts = IndexTTS2(cfg_path=str(cfg_path), model_dir=str(ckpt), device=args.device,
                    use_fp16=False, use_cuda_kernel=False)
    print(f"[export] loaded in {time.perf_counter() - t0:.1f}s")

    print("[export] gpt2_forward + conditioning_encoder …")
    t0 = time.perf_counter()
    export_gpt2(tts, str(fp32_dir), opset=args.opset)
    print(f"[export]   {time.perf_counter() - t0:.1f}s")

    print("[export] gpt2_init + gpt2_step (KV cache as explicit I/O) …")
    t0 = time.perf_counter()
    export_gpt2_autoregressive(tts, str(fp32_dir), opset=args.opset)
    print(f"[export]   {time.perf_counter() - t0:.1f}s")
    report(fp32_dir)

    return convert_variants(variants, out, fp32_dir)


def convert_variants(variants: list[str], out: Path, fp32_dir: Path) -> int:
    # conditioning_encoder is exported for completeness but the ONNX driver never loads it
    # (its MODEL_NAMES has no such entry), so there is nothing to gain by converting it.
    targets = ["gpt2_init", "gpt2_step", "gpt2_forward"]
    for variant in variants:
        if variant == "fp32":
            continue
        keep_io = variant == "fp16-io32"
        vdir = out / variant
        vdir.mkdir(parents=True, exist_ok=True)
        print(f"[export] converting to {variant} (keep_io_types={keep_io}) …")
        for name in targets:
            src = fp32_dir / f"{name}.onnx"
            if not src.is_file():
                print(f"[export] WARN: {src.name} missing; skipping", file=sys.stderr)
                continue
            t0 = time.perf_counter()
            fixed = convert_to_fp16(src, vdir / f"{name}.onnx", keep_io)
            print(f"[export]   {name} in {time.perf_counter() - t0:.1f}s "
                  f"({fixed} Cast node(s) repaired)")
        report(vdir)
    return 0


def repair_fp16_casts(model) -> int:
    """Point every Cast node at the dtype the converted graph says it produces.

    onnxconverter-common rewrites a pre-existing Cast's *declared output type* to float16
    but leaves the node's own `to` attribute on float32, so the model fails to load with
    "Type (tensor(float16)) ... does not match expected type (tensor(float))". These are
    the casts the traced attention blocks use around masking/softmax — 54 of 137 in
    gpt2_init. Realigning `to` with the declaration is what the converter meant to do.
    """
    declared = {v.name: v.type.tensor_type.elem_type for v in model.graph.value_info}
    for o in model.graph.output:
        declared.setdefault(o.name, o.type.tensor_type.elem_type)

    fixed = 0
    for node in model.graph.node:
        if node.op_type != "Cast":
            continue
        want = declared.get(node.output[0])
        if want is None:
            continue
        for attr in node.attribute:
            if attr.name == "to" and attr.i != want:
                attr.i = want
                fixed += 1
    return fixed


def convert_to_fp16(src: Path, dest: Path, keep_io_types: bool) -> int:
    """fp32 ONNX → fp16, saved with external data (these graphs exceed protobuf's 2 GB)."""
    import onnx
    from onnxconverter_common.float16 import convert_float_to_float16_model_path

    # The *_model_path variant is the one that can shape-infer a >2 GB model.
    model = convert_float_to_float16_model_path(str(src), keep_io_types=keep_io_types)
    fixed = repair_fp16_casts(model)
    for stale in (dest, dest.with_suffix(".onnx.data")):
        stale.unlink(missing_ok=True)
    onnx.save_model(model, str(dest), save_as_external_data=True,
                    all_tensors_to_one_file=True, location=dest.name + ".data",
                    size_threshold=1024)
    return fixed


def report(d: Path) -> None:
    produced = sorted(d.glob("*.onnx"))
    print(f"[export] {d}: {len(produced)} graph(s)")
    for f in produced:
        # External-data blobs sit beside the .onnx; report the pair's real footprint.
        blob = f.with_suffix(".onnx.data")
        size = f.stat().st_size + (blob.stat().st_size if blob.is_file() else 0)
        print(f"[export]   {f.name:28s} {size / 1e6:8.0f} MB")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
