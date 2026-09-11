#!/usr/bin/env bash
# LGPL ffmpeg + ffprobe for macOS arm64, from the release tarball (ADR-008).
#
# No ready-made LGPL static build exists for Apple Silicon (Martin Riedl's
# carry x264/x265, which makes them GPL). MyScribe needs only FFmpeg's own
# codecs - any decoder, pcm_s16le and aac encoders, volumedetect, the
# matroska/mp4/null muxers - so a build with no external libraries covers it.
# --disable-autodetect keeps it hermetic: nothing from Homebrew or the
# runner's image gets linked, so the binaries only need libSystem.
#
#   packaging/build_ffmpeg_macos.sh <tarball> <sha256> <out-dir>
set -euo pipefail

tarball=$1
expected=$2
out=$3

actual=$(shasum -a 256 "$tarball" | awk '{print $1}')
if [ "$actual" != "$expected" ]; then
  echo "ffmpeg source checksum mismatch: expected $expected, got $actual" >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -xf "$tarball" -C "$work"
cd "$work"/ffmpeg-*

./configure \
  --prefix="$work/prefix" \
  --arch=arm64 --target-os=darwin \
  --extra-cflags="-mmacosx-version-min=12.0" \
  --extra-ldflags="-mmacosx-version-min=12.0" \
  --disable-autodetect \
  --disable-shared --enable-static \
  --disable-ffplay --disable-doc --disable-debug \
  --disable-network \
  --enable-pic
make -j"$(sysctl -n hw.ncpu)"

mkdir -p "$out"
cp ffmpeg ffprobe "$out"/
cp COPYING.LGPLv2.1 "$out"/ffmpeg-LICENSE.txt
"$out"/ffmpeg -hide_banner -L | grep -q "Lesser General Public" \
  || { echo "built ffmpeg is not LGPL" >&2; exit 1; }
otool -L "$out"/ffmpeg
