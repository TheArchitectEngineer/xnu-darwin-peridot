#!/usr/bin/env python3
"""Build the minimal Apple Device Tree that XNU needs to boot on peridot.

peridot is the SM8635 (Redmi Turbo 3 / POCO F6) mainline-Qualcomm board;
every MMIO address below comes from the live FDT shipped with the device
(Resources/DTBs/peridot.dts).  Only what the early boot path actually reads
is modelled.  Usage:  mkadt.py OUT.adt [--dump]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Kernel-managed RAM: the XnuLoader asks UEFI for a block this size and
# passes the chosen base/size in boot_args; it rewrites these two properties
# before handoff when the allocation differs (keep them in sync for other
# consumers).
PHYS_BASE = 0x8C0000000
MEM_SIZE = 0x38000000
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

# From the peridot FDT:
#   interrupt-controller@17100000 reg = <0x17100000 0x10000 0x17180000 0x200000>
#   redistributor-stride = 0x40000; #redistributor-regions = 1
GICD, GICD_SIZE = 0x17100000, 0x10000
GICR, GICR_SIZE = 0x17180000, 0x200000
#   splash_region reg = <0x00 0xe3940000 0x00 0x2b00000>
FB, FB_SIZE = 0xE3940000, 0x2B00000
#   panel: 1220x2712, Fv_active row bytes 0x1310 (4880)
FB_W, FB_H, FB_STRIDE = 1220, 2712, 1220 * 4
#   timer clock-frequency = 0x124f800 (19.2 MHz)
TIMEBASE = 19200000
#   timer interrupts = <PPI 13> <PPI 14> <PPI 11> <PPI 12>: virtual = 16+11
VTIMER_INTID = 27
#   ufshc@1d84000 "ufs_mem" (host ABL leaves running)
UFS, UFS_SIZE = 0x01d84000, 0x3000

PERIDOT_ECID = 0x50455249444F5401  # 'PERIDOT' + 1, synthetic

BOOT_MANIFEST_HASH = hashlib.sha384(b'peridot boot manifest').digest()

# The iOS userland is the iPhone X (iPhone10,3 / D22AP) IPSW.  MobileGestalt
# derives product type, device class, UI layout, artwork etc. from the root
# identity and /product of the real D22AP device tree.  Extract it from the
# IPSW with tools/remotezip.py + `ipsw img4 im4p extract`; optional.
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """/product properties of the real D22AP tree, without the syscfg
    placeholders iBoot would fill in (flag bit set) and node bookkeeping."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None):
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(PHYS_BASE)),
        ('dram-size', u64(MEM_SIZE)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('peridot-shim')),
        ('system-firmware-version', cstr('peridot-shim')),
        # iBoot's SHA-384 of the boot manifest; /sbin/mount -P 1 exits unless
        # it exists. Fixed stand-in value.
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # libimg4 (cryptexd) aborts "failed to get crypto algorithm" without
        # it; real A11+ device trees say sha2-384.
        ('crypto-hash-method', cstr('sha2-384')),
        # MobileGestalt screen class; pixel size comes from IOMobileFramebuffer.
        # 1220x2712 @3x = 407x904 points (iPhone X class presentation).
        ('display-scale', u32(3)),
        # IOPlatformUUID source (gethostuuid).  Synthetic ECID (not the
        # phone's); chip-id of the A11 that the iPhone X userland expects.
        ('unique-chip-id', struct.pack('<Q', PERIDOT_ECID)),
        ('chip-id', u32(0x8015)),
        # Dev-fused, undemoted board (tuple 0,0,1,1): mobileactivationd takes
        # its native "Hactivation" branch (no SEP, no baseband on Linux
        # phones anyway).
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        # SIP relaxed like `csrutil disable` (lp-sip0) + unauthenticated root
        # (lp-sip1): the iOS shared cache is in no trust cache.
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # PERIDOT SMP: the eight Kryo cores live at MPIDR Aff1 = 0x0..0x7 with
    # Aff0 = 0 (cpu@0..cpu@700 in the live FDT), which is exactly the linear
    # scheme the kernel expects.  One homogeneous cluster; cpus=N selects an
    # initial prefix (default 1, see machine_routines.c).
    cpu_nodes = []
    for core in range(8):
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo') + cstr('ARM,v8')),
            ('reg', u32(core << 8)),  # actual MPIDR Aff1:Aff0, NOT logical ID
            ('cpu-id', u32(core)),   # descriptive: XNU assigns IDs by DT order
            ('cluster-id', u32(0)),
            ('cluster-type', u32(0)),  # homogeneous CLUSTER_TYPE_SMP
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),  # distinct per-core L2 nodes in live FDT
            ('l3-cache-id', u32(0)),     # shared L3
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(TIMEBASE)),
            ('clock-frequency', u32(2803000000 if core == 7 else 2400000000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(3200000000)),
            ('peripheral-frequency', u32(TIMEBASE)),
            ('fixed-frequency', u32(TIMEBASE)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    # SOC_BASE-relative GIC regs, exactly like /arm-io children on real
    # devices (pe_fiq.c maps soc_base_phys + prop, find_gicr_pe_base walks
    # gicr_size in GICR_PE_SIZE steps).
    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(VTIMER_INTID)),
        ('reg', u64(GICD - SOC_BASE, GICD_SIZE, GICR - SOC_BASE, GICR_SIZE)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('peridot-io')),
        ('compatible', cstr('peridot-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        # watchdogd checks IODeviceTree:/arm-io/wdt for no-pmu.
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        # Main display: IOMobileFramebuffer counts /arm-io children with this
        # device_type and reads the presented canvas size from the node.
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('peridot,display')),
            ('canvas-width', u32(1125)),
            ('canvas-height', u32(2436)),
        ])])

    # pe_serial.c fbcon console: reg + geometry of the loader framebuffer.
    fbcon = node('peridot-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_STRIDE)),
    ])

    # bsd/dev/sweet_ufs.c: the UFS host ABL leaves running.
    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    # pmap_compute_max_asids requires the node even when no overrides are set.
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    # MobileGestalt HomeButtonType = IODeviceTree:/buttons home-button-type
    # (D22AP: 2, no Home button).
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    product = [node('product', [ph()] + product_props)] if product_props else []

    # Identity of the iPhone X whose userland runs here: compatible[0] is
    # hw.model/hw.target (D22AP), model is hw.machine/hw.product (iPhone10,3).
    # 'xiaomi,peridot' is what PeridotPlatformExpert matches on.
    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,peridot') + cstr('qcom,sm8635') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + [ufs])


if __name__ == '__main__':
    root = build()
    blob = adt.serialize(root)
    open(sys.argv[1], 'wb').write(blob)
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(f'{sys.argv[1]}: {len(blob)} bytes', file=sys.stderr)
