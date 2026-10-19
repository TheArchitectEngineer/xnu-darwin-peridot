// hfsroot builds the small HFS+ root image served from the ram disk
// (XNUROOT1 tail block -> /chosen/memory-map/RAMDisk -> md0 -> rd=md0).
// It must contain at least /sbin/launchd (tools/peridot-init.S assembled by
// tools/mkinit.py) and /dev: bsd_init() mounts devfs at /dev with
// kernel_mount(), which namei()s the path on the freshly mounted root, so a
// missing /dev makes the devfs mount (and therefore open("/dev/console")
// in init) fail with ENOENT.
//
// Build inside a clone of github.com/go-filesystems/hfsplus (its pure-Go
// HFS+ writer, Mkfs + MkDir + WriteFile) as cmd/hfsroot, e.g.:
//
//	git clone https://github.com/go-filesystems/hfsplus
//	mkdir -p hfsplus/cmd/hfsroot && cp main.go hfsplus/cmd/hfsroot/
//	cd hfsplus && GOTOOLCHAIN=go1.27.1 go build ./cmd/hfsroot
//	./hfsroot root.img 8388608 peridot-init.bin
package main

import (
	"fmt"
	"os"

	hfs "github.com/go-filesystems/hfsplus"
)

func main() {
	if len(os.Args) != 4 {
		fmt.Fprintln(os.Stderr, "usage: hfsroot OUT.img SIZE_BYTES INIT_BINARY")
		os.Exit(2)
	}
	out, size, initPath := os.Args[1], os.Args[2], os.Args[3]

	var sizeBytes int64
	if _, err := fmt.Sscan(size, &sizeBytes); err != nil {
		fmt.Fprintln(os.Stderr, "bad size:", err)
		os.Exit(2)
	}
	initData, err := os.ReadFile(initPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	img, err := hfs.Mkfs(sizeBytes, hfs.FormatConfig{Label: "PeridotRoot", CaseSensitive: true})
	if err != nil {
		fmt.Fprintln(os.Stderr, "mkfs:", err)
		os.Exit(1)
	}
	if err := os.WriteFile(out, img, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	v, err := hfs.OpenFileWritable(out)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open:", err)
		os.Exit(1)
	}
	if err := v.MkDir("/dev", 0o755); err != nil {
		fmt.Fprintln(os.Stderr, "mkdir /dev:", err)
		os.Exit(1)
	}
	if err := v.MkDir("/sbin", 0o755); err != nil {
		fmt.Fprintln(os.Stderr, "mkdir:", err)
		os.Exit(1)
	}
	if err := v.WriteFile("/sbin/launchd", initData, 0o755); err != nil {
		fmt.Fprintln(os.Stderr, "write:", err)
		os.Exit(1)
	}
	if err := v.Sync(); err != nil {
		fmt.Fprintln(os.Stderr, "sync:", err)
		os.Exit(1)
	}
	if err := v.Close(); err != nil {
		fmt.Fprintln(os.Stderr, "close:", err)
		os.Exit(1)
	}
	fmt.Printf("wrote %s: %d bytes, /sbin/launchd %d bytes\n", out, sizeBytes, len(initData))
}
