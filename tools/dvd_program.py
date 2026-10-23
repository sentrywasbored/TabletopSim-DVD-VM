#!/usr/bin/env python3
"""
Export a DVD's navigation program -- every PGC, its VM commands, cells and
buttons -- as a compact line-based text file for the Lua DVD VM (dvdvm.lua).
The VM runs the disc's own command bytes, so game logic is not hand-ported.

    python dvd_program.py <disc folder> <out.txt> [--name "24"]

<disc folder> holds VIDEO_TS\\ (IFOs + VOBs). Buttons come from the disc itself: the
highlight information (PCI) in each cell's first NAV pack -- rectangles, arrow-key
neighbours, auto-action flag, command, forced select/action and the highlight time
window (made relative to the cell's clip start). Only the first NAV pack is read;
nav_scan.py checks that no disc changes its buttons within a cell.

Format (one record per line, fields separated by single spaces; '-' = empty):

  D <name> <nvts>
  T <tt> <vts> <ttn>                                    title -> VTS / VTS_TTN
  R <vts> <ttn> <pgcn>.<pgn> <pgcn>.<pgn> ...           PTT table of one VTS title
  G <key> <entry> <next> <prev> <goup> <still> <mode> <progmap> <pre> <post> <cellcmds> <cells> <subp>
      key      FP | M0.<pgcn> (VMGM) | M<vts>.<pgcn> (VTSM) | V<vts>.<pgcn> (titles)
      entry    menu type for entry PGCs: 2 Title 3 Root 4 Subpicture 5 Audio 6 Angle 7 PTT, else 0
      still    PGC still time (255 = infinite)      mode  playback mode byte (0 = sequential)
      progmap  comma list: first cell of each program
      pre/post/cellcmds  concatenated 16-hex-digit commands, '-' if none
      cells    comma list of  <cue>:<still>:<cellcmd>:<seconds>
      subp     available subpicture streams, logical=physical (4:3), comma list, or '-' 
  B <cue> <fsel> <fact> <hlstart> <hlend> <btn>;<btn>;...
      fsel/fact  forced select / forced action button (0 = none)
      hlstart/hlend  button window in seconds from the start of the cell's clip
                     (-1 = whole cell)
      btn  <n>,<x0>,<y0>,<x1>,<y1>,<up>,<down>,<left>,<right>,<auto>,<cmdhex>
           coordinates in 720x480 source pixels
"""
import argparse, re, struct, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dvd_cells import (u16, u32, ptime, pgcit, vob_set, cue_path, VobReader, nav_ptm)


def cmds(d, o):
    co = u16(d, o + 0xE4)
    if not co:
        return "", "", ""
    npre, npost, ncc = u16(d, o + co), u16(d, o + co + 2), u16(d, o + co + 4)
    p, out = o + co + 8, []
    for n in (npre, npost, ncc):
        out.append(d[p:p + 8 * n].hex())
        p += 8 * n
    return tuple(out)


def pgc_record(d, o, dom, vts, entry):
    nprog, ncell = d[o + 2], d[o + 3]
    pre, post, cc = cmds(d, o)
    pm = u16(d, o + 0xE6)
    progmap = [d[o + pm + i] for i in range(nprog)] if pm else []
    cells = []
    cp, cpos = u16(d, o + 0xE8), u16(d, o + 0xEA)
    for i in range(ncell):
        e, q = o + cp + 24 * i, o + cpos + 4 * i
        key = (dom, vts, u16(d, q), d[q + 3])
        cue = ".".join(cue_path(key).with_suffix("").parts)
        cells.append((cue, d[e + 2], d[e + 3], ptime(d[e + 4:e + 8]), key, u32(d, e + 8)))
    rec = [entry, u16(d, o + 0x9C), u16(d, o + 0x9E), u16(d, o + 0xA0), d[o + 0xA2], d[o + 0xA3],
           ",".join(map(str, progmap)) or "-", pre or "-", post or "-", cc or "-",
           ",".join(f"{c[0]}:{c[1]}:{c[2]}:{c[3]:.3f}" for c in cells) or "-",
           subp_map(d, o)]
    return rec, cells


def subp_map(d, o):
    """PGC subpicture stream control (0x1C, 32 x 4 bytes): logical stream -> physical
    stream for 4:3 video, only for entries flagged available. '-' if none."""
    out = []
    for i in range(32):
        v = u32(d, o + 0x1C + 4 * i)
        if v & 0x80000000:
            out.append(f"{i}={(v >> 24) & 0x1F}")
    return ",".join(out) or "-"


def pci_buttons(pack):
    """Buttons from a NAV pack's PCI highlight info (button group 1), or None."""
    if len(pack) < 2048 or pack[0x26:0x2A] != b"\x00\x00\x01\xbf":
        return None
    hli = 0x2D + 0x60
    if u16(pack, hli) & 3 == 0:                      # hli_ss 0: no highlight info here
        return None
    info = {"hs": u32(pack, hli + 2), "he": u32(pack, hli + 6),
            "fsel": pack[hli + 20], "fact": pack[hli + 21], "buttons": []}
    for i in range(pack[hli + 17]):                  # btn_ns: buttons in group 1
        b = pack[hli + 46 + 18 * i: hli + 64 + 18 * i]
        info["buttons"].append({
            "n": i + 1,
            "x0": ((b[0] & 0x3F) << 4) | (b[1] >> 4), "x1": ((b[1] & 3) << 8) | b[2],
            "y0": ((b[3] & 0x3F) << 4) | (b[4] >> 4), "y1": ((b[4] & 3) << 8) | b[5],
            "auto": b[3] >> 6,
            "udlr": (b[6] & 0x3F, b[7] & 0x3F, b[8] & 0x3F, b[9] & 0x3F),
            "cmd": b[10:18].hex(),
        })
    return info if info["buttons"] else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("disc")
    ap.add_argument("out")
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    disc = Path(a.disc)
    vdir = disc / "VIDEO_TS"

    lines, allcells = [], {}
    d = (vdir / "VIDEO_TS.IFO").read_bytes()
    nvts = u16(d, 0x3E)
    lines.append(f"D {a.name or disc.name.replace(' ', '_')} {nvts}")
    tt = u32(d, 0xC4) * 2048
    for i in range(u16(d, tt)):
        e = tt + 8 + 12 * i
        lines.append(f"T {i + 1} {d[e + 6]} {d[e + 7]}")

    def emit(key, d, o, dom, vts, entry):
        rec, cells = pgc_record(d, o, dom, vts, entry)
        lines.append(f"G {key} " + " ".join(map(str, rec)))
        for c in cells:
            allcells.setdefault(c[0], c)

    emit("FP", d, u32(d, 0x84), "VMGM", 0, 0)
    if u32(d, 0xC8):
        base = u32(d, 0xC8) * 2048
        pg = 0
        for lu in range(u16(d, base)):
            lub = base + u32(d, base + 8 + 8 * lu + 4)
            for j in range(u16(d, lub)):
                cat = d[lub + 8 + 8 * j]
                o = lub + u32(d, lub + 8 + 8 * j + 4)
                pg += 1
                emit(f"M0.{pg}", d, o, "VMGM", 0, cat & 0x0F if cat & 0x80 else 0)
            break                                   # first language unit only
    for n in range(1, nvts + 1):
        f = vdir / f"VTS_{n:02d}_0.IFO"
        if not f.exists():
            continue
        v = f.read_bytes()
        pt = u32(v, 0xC8) * 2048
        nttn = u16(v, pt)
        offs = [u32(v, pt + 8 + 4 * i) for i in range(nttn)]
        end = u32(v, pt + 4) + 1
        for i, off in enumerate(offs):
            stop = offs[i + 1] if i + 1 < nttn else end
            ptts = [f"{u16(v, pt + x)}.{u16(v, pt + x + 2)}" for x in range(off, stop, 4)]
            lines.append(f"R {n} {i + 1} " + " ".join(ptts))
        if u32(v, 0xD0):
            base = u32(v, 0xD0) * 2048
            lub = base + u32(v, base + 8 + 4)
            for j in range(u16(v, lub)):
                cat = v[lub + 8 + 8 * j]
                o = lub + u32(v, lub + 8 + 8 * j + 4)
                emit(f"M{n}.{j + 1}", v, o, "VTSM", n, cat & 0x0F if cat & 0x80 else 0)
        base = u32(v, 0xCC) * 2048
        for j, o in enumerate(pgcit(v, base), 1):
            emit(f"V{n}.{j}", v, o, "VTS", n, 0)

    # buttons, from each cell's first NAV pack, highlight window relative to the clip start
    readers, nb = {}, 0
    for cue, (_, still, cmd, secs, key, first) in sorted(allcells.items()):
        dom, vts, vob, cell = key
        rk = (dom, vts)
        if rk not in readers:
            readers[rk] = VobReader(vob_set(vdir, dom, vts))
        info = pci_buttons(readers[rk].read(first, 1))
        if not info:
            continue
        ptm = nav_ptm(readers[rk], first)

        def rel(t):
            if not ptm or t in (0, 0xFFFFFFFF):
                return -1
            return round((t - ptm[0]) / 90000, 3)

        hs, he = rel(info["hs"]), rel(info["he"])
        if he != -1 and he >= secs - 0.05:
            he = -1
        if hs != -1 and hs <= 0.05:
            hs = -1
        btns = []
        for b in info["buttons"]:
            u, dn, l, r = b["udlr"]
            cmdhex = b["cmd"] if b["cmd"] != "0" * 16 else "-"
            btns.append(f"{b['n']},{b['x0']},{b['y0']},{b['x1']},{b['y1']},{u},{dn},{l},{r},"
                        f"{1 if b['auto'] else 0},{cmdhex}")
            nb += 1
        lines.append(f"B {cue} {info['fsel']} {info['fact']} {hs} {he} " + ";".join(btns))

    Path(a.out).write_text("\n".join(lines) + "\n")
    g = sum(1 for x in lines if x[0] == "G")
    print(f"{g} PGCs, {len(allcells)} cells, {nb} buttons -> {a.out} "
          f"({Path(a.out).stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
