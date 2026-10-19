#!/bin/bash
#
# install.sh — install the fresh XnuLoader into Mu-Silicium tree.
#
# 1. Copies XnuLoader.c + XnuLoader.inf into
#      $MUDIR/Platforms/Xiaomi/peridotPkg/XnuLoader/
# 2. Adds `peridotPkg/XnuLoader/XnuLoader.inf` to peridot.dsc [Components]
# 3. Replaces the old `FILE APPLICATION = 7A3B1C2D...` block in peridot.fdf
#    with `INF peridotPkg/XnuLoader/XnuLoader.inf` (same FILE GUID, so the
#    BootManagerLib FV hook keeps working).
#
# Usage: ./install.sh [MUDIR]
set -e

HERE="$(cd "$(dirname "$0")/.." && pwd)"       # DarwinXNU repo root
SRC="$HERE/mu/XnuLoader"                       # DarwinXNU/mu/XnuLoader
MUDIR="${1:-/home/hoshikava/mu-build/mu}"
PKG="$MUDIR/Platforms/Xiaomi/peridotPkg"

if [ ! -d "$PKG" ]; then
    echo "Mu-Silicium duty not found at: $MUDIR"
    echo "Usage: ./install.sh [MUDIR]"
    exit 1
fi

COMPONENT="peridotPkg/XnuLoader/XnuLoader.inf"
OLD_BLOCK="FILE APPLICATION = 7A3B1C2D-4E5F-4061-8234-9A0B1C2D3E4F"

echo "[install] Copying loader sources into $PKG/XnuLoader/"
mkdir -p "$PKG/XnuLoader"
cp -v "$SRC/XnuLoader.c" "$PKG/XnuLoader/XnuLoader.c"
cp -v "$SRC/XnuLoader.inf" "$PKG/XnuLoader/XnuLoader.inf"

#
# 2. peridot.dsc — add the INF to [Components] (idempotent).
#
if ! grep -q "$COMPONENT" "$PKG/peridot.dsc"; then
    echo "[install] Adding $COMPONENT to peridot.dsc [Components]"
    python3 - "$PKG/peridot.dsc" <<'PYEOF'
import sys

path = sys.argv[1]
src = open(path).read()
component = "peridotPkg/XnuLoader/XnuLoader.inf"
anchor = "  peridot/AcpiTables.inf"
assert anchor in src
src = src.replace(anchor, anchor + "\n\n  #\n  # XNU (Darwin) Loader\n  #\n  " + component, 1)
open(path, "w").write(src)
print("  [install] peridot.dsc updated")
PYEOF
else
    echo "[install] peridot.dsc already has the component"
fi

#
# 3. peridot.fdf — replace the embedded prebuilt EFI with the INF source.
#
if grep -q "SECTION PE32 = peridotPkg/XnuLoader/BOOTAA64.EFI" "$PKG/peridot.fdf"; then
    echo "[install] Switching peridot.fdf from prebuilt BOOTAA64.EFI to INF"
    python3 - "$PKG/peridot.fdf" <<'PYEOF'
import sys

path = sys.argv[1]
src = open(path).read()
old = """  # XNU (Darwin) loader - embedded UEFI application, launched by BootManagerLib.
  FILE APPLICATION = 7A3B1C2D-4E5F-4061-8234-9A0B1C2D3E4F {
    SECTION PE32 = peridotPkg/XnuLoader/BOOTAA64.EFI
  }
"""
new = """  # XNU (Darwin) loader - built from source in this package, embedded UEFI
  # application, launched by BootManagerLib. FILE_GUID =
  # 7A3B1C2D-4E5F-4061-8234-9A0B1C2D3E4F (must match BootManager.c).
  INF peridotPkg/XnuLoader/XnuLoader.inf
"""
assert old in src
src = src.replace(old, new, 1)
open(path, "w").write(src)
print("  [install] peridot.fdf updated")
PYEOF
else
    if grep -q "INF $COMPONENT" "$PKG/peridot.fdf"; then
        echo "[install] peridot.fdf already uses the INF"
    else
        echo "[install] WARNING: neither prebuilt block nor INF found in peridot.fdf"
    fi
fi

echo "[install] Done. Build with:"
echo "    cd $MUDIR && ./build_uefi.py -d peridot -r RELEASE -c -m 0"
