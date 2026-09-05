# Remote narration (`--remote-host`)

Phase 4b normally calls the local IndexTTS-2 MLX binary. `--remote-host` moves that one call
to another machine over ssh + rsync and brings the per-cue wavs back; everything else in
`synthesize_tts.py` — the combined SRT, the Traditional→Simplified conversion, the concat, the
audio-accurate `timeline.json`, the captions — runs locally and is unchanged. A remote run and
a local run therefore produce the same `timeline.json` shape.

```bash
python3 scripts/synthesize_tts.py output/<slug> --ref voice.wav --remote-host my-4080
```

`$LECTUREVIDEO_TTS_HOST` supplies the default host, so the flag can be dropped once it is set.

## What crosses the wire

| direction | payload |
|---|---|
| up | `.tts_segments/combined.srt`, the reference wav (and `--emo-ref` if given), and — unless `--no-push-worker` — `scripts/remote/indextts2_onnx_batch.py` |
| down | `combined_<NNN>.wav`, one per cue, into `<slug>/.tts_segments/` |

Nothing runs between jobs: no daemon, no port, no state on the remote beyond the job directory
under `--remote-dir` (default `~/lecturevideo-tts/jobs/<slug>`). An interrupted run leaves the
finished wavs on **both** ends, so `--remote-resume` (remote skips what it has) and
`--skip-synth` (local skips synthesis entirely) both pick up where it stopped. Only pass
`--remote-resume` when the narration text has not changed — cue *numbers*, not text, decide
what counts as "already done".

The worker is pushed from this checkout on every run by default, so the remote copy can never
drift from the repo. `--no-push-worker` turns that off; `--remote-worker` says where it lands.

## The remote engine

`scripts/remote/indextts2_onnx_batch.py` is a drop-in stand-in for the MLX binary: same
`--ref` / `--srt` / `--out` flags, same `<srt-stem>_<NNN>.wav` output naming, same
1 ch / 16-bit / 22.05 kHz wavs. It is driven by
[`indextts-onnx`](https://github.com/vra/indextts-onnx) — IndexTTS-2 exported to ten ONNX
graphs and run through ONNX Runtime + numpy, **with no PyTorch at inference time**.

`--speed` and `--precision` have no equivalent in that engine. The worker accepts and warns
about them rather than failing, so `synthesize_tts.py` can forward its flags verbatim.

`--emo-ref` does work, though `indextts-onnx` itself does not offer it. `gpt2_init` and
`gpt2_forward` take `emo_cond` as a real, separately-shaped input ([1, emo_len, 1024] of
w2v-BERT hidden states) and run it through the GPT's own emotion perceiver; upstream's driver
just assigns the *speaker's* embedding to it, so every voice speaks in its reference's affect.
The worker computes a second wav's embedding instead. Verified: no `--emo-ref` and
`--emo-ref <the reference itself>` are bit-identical, a different wav changes the output.
IndexTTS-2's other two emotion controls — an 8-dim vector via `emo_matrix.npy`, and text-prompt
emotion via the QwenEmotion LLM — need weights this export does not ship.

### Execution-provider policy

The published graphs are a mix. `dit_step` and `bigvgan` are fp32; the GPT-2 stack, w2v-BERT,
CAMPPlus, the semantic codec and both s2mel graphs ship int8 from `quantize_dynamic`, i.e.
`MatMulInteger`. ONNX Runtime's CUDA EP has **no MatMulInteger kernel**, so an int8 graph placed
on CUDA runs its matmuls on the CPU anyway *and* pays hundreds of device copies per AR step.
`--providers auto` (the default) therefore routes by dtype: `_int8` → CPU, fp32/fp16 → CUDA.
That rule is why re-exported GPT-2 graphs need no code change to take effect — they simply are
not named `_int8`.

### The KV cache round trip

Even with the GPT-2 graphs on CUDA, the AR loop stayed at ~30 ms per token. It was not compute:
the exported `gpt2_step` takes `past_key_values.*` in and returns `present_*` out as ordinary
tensors, so upstream's numpy loop copies the whole cache to the device and back **every token**.
This model is 24 layers x 20 heads x 64, i.e. ~245 KB per token per direction — by 500 tokens
that is ~250 MB of PCIe traffic per step, which accounts for ~25 of those 30 ms.

`--io-binding` (default `auto`, on whenever `gpt2_step` landed on CUDA) rebinds each step's
`present_*` straight onto the next step's `past_*` as device tensors, so only `logits` — a few
KB, needed by the numpy sampler — returns to the host. Per token: 29.6 ms → 6.7 ms. Output is
**bit-identical** to the unbound path at the same seed; it is purely a memory-placement change.

Two details that bite: the binding owns the buffers `get_outputs()` hands back, so the previous
binding has to stay referenced until the run that consumes it finishes (clearing or reusing one
binding frees the cache underneath and the next step sees rank-0 inputs); and `--io-binding off`
cannot drive fp16 graphs, because the upstream loop feeds float32.

### Numbers

Measured on my-4080 (RTX 4080 16 GB, 20-core CPU), three Chinese lecture cues, ~9 s each:

| | GPT-2 AR | DiT (10 steps) | BigVGAN | RTF | peak VRAM |
|---|---|---|---|---|---|
| local MLX Release (Apple Silicon) | — | — | — | 8.4 | — |
| everything CPU, int8 | 20.7 s | 19.4 s | 37.3 s | 8.2 | — |
| everything CUDA, int8 | 25.6 s | 0.70 s | 0.29 s | 3.2 | — |
| `auto`: int8 CPU + fp32 CUDA | 20.7 s | 0.70 s | 0.29 s | 2.06 | — |
| \+ GPT-2 re-exported fp32, on CUDA | 15.6 s | 0.55 s | 0.22 s | 1.49 | 13.8 GB |
| \+ `--io-binding` | 3.5 s | 0.54 s | 0.22 s | **0.43** | 13.8 GB |
| \+ fp16 GPT-2 | 2.8 s | 0.48 s | 0.20 s | **0.42** | **8.7 GB** |

fp16 buys little speed once the transfer is gone (6.7 → 6.2 ms per token) but it halves VRAM,
and 13.8 GB of a 16 GB card leaves no headroom for a long cue. **fp16 is the default** —
`~/bin/indextts2-batch` prefers `models-fp16`, then `models-fp32`, then the stock int8 `models`.

### Where the remaining 6 ms per token goes

Not the Python bindings: rebuilding the ~100 bind calls for a step costs 0.09 ms of the
5.42 ms measured at a 450-token cache, i.e. 2%. Not weight bandwidth either — 512M fp16
params is ~1 GB, about 1.4 ms at the card's ~717 GB/s, and halving the bytes from fp32 only
bought 8%. And barely the cache: growing it from 1 to 900 tokens (0.1 MB to 110 MB) moves a
step from 4.92 to 5.64 ms. **~4.9 ms is fixed cost, independent of the work done.**

It is dispatch. `gpt2_step` is 5154 nodes, still 2361 after ORT's own optimizer — and only
~200 of those are arithmetic (96 Gemm, 49 MatMul, 50 LayerNormalization). The rest is the
shape plumbing a TorchScript trace leaves behind when a dimension is dynamic: 377 Unsqueeze,
301 Gather, 296 Reshape, 270 Concat, 177 Shape. At ~2 us of dispatch each, 2361 nodes is
~4.7 ms — the fixed cost, accounted for.

**bf16 would therefore not help.** Ada's tensor cores run bf16 and fp16 at the same rate and
the two are the same size, so it changes neither of the things that are costing time. (The
"bf16 is 7x slower" note in the MLX port is an Apple-Silicon fact — the M1 GPU emulates it —
and does not transfer.)

### Making the step static: measured, not shipped

Two things follow from "the cost is per-node dispatch": fold the shape plumbing away, and
stop paying CPU dispatch at all via CUDA Graphs. Both need static shapes — but *not* a
re-export, as it first appeared. `onnx-simplifier` with every input pinned
(`past_key_values.*` → `[1,20,MAX,64]`, `attention_mask` → `[1,MAX+1]`) constant-folds the
graph from 5154 nodes to **962**, all of them arithmetic, and ORT will then capture a CUDA
graph — which it refuses on the dynamic graph, because the shape ops fall back to CPU
("25 Memcpy nodes ... cannot use the graph capture feature").

The loop this implies: a fixed cache holding the real tokens **right-aligned**, zeros in
`attention_mask` over the left padding, and each step's `present_*` sliced back to `MAX`.
Position survives it — `GPT2StepWrapper` computes `pos_idx = attention_mask.shape[1] -
mel_len`, and with the shape frozen, `mel_len` is a runtime input that can carry the
position instead. Checked against the dynamic graph on the same state: logits correlate at
0.999974 with the same argmax (the residual is fp16 accumulation over the padded length).

| | ms/step | vs shipped |
|---|---|---|
| dynamic + `--io-binding` (shipped) | 5.60 | — |
| static, no CUDA graph | 4.26 | 1.31x |
| static + CUDA graph, MAX=1600 | 3.83 | 1.46x |
| static + CUDA graph, MAX=1024 | 3.53 | **1.59x** |

MAX is the new tradeoff: the step reads and writes the whole padded cache every token, so
1600 costs 197 MB where 1024 costs 126 MB, and a cue needs roughly 450 (prefill) + its token
count. End to end that is RTF 0.42 → ~0.27.

**Not shipped.** 1.55x end to end would buy a second AR loop to keep correct alongside the
first, a hard MAX ceiling needing a fallback when a cue overruns it, a ~1 GB static graph per
MAX bucket (6 minutes of onnxsim each), and output that is close to but not identical to what
ships today. Against the 4.5x that `--io-binding` cost ~60 lines, that is a poor trade — but
the artifacts are on my-4080 under `onnx-fp16/fp16/gpt2_step_static*.onnx` if it is ever
worth revisiting. Real kernel fusion would be the better lever: ORT's `optimize_model` fuses
FastGelu (24) and SkipLayerNormalization (49) here but matches **no** Attention pattern,
because these are custom traced wrappers rather than a stock HF GPT-2 export.

`--workers N` synthesizes N cues concurrently against shared sessions (the reference
conditioning is computed once and reused). It was worth ~1.2× on the int8 CPU path and is not
useful once the AR loop is on the GPU. `--seed` is process-wide once N > 1; pass `--workers 1`
for per-cue reproducibility.

## Re-exporting the GPT-2 graphs

`scripts/remote/setup_gpt2_fp16.sh` provisions the one-off export environment and runs
`export_gpt2_fp16.py`; `use_gpt2_variant.sh` assembles a model dir pairing one variant with the
shared graphs. **PyTorch is installed for this step and nowhere else** — inference stays
torch-free, which is the whole point of the ONNX engine.

The graphs are traced fp32 (the exporters' tested path) and converted afterwards. Three things
had to be worked around, all recorded in those scripts:

- **protobuf.** `onnx` drags protobuf past 4.x, which refuses index-tts's pinned tensorboard 2.9
  `_pb2` modules (reached via `descript-audiotools` → `torch.utils.tensorboard`). Upgrading
  tensorboard fixes it; forcing the pure-python protobuf backend would also work but would make
  saving multi-GB ONNX painfully slow. That upgrade then pulls numpy 2.x, which breaks
  index-tts's matplotlib, so `numpy==1.26.2` goes back last.
- **Cast nodes.** `onnxconverter-common` rewrites a pre-existing Cast's declared *output type*
  to float16 but leaves the node's own `to` attribute on float32 — 54 of 137 casts in
  `gpt2_init`. The model then fails to load with `Type (tensor(float16)) ... does not match
  expected type (tensor(float))`. `repair_fp16_casts` realigns `to` with the declaration.
- **`uv sync` stalls.** A single quiet connection hung it for over an hour (71 s of CPU across
  that time). It runs under `UV_HTTP_TIMEOUT` and a retry loop now; the cache makes a retry
  resume rather than restart.

## Setting up a remote box

What was done on my-4080 (Ubuntu 22.04, CUDA 13.0, driver 595, no root):

```bash
# 1. Python 3.11+ without touching the system (indextts-onnx requires >= 3.11)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12

# 2. A virtualenvwrapper env, as on the rest of that machine
mkvirtualenv -p "$(uv python find 3.12)" indextts-onnx

# 3. The engine. onnxruntime-gpu 1.29 targets CUDA 13 and brings its own CUDA/cuDNN
#    through the nvidia-* wheels, so the system toolkit is not involved.
pip install indextts-onnx
pip uninstall -y onnxruntime
pip install "onnxruntime-gpu[cuda,cudnn]"

# 4. Models (~3.3 GB) from HuggingFace yunfengwang/indextts-onnx into
#    $INDEXTTS_ONNX_MODELS, else ~/.cache/indextts-onnx/models

# 5. The GPT-2 re-export (see below) — ~30 GB and one PyTorch install, worth 5x
bash scripts/remote/setup_gpt2_fp16.sh /data/lecturevideo-tts
bash scripts/remote/use_gpt2_variant.sh fp16 /data/lecturevideo-tts

# 6. The launcher this repo's default --remote-cmd points at
install -m755 scripts/remote/indextts2-batch.sh ~/bin/indextts2-batch
```

The launcher exists only to pin the venv and the model dir so the ssh command line stays a
single word; `INDEXTTS_ONNX_VENV` / `INDEXTTS_ONNX_WORKER` / `INDEXTTS_ONNX_MODELS` override
each path. It prefers `/data/lecturevideo-tts/models` when that directory exists.
