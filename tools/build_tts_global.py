#!/usr/bin/env python3
"""
Build a disc's complete TTS Global script: the DVD VM host with the disc embedded.

    python build_tts_global.py --name 24 --title "24: The DVD Board Game" [--dir <TTS folder>]

Reads, from --dir (the disc's TTS folder, default: the current folder):
    dvdgame_vm_host.lua         the host template   } from this script's own folder
    dvdvm.lua                   the VM              } (the kit's tools/), not --dir
    <name>_program.txt          dvd_program.py export
    <name>_fixes.txt            optional disc-bug patches (switch with CONFIG.FIXES)
    manifests/<name>_lua.json   cue durations + loop flags (build_dvd_manifest.py)
    <name>_spu.txt              subpicture records (dvd_spu_export.py), optional
and writes one self-contained .lua to paste into Global. Nothing is fetched at
runtime: the only external assets are the two AssetBundles.

Rebuilding over an existing <name>_global.lua keeps its settings: the object GUIDs,
CONFIG.FIXES and the calibrated FRAME, so the new script can be pasted straight in.

Checks while building: every cue the program can play has a duration, and every
subpicture image the script can show exists in manifests/<name>_spu_unity.json.
"""
import argparse, datetime, json, re, sys
from collections import defaultdict
from pathlib import Path

DELIM = "]=====]"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dir", default=".", help="the disc's TTS folder")
    ap.add_argument("--template", default=None, help="host template (default: next to this script)")
    a = ap.parse_args()
    here = Path(a.dir)
    kit = Path(__file__).resolve().parent
    name = a.name
    out = Path(a.out) if a.out else here / f"{name}_global.lua"

    tpl = Path(a.template or kit / "dvdgame_vm_host.lua").read_text(encoding="utf-8")
    vmsrc = (kit / "dvdvm.lua").read_text(encoding="utf-8")
    program = (here / f"{name}_program.txt").read_text(encoding="utf-8").strip("\n")
    fixes_p = here / f"{name}_fixes.txt"
    fixes = fixes_p.read_text(encoding="utf-8").strip("\n") if fixes_p.exists() else ""
    man = json.loads((here / "manifests" / f"{name}_lua.json").read_text(encoding="utf-8"))["cues"]

    cues = [f"{c['cueName']} {c['duration']} {1 if c.get('loop') else 0} 0" for c in man]
    known = {c["cueName"] for c in man}

    spu_lines, has_base, images = [], False, set()
    spu_p = here / f"{name}_spu.txt"
    if spu_p.exists():
        H, N = defaultdict(list), defaultdict(list)
        for line in spu_p.read_text(encoding="utf-8").splitlines():
            f = line.split()
            if not f:
                continue
            if f[0] == "H":                       # H cue s btn sel act x y w h
                H[f[1]].append(f"{f[2]},{f[3]},{f[4]},{f[5]}")
                images.update(i for i in (f[4], f[5]) if i != "-")
            elif f[0] == "N":                     # N cue s img x y w h stop
                N[f[1]].append(f"{f[2]},{f[3]},{f[8]}")
                images.add(f[3])
        spu_lines = [f"H {c} " + " ".join(v) for c, v in sorted(H.items())] + \
                    [f"N {c} " + " ".join(v) for c, v in sorted(N.items())]
        has_base = bool(N)

    # checks
    problems = []
    played = set(re.findall(r"([A-Z]+\d*\.V\d{3}C\d{2}):\d+:\d+:", program))
    missing = sorted(played - known)
    if missing:
        problems.append(f"{len(missing)} cues in the program have no duration in the manifest, e.g. {missing[:5]}")
    su = here / "manifests" / f"{name}_spu_unity.json"
    if images and su.exists():
        effects = {c["cueName"] for c in json.loads(su.read_text(encoding="utf-8"))["cues"]}
        lost = sorted(i for i in images if "S_" + i not in effects)
        if lost:
            problems.append(f"{len(lost)} subpicture images aren't effects in {su.name}, e.g. {lost[:5]}")
    for label, text in (("program", program), ("fixes", fixes), ("cues", "\n".join(cues)),
                        ("spu", "\n".join(spu_lines)), ("vm", vmsrc)):
        if DELIM in text:
            sys.exit(f"{label} contains the long-string delimiter {DELIM}")

    script = (tpl.replace("%%TITLE%%", a.title or name)
                 .replace("%%DATE%%", datetime.date.today().isoformat())
                 .replace("%%BASE_DEFAULT%%", "PASTE_BASE_GUID" if has_base else "")
                 .replace("%%DVDVM%%", vmsrc.rstrip("\n"))
                 .replace("%%PROGRAM%%", program)
                 .replace("%%FIXES%%", fixes)
                 .replace("%%CUES%%", "\n".join(cues))
                 .replace("%%SPU%%", "\n".join(spu_lines)))
    kept = []
    if out.exists():                              # carry the user's settings over
        old = out.read_text(encoding="utf-8")
        for key in ("SCREEN_A", "SCREEN_B", "HIGHLIGHT", "BASE"):
            m = re.search(rf'^  {key}\s*=\s*"([^"]*)"', old, re.M)
            if m and m.group(1) and "PASTE" not in m.group(1):
                script = re.sub(rf'^(  {key}\s*=\s*)"[^"]*"', lambda x: f'{x.group(1)}"{m.group(1)}"',
                                script, count=1, flags=re.M)
                kept.append(key)
        for pat in (r"^  FIXES\s*=\s*(true|false)", r"^local FRAME = \{[^}]*\}"):
            m = re.search(pat, old, re.M)
            if m:
                script = re.sub(pat, lambda x: m.group(0), script, count=1, flags=re.M)
                kept.append(m.group(0).split("=")[0].strip().replace("local ", ""))
    out.write_text(script, encoding="utf-8", newline="\n")
    print(f"{out}: {len(script) / 1024:.0f} KB -- {len(cues)} cues, {len(spu_lines)} subpicture records, "
          f"base overlay {'used' if has_base else 'not needed'}")
    if kept:
        print("kept from the previous script:", ", ".join(kept))
    for p in problems:
        print("WARNING:", p)


if __name__ == "__main__":
    main()
