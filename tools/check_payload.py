#!/usr/bin/env python3
"""Verify the XnuLoader tail blocks in a Mu-Silicium bootshim FD.

The Mu build appends the XNU payload after the UEFI FD as a sequence of
16-aligned blocks:

    "XNUKERN1" | u64 size | kernel Mach-O
    "XNUFDT01" | u64 size | Apple device tree (ADT)
    "XNUROOT1" | u64 size | HFS+ ram disk (optional)

Usage:  check_payload.py FDBOOTSHIM [--kernel FILE] [--adt FILE] [--root FILE]

Checks each block's 16-byte alignment, ordering, bounds and magic (Mach-O /
HFS+ for kernel / ram disk) and, when the source file is given, that its
SHA-256 matches the block.  Exits non-zero on any failure.
"""
import argparse
import hashlib
import struct
import sys

MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")
HFS_SIGNS = (b"H+", b"HX")

BLOCKS = {
    b"XNUKERN1": "kernel",
    b"XNUFDT01": "adt",
    b"XNUROOT1": "ram disk",
}


def find_block(data, magic):
    i = data.find(magic)
    if i < 0:
        return None
    if i + 16 > len(data):
        return None
    size = struct.unpack_from("<Q", data, i + 8)[0]
    return i, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bootshim")
    ap.add_argument("--kernel")
    ap.add_argument("--adt")
    ap.add_argument("--root")
    args = ap.parse_args()

    data = open(args.bootshim, "rb").read()
    print(f"{args.bootshim}: {len(data)} bytes")

    sources = {"kernel": args.kernel, "adt": args.adt, "ram disk": args.root}
    fails = []
    last_end = 0
    found = {}

    for magic, what in ((b"XNUKERN1", "kernel"), (b"XNUFDT01", "adt"),
                        (b"XNUROOT1", "ram disk")):
        r = find_block(data, magic)
        if r is None:
            if what != "ram disk":          # optional block
                fails.append(f"{what}: {magic.decode()} block not found")
                print(f"  {what:8s} MISSING")
            else:
                print(f"  {what:8s} absent (no ram disk in this image)")
            continue
        off, size = r
        found[what] = (off, size)
        payload = data[off + 16:off + 16 + size]
        ok = True

        if off % 16 != 0:
            fails.append(f"{what}: block at 0x{off:x} is not 16-aligned")
            ok = False
        if off < last_end:
            fails.append(f"{what}: block at 0x{off:x} overlaps previous block")
            ok = False
        if off + 16 + size > len(data):
            fails.append(f"{what}: block at 0x{off:x} runs past EOF")
            ok = False
        last_end = off + 16 + size

        if what == "kernel" and payload[:4] not in MACHO_MAGICS:
            fails.append(f"{what}: not a Mach-O (magic {payload[:4].hex()})")
            ok = False
        if what == "ram disk" and size > 1024 + 2:
            sig = payload[1024:1026]
            if sig not in HFS_SIGNS:
                fails.append(f"{what}: no HFS+ volume header (sig {sig!r})")
                ok = False

        digest = hashlib.sha256(payload).hexdigest()
        src = sources[what]
        if src:
            want = hashlib.sha256(open(src, "rb").read()).hexdigest()
            if want != digest:
                fails.append(f"{what}: sha256 != {src}")
                ok = False
            note = f"  (matches {src})" if ok else f"  (!= {src})"
        else:
            note = ""

        print(f"  {what:8s} @0x{off:x} size {size} sha256 {digest[:16]}..."
              f" {'OK' if ok else 'FAIL'}{note}")

    if args.root and "ram disk" not in found:
        print("  note: --root given but the image has no XNUROOT1 block")

    if fails:
        print("FAILED:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("payload OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
