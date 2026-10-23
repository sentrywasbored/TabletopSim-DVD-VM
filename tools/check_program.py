#!/usr/bin/env python3
"""
Does this disc need anything the DVD VM doesn't emulate?

    python check_program.py <name>_program.txt [<name>_fixes.txt]

Reads every VM command in the program export (PGC pre / post / cell commands and
every button command, plus any patch lines) and reports:

  * command groups 4-7 (the VM only runs groups 0-3: compare/goto, links/jumps,
    SetSystem, Set) -- these are skipped with a log line at runtime
  * SetSystem forms the VM ignores: SetNVTMR (navigation timer), SetAMXMD
    (karaoke audio mix) -- and SetGPRMMD with counter mode on (registers that
    count seconds by themselves; the VM treats them as plain registers)
  * SetSTN of the audio or angle stream (harmless: the dump has one audio track and
    one angle, but the disc may expect a different track)
  * angle / multi-angle hints: commands that set SPRM3

None of these occurred on 24 or The Amazing Race. If any appear, check what the
disc uses them for (the Test Player traces every command) before porting.
"""
import sys
from collections import Counter


def bits(v, start, count):
    """libdvdnav numbering: bit 63 is the MSB of the 64-bit command."""
    return (v >> (start - count + 1)) & ((1 << count) - 1)


def commands(path):
    for line in open(path, encoding="utf-8"):
        f = line.split()
        if not f:
            continue
        if f[0] == "G" and len(f) >= 12:          # pre post cellcmds = fields 10..12
            for field, where in ((f[9], "pre"), (f[10], "post"), (f[11], "cellcmd")):
                if field != "-":
                    for i in range(0, len(field), 16):
                        yield field[i:i + 16], f"{f[1]} {where}"
        elif f[0] == "B" and len(f) >= 7:         # buttons: n,x0,y0,x1,y1,u,d,l,r,auto,cmd;...
            for b in f[6].split(";"):
                v = b.split(",")
                if len(v) >= 11 and v[10] != "-":
                    yield v[10], f"{f[1]} button {v[0]}"
        elif f[0] == "P" and len(f) >= 5 and f[4] != "-":
            for i in range(0, len(f[4]), 16):
                yield f[4][i:i + 16], f"patch {f[1]} {f[2]}"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    total = Counter()
    issues = {}

    def flag(kind, where, hexcmd):
        issues.setdefault(kind, []).append(f"{where}: {hexcmd}")

    for path in sys.argv[1:]:
        for hexcmd, where in commands(path):
            try:
                v = int(hexcmd, 16)
            except ValueError:
                flag("unreadable command", where, hexcmd)
                continue
            grp = bits(v, 63, 3)
            total[grp] += 1
            if grp >= 4:
                flag(f"command group {grp} (not emulated)", where, hexcmd)
            elif grp == 2:
                t = bits(v, 59, 4)
                if t == 2:
                    flag("SetNVTMR navigation timer (not emulated)", where, hexcmd)
                elif t == 4:
                    flag("SetAMXMD karaoke mix (not emulated)", where, hexcmd)
                elif t == 3 and bits(v, 23, 1):
                    flag("SetGPRMMD counter mode (register counts by itself; not emulated)", where, hexcmd)
                elif t == 1:
                    if bits(v, 39, 1):
                        flag("SetSTN audio stream (dump has one audio track)", where, hexcmd)
                    if bits(v, 23, 1):
                        flag("SetSTN angle (dump has one angle)", where, hexcmd)

    print("commands by group:", {g: total[g] for g in sorted(total)},
          "(0 compare/goto, 1 link/jump, 2 SetSystem, 3 Set)")
    if not issues:
        print("OK: everything this disc runs is emulated.")
        return
    for kind, where in sorted(issues.items()):
        print(f"\n{kind}: {len(where)}")
        for w in where[:8]:
            print("   ", w)
        if len(where) > 8:
            print(f"    ... and {len(where) - 8} more")


if __name__ == "__main__":
    main()
