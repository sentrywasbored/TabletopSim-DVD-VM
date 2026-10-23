#!/usr/bin/env python3
"""
Dump every DVD cell of a disc to its own Unity-ready MP4, cut on the cell's own
presentation timestamps so audio and video stay in sync.

    python dvd_cells.py <VIDEO_TS> <out folder> [--jobs 4] [--crf 24] [--abr 128k]
                        [--only VTS05,VMGM] [--list] [--force] [--tails]

One file per unique cell, named by where it lives on the disc:
    <out>/VMGM/V004C01.mp4          VIDEO_TS.VOB, VOB id 4, cell id 1
    <out>/VTSM07/V012C01.mp4        VTS_07_0.VOB (title-set menus)
    <out>/VTS07/V003C05.mp4         VTS_07_1..9.VOB (titles)
Cue names (path, separators -> '.') are therefore e.g. "VTS07.V003C05"; the
program export (dvd_program.py) uses the same rule, so the Lua VM can find the
cue for any cell without a lookup table.

How a cell is cut:
  * the cell's sectors (first..last, from the PGC cell table) are read straight
    from the VOB set and piped into ffmpeg -- no PgcDemux step;
  * the first VOBU's NAV pack gives VOBU_S_PTM, the last VOBU's gives VOBU_E_PTM;
    video and audio are trimmed to exactly that window with -copyts, so a cell
    whose audio is muxed a few frames ahead of its video still lines up;
  * on a seamless join the disc stores the last ~200 ms of the cell's audio in the
    next cell's first VOBUs (audio is muxed behind video), so reading continues past
    the cell while the timeline is continuous (tail_sectors); --tails re-encodes
    just those cells in a dump made before this was handled;
  * if the result is short of the IFO playback time (a held last frame, a short
    audio tail), video is padded with its last frame and audio with silence.

Encoding (Gatekeeper/Khufu settings, NTSC 4:3 source):
    H.264 High CRF 24 veryslow, 640x480 square pixels, 29.97 fps, bwdif
    deinterlace + light hqdn3d; AAC-LC stereo 48 kHz (5.1 downmixed, not renormalised)
--list writes cells.csv (cue, domain, sectors, seconds) and exits without encoding.
"""
import argparse, csv, os, struct, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

W, H = 640, 480
CRF, ABR = 24, "128k"
VF = "bwdif=mode=send_frame:parity=tff,hqdn3d=2:1.5:3:3,scale={w}:{h}:flags=lanczos,setsar=1"
DOWNMIX = ("pan=stereo|c0=FL+0.707*FC+0.707*SL|c1=FR+0.707*FC+0.707*SR,"
           "alimiter=limit=0.97:level=disabled")


# ---------------------------------------------------------------- IFO reading
def u16(d, o): return struct.unpack(">H", d[o:o + 2])[0]
def u32(d, o): return struct.unpack(">I", d[o:o + 4])[0]
def bcd(b): return (b >> 4) * 10 + (b & 15)


def ptime(b):
    fps = {1: 25, 3: 30000 / 1001}.get(b[3] >> 6, 25)
    return bcd(b[0]) * 3600 + bcd(b[1]) * 60 + bcd(b[2]) + bcd(b[3] & 0x3f) / fps


def pgc_cells(d, o):
    ncell = d[o + 3]
    cp, cpos = u16(d, o + 0xE8), u16(d, o + 0xEA)
    out = []
    for i in range(ncell):
        e, q = o + cp + 24 * i, o + cpos + 4 * i
        out.append({"vobid": u16(d, q), "cellid": d[q + 3], "time": ptime(d[e + 4:e + 8]),
                    "first": u32(d, e + 8), "lastvobu": u32(d, e + 16), "last": u32(d, e + 20)})
    return out


def pgcit(d, base):
    return [base + u32(d, base + 8 + 8 * i + 4) for i in range(u16(d, base))]


def pgci_ut(d, base):
    out = []
    for i in range(u16(d, base)):
        out += pgcit(d, base + u32(d, base + 8 + 8 * i + 4))
    return out


def disc_cells(vdir):
    """{(domain, vtsn, vobid, cellid): cell} for every cell any PGC plays."""
    vdir = Path(vdir)
    cells = {}

    def take(dom, n, d, pgc_offsets):
        for o in pgc_offsets:
            if d[o + 3] == 0:
                continue
            for c in pgc_cells(d, o):
                cells.setdefault((dom, n, c["vobid"], c["cellid"]), c)

    d = (vdir / "VIDEO_TS.IFO").read_bytes()
    if u32(d, 0xC8):
        take("VMGM", 0, d, pgci_ut(d, u32(d, 0xC8) * 2048))
    for n in range(1, u16(d, 0x3E) + 1):
        f = vdir / f"VTS_{n:02d}_0.IFO"
        if not f.exists():
            continue
        v = f.read_bytes()
        if u32(v, 0xD0):
            take("VTSM", n, v, pgci_ut(v, u32(v, 0xD0) * 2048))
        take("VTS", n, v, pgcit(v, u32(v, 0xCC) * 2048))
    return cells


def vob_set(vdir, dom, n):
    vdir = Path(vdir)
    if dom == "VMGM":
        return [vdir / "VIDEO_TS.VOB"]
    if dom == "VTSM":
        return [vdir / f"VTS_{n:02d}_0.VOB"]
    return sorted(vdir.glob(f"VTS_{n:02d}_[1-9].VOB"))


def cue_path(key):
    dom, n, vob, cell = key
    folder = "VMGM" if dom == "VMGM" else f"{dom}{n:02d}"
    return Path(folder) / f"V{vob:03d}C{cell:02d}.mp4"


class VobReader:
    def __init__(self, paths):
        self.paths = paths
        self.sizes = [p.stat().st_size for p in paths]

    def read(self, sector, count):
        off, want, out = sector * 2048, count * 2048, bytearray()
        for p, sz in zip(self.paths, self.sizes):
            if off >= sz:
                off -= sz
                continue
            with open(p, "rb") as f:
                f.seek(off)
                out += f.read(want - len(out))
            off = 0
            if len(out) >= want:
                break
        return bytes(out)


def nav_ptm(reader, sector):
    """(VOBU_S_PTM, VOBU_E_PTM) from the NAV pack at sector, in 90 kHz ticks."""
    p = reader.read(sector, 1)
    if p[:4] != b"\x00\x00\x01\xba" or p[0x26:0x2A] != b"\x00\x00\x01\xbf":
        return None
    return u32(p, 0x39), u32(p, 0x3D)


def tail_sectors(reader, cell, max_lead=0.6, scan=2048):
    """Sectors after the cell that still hold its own audio.

    Discs mux audio *behind* the video with the same timestamps, so on a seamless
    join the cell's last ~200 ms of audio sits in the next cell's first VOBUs.
    If the sector after the cell is a NAV pack that continues the timeline (its
    VOBU_S_PTM equals the cell's VOBU_E_PTM), read on while the timeline stays
    continuous and within max_lead seconds of the cell's end; atrim then keeps only
    the audio up to the cell's end. 0 when the next sectors are something else."""
    e = nav_ptm(reader, cell["lastvobu"])
    if not e:
        return 0
    end = e[1]
    data = reader.read(cell["last"] + 1, scan)
    n = len(data) // 2048
    count, prev_end = 0, end
    for i in range(n):
        p = data[i * 2048:(i + 1) * 2048]
        if p[:4] == b"\x00\x00\x01\xba" and p[0x26:0x2A] == b"\x00\x00\x01\xbf":
            vs, ve = u32(p, 0x39), u32(p, 0x3D)
            if abs(vs - prev_end) > 3 or vs > end + max_lead * 90000:
                break
            prev_end = ve
        elif i == 0:
            return 0              # not a NAV pack right after the cell: no continuation
        count = i + 1
    return count


# ---------------------------------------------------------------- encoding
def probe_channels(data):
    r = subprocess.run(["ffprobe", "-v", "error", "-f", "mpeg", "-i", "-", "-select_streams", "a:0",
                        "-show_entries", "stream=channels", "-of", "csv=p=0"],
                       input=data[:4 * 1024 * 1024], capture_output=True)
    try:
        return int(r.stdout.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def encode(vdir, key, cell, dst, a):
    rd = VobReader(vob_set(vdir, key[0], key[1]))
    data = rd.read(cell["first"], cell["last"] - cell["first"] + 1 + tail_sectors(rd, cell))
    s = nav_ptm(rd, cell["first"])
    e = nav_ptm(rd, cell["lastvobu"])
    length = max(cell["time"], 1 / 29.97)
    vf = VF.format(w=W, h=H)
    if s and e and e[1] > s[0]:
        t0, t1 = s[0] / 90000, e[1] / 90000
        # shift by the cell's own start, not by the first surviving frame: an open
        # GOP drops the leading B-frames, and STARTPTS would pull video ahead of audio
        vtrim = (f"trim=start={t0:.5f}:end={t1:.5f},setpts=PTS-{t0:.5f}/TB,"
                 f"fps=30000/1001:start_time=0,")
        atrim = (f"atrim=start={t0:.5f}:end={t1:.5f},asetpts=PTS-{t0:.5f}/TB,"
                 f"aresample=48000:first_pts=0,")
    else:
        vtrim = atrim = ""
    ch = probe_channels(data)
    vchain = f"{vtrim}{vf},tpad=stop_mode=clone:stop_duration={length:.3f}"
    if getattr(a, "label", False):
        vchain += "," + label_filter(".".join(cue_path(key).with_suffix("").parts))
    if ch:
        achain = atrim + (DOWNMIX + "," if ch == 6 else "") + "aresample=48000," + \
                 f"apad=whole_dur={length:.3f}"
        maps = ["-filter_complex", f"[0:v:0]{vchain}[v];[0:a:0]{achain}[a]", "-map", "[v]", "-map", "[a]"]
        inputs = []
    else:
        maps = ["-filter_complex", f"[0:v:0]{vchain}[v]", "-map", "[v]", "-map", "1:a"]
        inputs = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-copyts", "-f", "mpeg", "-i", "-",
           *inputs, *maps, "-t", f"{length:.3f}",
           "-c:v", "libx264", "-profile:v", "high", "-preset", a.preset, "-crf", str(a.crf),
           "-pix_fmt", "yuv420p", "-r", "30000/1001", "-g", "60",
           "-c:a", "aac", "-b:a", a.abr, "-ac", "2", "-ar", "48000",
           "-movflags", "+faststart", "-map_metadata", "-1", "-map_chapters", "-1", str(dst)]
    r = subprocess.run(cmd, input=data, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace").strip()[-600:])


FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "C:/Windows/Fonts/consolab.ttf",
         "C:/Windows/Fonts/consola.ttf", "/System/Library/Fonts/Menlo.ttc"]


def label_filter(text):
    """Burn '<cue>  f<frame>' into the corner: test clips only (--label), so a screen
    recording shows exactly which frames of which clip were displayed."""
    font = next((f for f in FONTS if Path(f).exists()), None)
    ff = f"fontfile='{font.replace(':', chr(92) + ':')}':" if font else ""
    return (f"drawtext={ff}text='{text}  f%{{eif\\:n\\:d\\:3}}':x=12:y=12:fontsize=26:"
            "fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video_ts")
    ap.add_argument("out")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--crf", type=int, default=CRF)
    ap.add_argument("--abr", default=ABR)
    ap.add_argument("--preset", default="veryslow")
    ap.add_argument("--only", default="", help="comma list of folders, e.g. VMGM,VTSM01,VTS05")
    ap.add_argument("--limit", type=int, default=0, help="encode at most N cells (testing)")
    ap.add_argument("--every", type=int, default=0, help="encode every Nth cell only (size sampling)")
    ap.add_argument("--cues", default="", help="comma list of cue names to encode, e.g. VTS08.V003C01")
    ap.add_argument("--label", action="store_true", help="burn cue name + frame number in (test clips)")
    ap.add_argument("--list", action="store_true", help="write cells.csv and exit")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tails", action="store_true",
                    help="re-encode only cells whose audio tail lives in the next cell's sectors "
                         "(dumps made before the tail fix are missing ~200 ms of audio there)")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cells = disc_cells(a.video_ts)
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    keys = [k for k in sorted(cells) if not only or cue_path(k).parts[0] in only]
    if a.cues:
        want = {c.strip() for c in a.cues.split(",") if c.strip()}
        keys = [k for k in keys if ".".join(cue_path(k).with_suffix("").parts) in want]

    with open(out / "cells.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cue", "domain", "vts", "vobid", "cellid", "first", "last", "seconds"])
        for k in sorted(cells):
            c = cells[k]
            w.writerow([".".join(cue_path(k).with_suffix("").parts), *k, c["first"], c["last"],
                        f"{c['time']:.3f}"])
    print(f"{len(cells)} unique cells, {sum(c['time'] for c in cells.values()) / 60:.1f} min")
    if a.list:
        return

    idle = out / "Idle.mp4"          # 0.2 s black: bundle index 0, the VM's "stop" cue
    if a.force or not idle.exists():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=black:s={W}x{H}:r=30000/1001", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", "0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "64k", str(idle)], check=True)
    if a.tails:
        readers = {}
        def has_tail(k):
            rd = readers.setdefault(k[:2], VobReader(vob_set(a.video_ts, k[0], k[1])))
            return tail_sectors(rd, cells[k]) > 0
        keys = [k for k in keys if has_tail(k)]
        print(f"{len(keys)} cells carry audio in the following sectors")
    todo = [k for k in keys if a.force or a.tails or not (out / cue_path(k)).exists()]
    if a.every:
        todo = todo[::a.every]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(keys)} selected, {len(todo)} to encode with {a.jobs} jobs")
    failed = []

    def run(k):
        dst = out / cue_path(k)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.stem + ".part.mp4")
        encode(a.video_ts, k, cells[k], tmp, a)
        tmp.replace(dst)
        return dst

    with ThreadPoolExecutor(a.jobs) as pool:
        futs = {pool.submit(run, k): k for k in todo}
        for i, f in enumerate(as_completed(futs), 1):
            k = futs[f]
            try:
                f.result()
                print(f"[{i}/{len(todo)}] {cue_path(k).as_posix()}")
            except Exception as e:
                failed.append(k)
                print(f"[{i}/{len(todo)}] FAILED {cue_path(k).as_posix()}\n    {e}")
    if failed:
        print(f"\n{len(failed)} failed -- rerun to retry just those")
        sys.exit(1)


if __name__ == "__main__":
    main()
