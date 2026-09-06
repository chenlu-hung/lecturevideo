#!/usr/bin/env bash
# One-off, run ON the remote: install what remote_render.py needs beyond the TTS
# worker — Node, a Chrome binary, ffmpeg, and an `opencc` command.
#
#   ssh <host> 'bash -s' < scripts/remote/setup_render_host.sh
#
# Everything lands under $HOME/.local, so no root is required. That matters: the
# box this was built for has no passwordless sudo, which rules out apt for all
# four dependencies (Ubuntu's chromium is a snap, and its opencc needs a package).
#
# NOT installed here: the CJK font the player draws captions in. Captions are live
# HTML text rendered by the remote's Chrome, so a remote-rendered video whose font
# differs from a locally-rendered one is visibly inconsistent — same deck, two
# different caption faces. Copy the local font over instead:
#
#   rsync <mac>:/System/Library/AssetsV2/com_apple_MobileAsset_Font7/*/AssetData/PingFang.ttc \
#         <mac>:/System/Library/Fonts/HelveticaNeue.ttc  <host>:~/.fonts/
#   ssh <host> fc-cache -f ~/.fonts
#
# (`fc-match "PingFang TC"` on the remote should then name PingFang.ttc, not a
# fallback.) Ship only fonts you are licensed to run on that machine.
set -euo pipefail

NODE_VERSION="${NODE_VERSION:-v22.23.2}"
CHROME_VERSION="${CHROME_VERSION:-152.0.7977.82}"
PREFIX="$HOME/.local"
SRC="$PREFIX/src"

mkdir -p "$PREFIX/bin" "$SRC" "$HOME/.fonts"
cd "$SRC"

echo "=== Node $NODE_VERSION ==="
if [ ! -x "$PREFIX/node-$NODE_VERSION/bin/node" ]; then
  curl -fsSL -o node.tar.xz \
    "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-x64.tar.xz"
  tar xf node.tar.xz && rm node.tar.xz
  rm -rf "$PREFIX/node-$NODE_VERSION"
  mv "node-$NODE_VERSION-linux-x64" "$PREFIX/node-$NODE_VERSION"
fi
ln -sf "$PREFIX/node-$NODE_VERSION/bin/node" "$PREFIX/bin/node"
"$PREFIX/bin/node" --version

echo "=== ffmpeg (BtbN static, NVENC) ==="
# The distro build would do, but this one is static (no library hunt) and carries
# the NVENC encoders, which the export can use on a box that has the GPU anyway.
if [ ! -x "$PREFIX/bin/ffmpeg" ]; then
  curl -fsSL -o ff.tar.xz \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
  tar xf ff.tar.xz && rm ff.tar.xz
  d="ffmpeg-master-latest-linux64-gpl"
  cp "$d/bin/ffmpeg" "$d/bin/ffprobe" "$PREFIX/bin/"
  rm -rf "$d"
fi
"$PREFIX/bin/ffmpeg" -version | head -1
echo "NVENC encoders: $("$PREFIX/bin/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -c nvenc)"

echo "=== Chrome for Testing $CHROME_VERSION ==="
# The full browser, not chrome-headless-shell: export_mp4.mjs drives it with
# --headless=new over the DevTools protocol.
if [ ! -x "$PREFIX/chrome-linux64/chrome" ]; then
  curl -fsSL -o chrome.zip \
    "https://storage.googleapis.com/chrome-for-testing-public/$CHROME_VERSION/linux64/chrome-linux64.zip"
  unzip -q -o chrome.zip && rm chrome.zip
  rm -rf "$PREFIX/chrome-linux64" && mv chrome-linux64 "$PREFIX/"
fi
missing=$(ldd "$PREFIX/chrome-linux64/chrome" 2>/dev/null | grep -c "not found" || true)
if [ "$missing" != "0" ]; then
  echo "WARN: Chrome is missing $missing shared libraries:" >&2
  ldd "$PREFIX/chrome-linux64/chrome" | grep "not found" >&2
  echo "      Those need a system package, i.e. root." >&2
fi
"$PREFIX/chrome-linux64/chrome" --headless=new --disable-gpu --dump-dom \
  --virtual-time-budget=3000 "data:text/html,<b>ok</b>" >/dev/null 2>&1 \
  && echo "chrome headless: ok" || { echo "ERROR: chrome cannot run headless" >&2; exit 1; }

echo "=== opencc ==="
# synthesize_tts.py shells out to an `opencc` binary to fold Traditional to
# Simplified (IndexTTS-2's tokenizer is Simplified-only). Ubuntu's binary needs
# root to install; the Python module is pip-installable as a user, so wrap it.
if ! python3 -c "import opencc" 2>/dev/null; then
  python3 -m pip install --user --quiet opencc-python-reimplemented
fi
cat > "$PREFIX/bin/opencc" <<'PY'
#!/usr/bin/env python3
"""CLI shim over the opencc Python module (`opencc -c t2s`, text on stdin)."""
import sys
import opencc

args = sys.argv[1:]
cfg = "t2s"
if "-c" in args:
    cfg = args[args.index("-c") + 1]
cfg = cfg[:-5] if cfg.endswith(".json") else cfg
sys.stdout.write(opencc.OpenCC(cfg).convert(sys.stdin.read()))
PY
chmod +x "$PREFIX/bin/opencc"
# Line count must survive: synthesize_tts.py pairs converted lines back to cues.
n_in=3
n_out=$(printf '甲\n乙\n丙\n' | "$PREFIX/bin/opencc" -c t2s | wc -l)
[ "$n_out" = "$n_in" ] && echo "opencc shim: ok" \
  || { echo "ERROR: opencc shim changed the line count ($n_in -> $n_out)" >&2; exit 1; }

echo
echo "Installed under $PREFIX. remote_render.py's defaults point at:"
echo "  --remote-node    $PREFIX/bin/node"
echo "  --remote-chrome  $PREFIX/chrome-linux64/chrome"
echo "  --remote-ffmpeg  $PREFIX/bin/ffmpeg"
echo "Still needed: the IndexTTS-2 worker (~/bin/indextts2-batch) and the caption font."
