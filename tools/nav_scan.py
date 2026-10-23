#!/usr/bin/env python3
"""Scan every NAV pack of every cell and report where highlight info changes mid-cell.

    python nav_scan.py <VIDEO_TS> [--only VTS08,...] [--json out.json]

For each cell, reads only the NAV pack of each VOBU (addresses from the VOBU_ADMAP),
decodes the PCI highlight info (status, highlight/button time windows, colour schemes,
every button's colour index / rectangle / udlr / auto / command), and compares
consecutive VOBUs. Reports:
  * cells whose button set or colours change between VOBUs ("hli change")
  * VOBUs inside a button cell with hli_ss = 0 (no buttons there: a gap in the window)
  * highlight windows that start after / end before the cell (sub-cell timing)
"""
import argparse, json, struct, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dvd_cells import disc_cells, vob_set, VobReader, u16, u32


def admap(ifo, off):
    if not off:
        return []
    base = off * 2048
    end = u32(ifo, base)
    return [u32(ifo, base + 4 + 4 * i) for i in range((end - 3) // 4)]


def pci(p):
    if p[:4] != b"\x00\x00\x01\xba" or p[0x26:0x2A] != b"\x00\x00\x01\xbf":
        return None
    o = 0x2D
    s_ptm, e_ptm = u32(p, o + 12), u32(p, o + 16)
    h = o + 0x60
    hli_ss = u16(p, h) & 3
    hl = {"ss": hli_ss, "vobu": (s_ptm, e_ptm)}
    if hli_ss == 0:
        return hl
    hl["hl_s"], hl["hl_e"], hl["btn_se_e"] = u32(p, h + 2), u32(p, h + 6), u32(p, h + 10)
    hl["btn_md"], hl["ofn"], hl["ns"], hl["nsl"] = u16(p, h + 14), p[h + 16], p[h + 17], p[h + 18]
    hl["fosl"], hl["foac"] = p[h + 20], p[h + 21]
    hl["schemes"] = bytes(p[h + 22:h + 46]).hex()
    hl["buttons"] = bytes(p[h + 46:h + 46 + 18 * hl["ns"]]).hex()
    return hl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_ts")
    ap.add_argument("--only", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    vdir = Path(a.video_ts)
    cells = disc_cells(vdir)
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    ifos, maps, readers = {}, {}, {}

    def domain_map(dom, n):
        k = (dom, n)
        if k not in maps:
            if dom == "VMGM":
                ifo = (vdir / "VIDEO_TS.IFO").read_bytes(); off = u32(ifo, 0xDC)
            else:
                ifo = (vdir / f"VTS_{n:02d}_0.IFO").read_bytes()
                off = u32(ifo, 0xDC if dom == "VTSM" else 0xE4)
            maps[k] = sorted(admap(ifo, off))
            readers[k] = VobReader(vob_set(vdir, dom, n))
        return maps[k], readers[k]

    stats = Counter()
    report = defaultdict(list)
    import bisect
    for key in sorted(cells):
        dom, n, vob, cid = key
        folder = "VMGM" if dom == "VMGM" else f"{dom}{n:02d}"
        if only and folder not in only:
            continue
        c = cells[key]
        amap, rd = domain_map(dom, n)
        i0 = bisect.bisect_left(amap, c["first"])
        vobus = []
        while i0 < len(amap) and amap[i0] <= c["last"]:
            vobus.append(amap[i0]); i0 += 1
        navs = [pci(rd.read(s, 1)) for s in vobus]
        navs = [x for x in navs if x]
        stats["cells"] += 1
        stats["vobus"] += len(navs)
        if not any(x["ss"] for x in navs):
            continue
        stats["button cells"] += 1
        name = f"{folder}.V{vob:03d}C{cid:02d}"
        cell_s, cell_e = navs[0]["vobu"][0], navs[-1]["vobu"][1]
        gaps = [i for i, x in enumerate(navs) if x["ss"] == 0]
        if gaps:
            stats["cells with no-button VOBUs"] += 1
            report[name].append(f"no buttons in VOBU {gaps[0]+1}..{gaps[-1]+1} of {len(navs)}"
                                f" ({sum(1 for _ in gaps)} VOBUs)")
        full = [x for x in navs if x["ss"] == 1]
        sigs = []
        for x in full:
            sig = (x["schemes"], x["buttons"], x["ns"], x["fosl"], x["foac"], x["btn_md"])
            if not sigs or sigs[-1] != sig:
                sigs.append(sig)
        if len(sigs) > 1:
            stats["cells with hli changes"] += 1
            what = []
            for a_, b_ in zip(sigs, sigs[1:]):
                parts = []
                if a_[0] != b_[0]: parts.append("colour schemes")
                if a_[2] != b_[2]: parts.append(f"button count {a_[2]}->{b_[2]}")
                elif a_[1] != b_[1]:
                    ba = [a_[1][i:i + 36] for i in range(0, len(a_[1]), 36)]
                    bb = [b_[1][i:i + 36] for i in range(0, len(b_[1]), 36)]
                    diff = [j + 1 for j, (p_, q_) in enumerate(zip(ba, bb)) if p_ != q_]
                    kinds = set()
                    for j in diff:
                        p_, q_ = bytes.fromhex(ba[j - 1]), bytes.fromhex(bb[j - 1])
                        if p_[0] >> 6 != q_[0] >> 6: kinds.add("colour index")
                        if p_[:6] != q_[:6] and (bytes([p_[0] & 0x3F]) + p_[1:6]) != (bytes([q_[0] & 0x3F]) + q_[1:6]): kinds.add("rect")
                        if p_[6:10] != q_[6:10]: kinds.add("udlr/auto")
                        if p_[10:18] != q_[10:18]: kinds.add("command")
                    parts.append(f"buttons {diff[:8]}{'...' if len(diff) > 8 else ''} ({', '.join(sorted(kinds))})")
                if a_[3:] != b_[3:]: parts.append("forced select/action or mode")
                what.append("; ".join(parts))
            report[name].append(f"{len(sigs)} different button sets: " + " | ".join(what))
        for x in full[:1]:
            hs = (x["hl_s"] - cell_s) / 90000
            he = (x["hl_e"] - cell_s) / 90000 if x["hl_e"] not in (0, 0xFFFFFFFF) else None
            be = (x["btn_se_e"] - cell_s) / 90000 if x["btn_se_e"] not in (0, 0xFFFFFFFF) else None
            dur = (cell_e - cell_s) / 90000
            if hs > 0.1:
                stats["highlight starts late"] += 1
                report[name].append(f"highlight starts {hs:.2f}s into a {dur:.2f}s cell")
            if be is not None and be < dur - 0.1:
                stats["buttons end early"] += 1
                report[name].append(f"buttons selectable until {be:.2f}s of {dur:.2f}s")
    print(dict(stats))
    for name, lines in sorted(report.items()):
        for l in lines:
            print(f"  {name}: {l}")
    if a.json:
        Path(a.json).write_text(json.dumps({"stats": stats, "report": report}, indent=1))


if __name__ == "__main__":
    main()
