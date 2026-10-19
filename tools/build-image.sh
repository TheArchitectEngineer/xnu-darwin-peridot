#!/bin/bash
#
# build-image.sh — reproduce Mu-peridot.img end to end from this repo.
#
#   tools/build-image.sh [--mu MUDIR] [--kernel KERNEL] [--root ROOT.IMG]
#                        [--no-root] [--skip-build] [--skip-check]
#
# Steps:
#   1. mu/install.sh          — copy the XnuLoader sources into the Mu tree
#   2. tools/mkadt.py --ramdisk — regenerate the peridot ADT (RAMDisk placeholder)
#   3. stage kernel + ADT + ram disk under MUDIR/Resources/Xnu/
#   4. MUDIR/build_uefi.py -d peridot — build the UEFI FD + tail blocks
#   5. tools/check_payload.py — verify XNUKERN1 / XNUFDT01 / XNUROOT1
#
# Defaults: MUDIR=$HOME/mu-build/mu (or $MUDIR), KERNEL=kernel.development.peridot
# from the CI artifact download dir, ROOT=/tmp/opencode/peridot-root.img build
# by tools/hfsroot (see its header; /dev directory is required).
#
# The UEFI toolchain needs the Android NDK clang and the Mu virtualenv; both
# are auto-detected, override with $ANDROID_NDK / $VENV.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"

MUDIR="${MUDIR:-/home/hoshikava/mu-build/mu}"
KERNEL="${KERNEL:-/home/hoshikava/Downloads/xnu-kernel-art/kernel.development.peridot}"
ROOT="${ROOT:-/tmp/opencode/peridot-root.img}"
ANDROID_NDK="${ANDROID_NDK:-/opt/android-ndk}"
VENV="${VENV:-$(dirname "$MUDIR")/venv}"
RAMDISK=1
DO_BUILD=1
DO_CHECK=1

while [ $# -gt 0 ]; do
    case "$1" in
        --mu)         MUDIR=$2; shift 2 ;;
        --kernel)     KERNEL=$2; shift 2 ;;
        --root)       ROOT=$2; shift 2 ;;
        --no-root)    RAMDISK=0; shift ;;
        --skip-build) DO_BUILD=0; shift ;;
        --skip-check) DO_CHECK=0; shift ;;
        -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
        *)            echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

NDK_BIN="$ANDROID_NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
[ -d "$MUDIR" ]  || { echo "Mu tree not found: $MUDIR" >&2; exit 1; }
[ -x "$NDK_BIN/clang" ] || { echo "NDK clang not found under $ANDROID_NDK" >&2; exit 1; }
[ -f "$KERNEL" ] || { echo "kernel not found: $KERNEL" >&2; exit 1; }
if [ "$RAMDISK" = 1 ] && [ ! -f "$ROOT" ]; then
    echo "ram disk not found: $ROOT (build it with tools/hfsroot, or --no-root)" >&2
    exit 1
fi

echo "[1/5] installing XnuLoader into $MUDIR"
"$HERE/mu/install.sh" "$MUDIR"

# build_uefi.py appends the ram disk only when peridot.toml names it; toggle
# the key so --no-root produces a kernel+ADT-only image.
python3 - "$MUDIR/Resources/Configs/peridot.toml" "$RAMDISK" <<'PY'
import sys
path, on = sys.argv[1], sys.argv[2] == '1'
src = open(path).read()
line_on = 'xnu_ramdisk        = "Resources/Xnu/root.img"'
line_off = '# xnu_ramdisk      = "Resources/Xnu/root.img"'
src = src.replace(line_off, line_on) if on else src.replace(line_on, line_off)
open(path, 'w').write(src)
PY

echo "[2/5] generating ADT"
if [ "$RAMDISK" = 1 ]; then
    python3 "$HERE/tools/mkadt.py" "$MUDIR/Resources/Xnu/sm8635.dt" --ramdisk
else
    python3 "$HERE/tools/mkadt.py" "$MUDIR/Resources/Xnu/sm8635.dt"
fi

echo "[3/5] staging kernel, ADT and ram disk"
cp "$KERNEL" "$MUDIR/Resources/Xnu/kernel.bin"
[ "$RAMDISK" = 1 ] && cp "$ROOT" "$MUDIR/Resources/Xnu/root.img"
ls -l "$MUDIR/Resources/Xnu/"

if [ "$DO_BUILD" = 1 ]; then
    echo "[4/5] building Mu image (build_uefi.py -d peridot)"
    (
        cd "$MUDIR"
        # shellcheck disable=SC1091
        [ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"
        export PATH="$NDK_BIN:$PATH"
        python build_uefi.py -d peridot
    )
else
    echo "[4/5] skipped (--skip-build)"
fi

if [ "$DO_CHECK" = 1 ]; then
    echo "[5/5] verifying payload"
    FD=$(ls "$MUDIR"/Build/peridotPkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd-bootshim)
    if [ "$RAMDISK" = 1 ]; then
        python3 "$HERE/tools/check_payload.py" "$FD" \
            --kernel "$KERNEL" --adt "$MUDIR/Resources/Xnu/sm8635.dt" --root "$ROOT"
    else
        python3 "$HERE/tools/check_payload.py" "$FD" \
            --kernel "$KERNEL" --adt "$MUDIR/Resources/Xnu/sm8635.dt"
    fi
else
    echo "[5/5] skipped (--skip-check)"
fi

md5sum "$MUDIR/Mu-peridot.img"
echo "[build-image] done: $MUDIR/Mu-peridot.img"
