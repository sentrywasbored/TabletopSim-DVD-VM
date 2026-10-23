#!/usr/bin/env python3
"""
Cue manifests for a per-cell DVD dump (dvd_cells.py) driven by the Lua DVD VM.

    python build_dvd_manifest.py Content <program.txt> --name 24 --out-dir manifests

Cue name = path under Content with separators -> '.', extension stripped
(Content/VTS07/V003C05.mp4 -> "VTS07.V003C05"), the same rule dvd_program.py
uses for the cells it lists, so the VM never needs a lookup table.

"loop": true is set where the disc itself loops a cell with no side effects,
so Unity can loop the clip natively instead of the VM re-triggering it
(re-triggering a cue costs a visible hitch):
  * cells with an infinite still (still byte 255);
  * the only cell of a PGC whose cell command just restarts that cell
    (LinkPGN 1 / LinkCN 1 -- the usual "30 s menu loop").
The VM treats a looping cue as "wait for input" and doesn't re-run the cell.

Writes, into --out-dir:
    <name>_unity.json  -> GenerateVideoCues.cs (index 0 = Idle, 0.2 s black)
    <name>_lua.json    -> host it; cueName, duration, loop only
"""
import argparse, json, subprocess, sys
from pathlib import Path

SELF_LOOP = {"2006000000000001", "2007000000000001",   # LinkPGN 1, LinkCN 1
             "2006000000000401", "2007000000000401"}   # ... (button 1)


def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise RuntimeError(r.stderr.strip() or "no duration")
    return round(float(r.stdout.strip()), 3)


def looping_cues(program):
    loops, first = set(), None
    for line in Path(program).read_text().splitlines():
        f = line.split(" ")
        if f[0] != "G" or f[12] == "-":
            continue
        cells = [c.split(":") for c in f[12].split(",")]
        cc = f[11]
        for cue, still, cmd, _ in cells:
            if first is None:
                first = cue
            if still == "255":
                loops.add(cue)
        if len(cells) == 1 and cells[0][2] != "0":
            n = int(cells[0][2])
            if cc[(n - 1) * 16:n * 16].lower() in SELF_LOOP:
                loops.add(cells[0][0])
    return loops, first


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("content_dir")
    ap.add_argument("program")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--unity-root", default="Assets/Content")
    a = ap.parse_args()

    root = Path(a.content_dir).resolve()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    loops, first = looping_cues(a.program)
    unity, lua, failed = [], [], []
    for f in sorted(root.rglob("*.mp4")):
        if ".part" in f.name:
            continue
        rel = f.relative_to(root)
        name = ".".join(rel.with_suffix("").parts)
        try:
            dur = probe(f)
        except RuntimeError as e:
            failed.append(f"{rel}: {e}")
            continue
        loop = name in loops
        unity.append({"cueName": name, "videoClipPath": f"{a.unity_root}/{rel.as_posix()}", "loop": loop})
        lua.append({"cueName": name, "duration": dur, "loop": loop})
    # effect index 0 autoplays when the bundle loads: keep it the 0.2 s black Idle clip
    unity.sort(key=lambda c: c["cueName"] != "Idle")
    (out / f"{a.name}_unity.json").write_text(json.dumps({"cues": unity}, indent=1))
    (out / f"{a.name}_lua.json").write_text(json.dumps({"cues": lua}, separators=(",", ":")))
    print(f"{len(unity)} cues ({sum(c['loop'] for c in unity)} looping), index 0 = {unity[0]['cueName'] if unity else '-'}")
    if failed:
        print("\nffprobe failed on:", *failed, sep="\n  ", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
