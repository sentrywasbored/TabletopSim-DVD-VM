#!/usr/bin/env python3
"""
Check a cell dump before building bundles from it.

    python check_dump.py <VIDEO_TS> <Content folder> <name>_program.txt [--tails all|N|0]

1. Coverage: every cue the program can play has a clip; no leftover .part.mp4 files.
2. Length: every clip is within 0.1 s of its cell's playback time from the IFO.
3. Tail audio (the bug that cost the 24 port hours): on a seamless cell join the
   disc stores the end of a cell's audio in the NEXT cell's sectors. A dumper that
   only reads the cell's own sectors pads that stretch with silence, and you hear a
   gap at every join. For each cell whose timeline continues into the following
   sectors, the clip's last 300 ms must contain sound -- unless the disc itself is
   silent there (fade-outs), which is checked from the VOB.
   --tails all checks every such cell (default), N a random sample, 0 skips it.

Prints a summary; exit code 1 if anything needs fixing. Needs ffmpeg/ffprobe on PATH.
"""
import argparse, array, random, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dvd_cells import disc_cells, vob_set, VobReader, nav_ptm, cue_path, tail_sectors

QUIET = 330          # |sample| below this (about -40 dBFS) counts as silence
GAP_MS = 120         # trailing silence longer than this is suspicious


def duration(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(f)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def pcm(args, data=None):
    r = subprocess.run(["ffmpeg", "-v", "error", *args, "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                       input=data, capture_output=True)
    a = array.array("h")
    a.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
    return a


def trailing_quiet_ms(samples):
    n = 0
    for v in reversed(samples):
        if abs(v) >= QUIET:
            break
        n += 1
    return n / 48


def disc_is_quiet_at_end(rd, cell):
    """Is the disc's own audio silent in the last 300 ms of the cell?"""
    s, e = nav_ptm(rd, cell["first"]), nav_ptm(rd, cell["lastvobu"])
    if not (s and e):
        return False
    data = rd.read(cell["first"], cell["last"] - cell["first"] + 1 + tail_sectors(rd, cell))
    t1 = e[1] / 90000
    x = pcm(["-copyts", "-f", "mpeg", "-i", "-", "-map", "0:a:0",
             "-af", f"atrim=start={max(0, t1 - 0.3):.4f}:end={t1:.4f}"], data)
    return len(x) > 0 and max(abs(v) for v in x) < QUIET * 3


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video_ts")
    ap.add_argument("content")
    ap.add_argument("program")
    ap.add_argument("--tails", default="all")
    a = ap.parse_args()
    vdir, content = Path(a.video_ts), Path(a.content)
    cells = disc_cells(vdir)
    by_cue = {".".join(cue_path(k).with_suffix("").parts): k for k in cells}
    problems = 0

    # 1. coverage
    played = set(re.findall(r"([A-Z]+\d*\.V\d{3}C\d{2}):\d+:\d+:", Path(a.program).read_text(encoding="utf-8")))
    missing = sorted(c for c in played if not (content / cue_path(by_cue[c])).exists()) if by_cue else []
    unknown = sorted(c for c in played if c not in by_cue)
    parts = sorted(content.rglob("*.part.mp4"))
    print(f"coverage: {len(played)} cues in the program, {len(missing)} without a clip, "
          f"{len(parts)} unfinished .part files")
    for c in missing[:10]:
        print("   missing", c)
    for c in unknown[:10]:
        print("   program cue not on the disc?", c)
    if parts:
        print("   rerun dvd_cells.py to redo:", ", ".join(p.name for p in parts[:5]))
    problems += len(missing) + len(parts) + len(unknown)

    # 2. lengths
    off = []
    for cue, k in sorted(by_cue.items()):
        f = content / cue_path(k)
        if not f.exists():
            continue
        d = duration(f)
        want = max(cells[k]["time"], 1 / 29.97)
        if d is None or abs(d - want) > 0.1:
            off.append((cue, d, want))
    print(f"length: {len(off)} clips more than 0.1 s from their IFO time")
    for cue, d, want in off[:10]:
        print(f"   {cue}: {d} s, IFO {want:.3f} s")
    problems += len(off)

    # 3. tail audio
    if a.tails != "0":
        readers, cont = {}, []
        for cue, k in sorted(by_cue.items()):
            rd = readers.setdefault(k[:2], VobReader(vob_set(vdir, k[0], k[1])))
            e, n = nav_ptm(rd, cells[k]["lastvobu"]), nav_ptm(rd, cells[k]["last"] + 1)
            if e and n and abs(n[0] - e[1]) <= 3 and (content / cue_path(k)).exists():
                cont.append((cue, k))
        if a.tails != "all":
            random.seed(1)
            cont = random.sample(cont, min(int(a.tails), len(cont)))
        lost = []
        for i, (cue, k) in enumerate(cont, 1):
            x = pcm(["-sseof", "-0.3", "-i", str(content / cue_path(k))])
            if x and trailing_quiet_ms(x) > GAP_MS:
                rd = readers[k[:2]]
                if not disc_is_quiet_at_end(rd, cells[k]):
                    lost.append(cue)
            if i % 100 == 0:
                print(f"   ... {i}/{len(cont)}")
        print(f"tail audio: {len(cont)} cells continue into the next cell's sectors; "
              f"{len(lost)} end in silence the disc doesn't have")
        for c in lost[:10]:
            print("   lost tail audio:", c)
        if lost:
            print("   fix: python dvd_cells.py <VIDEO_TS> <Content> --tails   (re-encodes just these)")
        problems += len(lost)

    print("\nOK" if not problems else f"\n{problems} problem(s) -- fix before building bundles")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
