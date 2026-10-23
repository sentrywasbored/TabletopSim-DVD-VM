#!/usr/bin/env python3
"""Full-read scan of every cell's subpicture data.

    python spu_scan.py <VIDEO_TS> [--only VMGM,VTSM01,VTS05,...]

Per cell and stream: how many SPU units, whether later units differ from the first
(mid-cell subpicture changes), display timing (delays / stop commands), and any
CHG_COLCON (per-region colour/contrast) commands.
"""
import argparse, hashlib, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dvd_cells import disc_cells, vob_set, VobReader, cue_path
from dvd_spu import demux_spu, decode_spu


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_ts")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    vdir = Path(a.video_ts)
    cells = disc_cells(vdir)
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    readers, stats, notes = {}, Counter(), defaultdict(list)
    uniq = set()
    for key in sorted(cells):
        folder = cue_path(key).parts[0]
        if only and folder not in only:
            continue
        c = cells[key]
        rk = key[:2]
        rd = readers.setdefault(rk, VobReader(vob_set(vdir, *rk)))
        spus = demux_spu(rd.read(c["first"], c["last"] - c["first"] + 1))
        stats["cells"] += 1
        if spus:
            stats["cells with SPU"] += 1
        name = ".".join(cue_path(key).with_suffix("").parts)
        for st, units in spus.items():
            stats[f"stream {st} cells"] += 1
            hashes = [hashlib.md5(u[1]).hexdigest() for u in units]
            uniq.update(hashes)
            if len(units) > 1:
                stats["multi-unit streams"] += 1
                if len(set(hashes)) > 1:
                    stats["mid-cell SPU changes"] += 1
                    notes[name].append(f"stream {st}: {len(units)} units, {len(set(hashes))} different")
            for pts, buf in units:
                info = decode_spu(buf)
                seqs = info["seqs"]
                if any(q.get("chg_colcon") for q in seqs):
                    stats["CHG_COLCON"] += 1
                    notes[name].append(f"stream {st}: CHG_COLCON")
                if any(q.get("stop") for q in seqs) or len(seqs) > 1:
                    stats["timed display (stop/delay)"] += 1
                    notes[name].append(f"stream {st}: " + ", ".join(
                        f"{q['delay']:.2f}s:" + "/".join(k for k in ("forced", "start", "stop") if q.get(k)) for q in seqs))
                if not any(q.get("forced") for q in seqs):
                    stats["not forced"] += 1
                    notes[name].append(f"stream {st}: not forced (needs display flag)")
                if info.get("contrast") and any(info["contrast"]):
                    stats["visible normal layer"] += 1
    stats["unique SPU units"] = len(uniq)
    print(dict(stats))
    for name, ls in sorted(notes.items()):
        for l in sorted(set(ls)):
            print(f"  {name}: {l}")


if __name__ == "__main__":
    main()
