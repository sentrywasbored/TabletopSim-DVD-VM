#!/usr/bin/env python3
"""
DVD Port Kit -- turn a DVD game's VIDEO_TS into everything a TTS port needs.

    python port.py "<disc folder>" --name 24 --title "24: The DVD Board Game"
    python port.py "<disc folder>" --name 24 --steps check,global

<disc folder> must hold VIDEO_TS\\. Everything is written to <disc folder>\\TTS\\.
Steps (default: all of them, in this order; each is safe to re-run):

  program   dvd_program.py      the disc's navigation program -> <name>_program.txt
  features  check_program.py    does the disc use anything the VM doesn't emulate?
  dump      dvd_cells.py        one MP4 per cell -> Content\\  (hours; resumes where it
                                stopped, skips finished cells)
  manifest  build_dvd_manifest  -> manifests\\<name>_unity.json (Unity) + <name>_lua.json
  spu       dvd_spu_export.py   subpictures + button highlights -> <name>_spu.txt, SPU\\
            build_spu_manifest  -> manifests\\<name>_spu_unity.json (Unity)
  check     check_dump.py       coverage, clip lengths, tail audio (see README)
  global    build_tts_global    the TTS Global script -> <name>_global.lua
  test      test_tts_global.lua simulated TTS run (only if lua5.2 / lua is installed)

Optional, for a new disc before the long dump (reads the whole disc, slow):
  scan      nav_scan.py + spu_scan.py   mid-cell button / subpicture changes

Options:
  --jobs N           parallel encodes for the dump (default: half your CPU threads)
  --crf 24 --abr 128k  video / audio quality (lower CRF = bigger, better)
  --unity-folder X   where the disc's files live in the Unity project:
                     Assets/X/Content and Assets/X/SPU (default: the --name;
                     "Assets/X" works too)
  --tails N          check_dump: check N random chained cells instead of all
  --tts-dir / --content   write somewhere other than <disc>\\TTS / use another clip folder

Put disc-bug patches in <disc folder>\\TTS\\<name>_fixes.txt (see README) -- the
global step embeds them, switchable in the script with CONFIG.FIXES.
"""
import argparse, os, shutil, subprocess, sys, time
from pathlib import Path

KIT = Path(__file__).resolve().parent
TOOLS = KIT / "tools"
ALL = ["program", "features", "dump", "manifest", "spu", "check", "global", "test"]
EXTRA = ["scan"]


def find_disc(arg):
    """The disc folder and its VIDEO_TS, from what the user typed.

    Tolerates a trailing backslash: Windows PowerShell before 7.3 passes
    '..\\Some Disc\\' to Python as  ..\\Some Disc"  (the backslash escapes the closing
    quote it adds around paths with spaces). Also accepts the VIDEO_TS folder itself."""
    raw = arg.strip().strip('"').strip("'").rstrip("\\/ ")
    p = Path(raw).resolve()
    if p.name.upper() == "VIDEO_TS" and p.is_dir():
        p = p.parent
    if p.is_dir():
        for child in p.iterdir():
            if child.is_dir() and child.name.upper() == "VIDEO_TS" and \
                    any(f.name.upper() == "VIDEO_TS.IFO" for f in child.iterdir()):
                return p, child
    msg = [f"Couldn't find VIDEO_TS\\VIDEO_TS.IFO for: {arg!r}", f"  looked in: {p}"]
    if not p.exists():
        msg.append("  that folder doesn't exist")
    elif p.is_dir():
        msg.append("  it contains: " + ", ".join(sorted(c.name for c in p.iterdir())[:15]))
    sys.exit("\n".join(msg))


def run(args, cwd, label):
    print(f"\n=== {label} ===\n$ " + " ".join(str(a) for a in args), flush=True)
    t = time.time()
    r = subprocess.run([str(a) for a in args], cwd=cwd)
    print(f"--- {label}: {'ok' if r.returncode == 0 else 'exit ' + str(r.returncode)} "
          f"({time.time() - t:.0f} s)", flush=True)
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("disc", help="folder that contains VIDEO_TS")
    ap.add_argument("--name", required=True, help="short id used in file names, e.g. 24 or tar")
    ap.add_argument("--title", default=None, help="human title for the script header")
    ap.add_argument("--steps", default="all", help="comma list, e.g. check,global (default all)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--crf", default="24")
    ap.add_argument("--abr", default="128k")
    ap.add_argument("--unity-folder", default=None)
    ap.add_argument("--tails", default="all")
    ap.add_argument("--tts-dir", default=None, help="output folder (default <disc>\\TTS)")
    ap.add_argument("--content", default=None, help="clip folder (default <TTS>\\Content)")
    a = ap.parse_args()

    disc, vts = find_disc(a.disc)
    tts = Path(a.tts_dir).resolve() if a.tts_dir else disc / "TTS"
    tts.mkdir(parents=True, exist_ok=True)
    content = Path(a.content).resolve() if a.content else tts / "Content"
    name = a.name
    # --unity-folder is the folder under Assets; "Assets/X" is accepted too
    uf = (a.unity_folder or a.name).replace("\\", "/").strip("/")
    if uf.lower().startswith("assets/"):
        uf = uf[len("assets/"):]
    steps = ALL if a.steps == "all" else [s.strip() for s in a.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in ALL + EXTRA]
    if bad:
        sys.exit(f"unknown step(s): {bad}; choose from {ALL + EXTRA}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg and ffprobe must be on PATH")
    py = sys.executable
    program = tts / f"{name}_program.txt"
    failed = []

    for step in steps:
        rc = 0
        if step == "program":
            rc = run([py, TOOLS / "dvd_program.py", disc, program, "--name", name], tts, "program")
        elif step == "features":
            extra = [tts / f"{name}_fixes.txt"] if (tts / f"{name}_fixes.txt").exists() else []
            rc = run([py, TOOLS / "check_program.py", program, *extra], tts, "features")
        elif step == "scan":
            rc = run([py, TOOLS / "nav_scan.py", vts], tts, "scan: buttons per VOBU")
            rc |= run([py, TOOLS / "spu_scan.py", vts], tts, "scan: subpictures")
        elif step == "dump":
            rc = run([py, TOOLS / "dvd_cells.py", vts, content, "--jobs", a.jobs,
                      "--crf", a.crf, "--abr", a.abr], tts, "dump")
        elif step == "manifest":
            rc = run([py, TOOLS / "build_dvd_manifest.py", content, program, "--name", name,
                      "--out-dir", tts / "manifests", "--unity-root", f"Assets/{uf}/Content"], tts, "manifest")
        elif step == "spu":
            rc = run([py, TOOLS / "dvd_spu_export.py", vts, tts, "--name", name], tts, "spu export")
            if rc == 0:
                rc = run([py, TOOLS / "build_spu_manifest.py", tts / f"{name}_spu.txt", tts / "SPU",
                          "--name", name, "--out-dir", tts / "manifests", "--unity-dir", f"Assets/{uf}/SPU"],
                         tts, "spu manifest")
        elif step == "check":
            rc = run([py, TOOLS / "check_dump.py", vts, content, program, "--tails", a.tails],
                     tts, "check")
        elif step == "global":
            rc = run([py, TOOLS / "build_tts_global.py", "--name", name, "--title", a.title or name,
                      "--dir", tts], tts, "global script")
        elif step == "test":
            lua = shutil.which("lua5.2") or shutil.which("lua52") or shutil.which("lua")
            if not lua:
                print("\n=== test === skipped: no lua5.2 / lua on PATH (optional)")
                continue
            rc = run([lua, KIT / "tests" / "test_tts_global.lua", name, "1", "2"], tts, "simulated TTS")
        if rc:
            failed.append(step)
            if step in ("program", "dump"):
                print(f"\nstopping: later steps need '{step}'")
                break

    print("\n" + ("all steps ok" if not failed else "steps with problems: " + ", ".join(failed)))
    print(f"\nnext: Unity (README, part 2) with\n  {content}  ->  Assets/{uf}/Content"
          f"\n  {tts / 'SPU'}  ->  Assets/{uf}/SPU   (img\\ and calibrate.png)"
          f"\n  manifests: {tts / 'manifests'}\nthen TTS with {tts / (name + '_global.lua')}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
