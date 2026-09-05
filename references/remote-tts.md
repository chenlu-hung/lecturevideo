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

`--emo-ref`, `--speed` and `--precision` have no equivalent in that engine. The worker accepts
and warns about them rather than failing, so `synthesize_tts.py` can forward its flags verbatim.

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

fp16 buys little speed once the transfer is gone (6.7 → 6.2 ms per token; what is left is
per-run overhead, not weight bandwidth) but it halves VRAM, and 13.8 GB of a 16 GB card leaves
no headroom for a long cue. **fp16 is the default** — `~/bin/indextts2-batch` prefers
`models-fp16`, then `models-fp32`, then the stock int8 `models`.

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
