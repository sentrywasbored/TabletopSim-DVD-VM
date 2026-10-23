#!/usr/bin/env python3
"""
Export a disc's subpictures and button colour schemes for the DVD VM.

    python dvd_spu_export.py <VIDEO_TS> <out folder> --name 24 [--only VTS08,...]

Reads every cell (subpicture units come from the full sector range; the colour
schemes from the cell's first NAV pack) and writes, into <out folder>:

  <name>_spu.txt          the data file (below)
  SPU/layers/<id>.png     raw 2-bit layers, 8-bit greyscale PNG holding pixel values
                          0..3 (0 background, 1 pattern, 2 emphasis 1, 3 emphasis 2),
                          cropped to the subpicture's display area. For hosts that
                          recolour at runtime (the test player).
  SPU/img/<id>.png        pre-rendered RGBA images for hosts that can't recolour (TTS):
                          visible base layers and every button's selected / activated
                          highlight, cropped to their visible pixels. Deduplicated.

Data file, one record per line:
  D <name>
  P <pal> <16 x RRGGBB>                     palette (from the PGC colour table)
  L <layer> <x> <y> <w> <h>                 raw layer and where it sits on the 720x480 frame
  C <cue> <pal> <streams> <schemes> <btns>  per cell:
      streams  s:layer:colors:contrast:stop:forced;...   colors/contrast = 4 hex digits in
               e2 e1 p b order; stop = seconds into the cell the display ends, or -;
               forced = 1 if it shows even with subpicture display off
      schemes  3 x 16 hex digits: select colors, select contrast, action colors,
               action contrast (each 4 digits, e2 e1 p b), or - when the cell has no HLI
      btns     n:scheme,... (scheme 1-3; 0 = button never highlights)
  N <cue> <s> <img> <x> <y> <w> <h> <stop>  pre-rendered visible base layer for stream s
  H <cue> <s> <btn> <sel> <act> <x> <y> <w> <h>
                                            pre-rendered highlight of button btn when stream s
                                            is active ('-' = nothing visible in that state)

Palettes are taken from a PGC that actually plays the cell (the colour table is per
PGC; the first one with a non-empty table wins, and disagreements are reported).
"""
import argparse, hashlib, struct, sys
from collections import Counter
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dvd_cells import disc_cells, vob_set, VobReader, cue_path, pgcit, pgci_ut, u32
from dvd_spu import demux_spu, decode_spu, read_pci, pgc_palette, palette_is_empty, pgcs_for_cell


def hex4(v): return "".join(f"{x:x}" for x in v)


def domain_pgcs(vdir, dom, n, cache):
    k = (dom, n)
    if k not in cache:
        if dom == "VMGM":
            ifo = (vdir / "VIDEO_TS.IFO").read_bytes()
            offs = pgci_ut(ifo, u32(ifo, 0xC8) * 2048) if u32(ifo, 0xC8) else []
        else:
            ifo = (vdir / f"VTS_{n:02d}_0.IFO").read_bytes()
            if dom == "VTSM":
                offs = pgci_ut(ifo, u32(ifo, 0xD0) * 2048) if u32(ifo, 0xD0) else []
            else:
                offs = pgcit(ifo, u32(ifo, 0xCC) * 2048)
        cache[k] = (ifo, offs)
    return cache[k]


def render_crop(layer, area, palette, colors, contrast, clip=None):
    """RGBA crop of a layer recoloured with colors/contrast (e2,e1,p,b), optionally clipped
    to a rectangle (x0,y0,x1,y1 frame coords). Returns (img, x, y) or None if invisible."""
    x1, y1 = area[0], area[2]
    img = layer
    ox, oy = x1, y1
    if clip:
        cx0, cy0 = max(clip[0], x1), max(clip[1], y1)
        cx1, cy1 = min(clip[2], x1 + layer.width - 1), min(clip[3], y1 + layer.height - 1)
        if cx0 > cx1 or cy0 > cy1:
            return None
        img = layer.crop((cx0 - x1, cy0 - y1, cx1 - x1 + 1, cy1 - y1 + 1))
        ox, oy = cx0, cy0
    lut = [[0] * 256 for _ in range(4)]
    for px in range(4):
        k = 3 - px
        c = palette[colors[k]]
        for ch in range(3):
            lut[ch][px] = c[ch]
        lut[3][px] = contrast[k] * 17
    a = img.point(lut[3])
    bbox = a.getbbox()
    if not bbox:
        return None
    img, a = img.crop(bbox), a.crop(bbox)
    rgba = Image.merge("RGBA", (img.point(lut[0]), img.point(lut[1]), img.point(lut[2]), a))
    return rgba, ox + bbox[0], oy + bbox[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video_ts")
    ap.add_argument("out")
    ap.add_argument("--name", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-render", action="store_true", help="skip the pre-rendered TTS images")
    a = ap.parse_args()
    vdir, out = Path(a.video_ts), Path(a.out)
    (out / "SPU" / "layers").mkdir(parents=True, exist_ok=True)
    (out / "SPU" / "img").mkdir(parents=True, exist_ok=True)
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}

    cells = disc_cells(vdir)
    readers, ifocache = {}, {}
    palettes, layers, units, imgs = {}, {}, {}, set()
    lines_c, lines_n, lines_h = [], [], []
    stats = Counter()

    def palette_id(pal):
        key = "".join(f"{r:02x}{g:02x}{b:02x}" for r, g, b in pal)
        if key not in palettes:
            palettes[key] = f"p{len(palettes) + 1}"
        return palettes[key], key

    def save_img(rgba, key):
        name = hashlib.md5(repr(key).encode()).hexdigest()[:12]
        if name not in imgs:
            rgba.save(out / "SPU" / "img" / f"{name}.png", optimize=True)
            imgs.add(name)
        return name

    for key in sorted(cells):
        folder = cue_path(key).parts[0]
        if only and folder not in only:
            continue
        dom, n, vob, cid = key
        c = cells[key]
        cue = ".".join(cue_path(key).with_suffix("").parts)
        rd = readers.setdefault((dom, n), VobReader(vob_set(vdir, dom, n)))
        data = rd.read(c["first"], c["last"] - c["first"] + 1)
        stats["cells"] += 1
        spus = demux_spu(data)
        pci = read_pci(data[:2048])
        has_hli = bool(pci and pci["hli_ss"])
        if not spus and not has_hli:
            continue

        ifo, offs = domain_pgcs(vdir, dom, n, ifocache)
        pals = {tuple(pgc_palette(ifo, o)) for o in pgcs_for_cell(ifo, offs, vob, cid)
                if not palette_is_empty(ifo, o)}
        if len(pals) > 1:
            stats["cells whose PGCs disagree on palette"] += 1
        pal = list(sorted(pals)[0]) if pals else [(0, 0, 0)] * 16
        pid, _ = palette_id(pal)

        streams = []
        for st, us in sorted(spus.items()):
            pts, buf = us[0]
            h = hashlib.md5(buf).hexdigest()[:12]
            if h not in units:
                info = decode_spu(buf)
                if "pixels" not in info:
                    units[h] = None
                else:
                    x1, x2, y1, y2 = info["area"]
                    w, hh = x2 - x1 + 1, y2 - y1 + 1
                    flat = bytes(v for row in info["pixels"] for v in row)
                    lid = hashlib.md5(flat + struct.pack(">4H", x1, x2, y1, y2)).hexdigest()[:12]
                    if lid not in layers:
                        img = Image.frombytes("L", (w, hh), flat)
                        img.save(out / "SPU" / "layers" / f"{lid}.png", optimize=True)
                        layers[lid] = (x1, y1, w, hh, img)
                    stop = next((q["delay"] for q in info["seqs"] if q.get("stop")), None)
                    units[h] = {"lid": lid, "area": info["area"], "color": info.get("color", [0] * 4),
                                "contrast": info.get("contrast", [0] * 4), "stop": stop,
                                "forced": any(q.get("forced") for q in info["seqs"])}
            u = units[h]
            if u:
                streams.append((st, u))

        schemes = "-"
        btns = []
        if has_hli:
            schemes = "".join(hex4(sc["select"][0]) + hex4(sc["select"][1]) +
                              hex4(sc["action"][0]) + hex4(sc["action"][1]) for sc in pci["schemes"])
            btns = [(b["n"], b["coln"], b["rect"]) for b in pci["buttons"]]
        lines_c.append(f"C {cue} {pid} " +
                       (";".join(f"{st}:{u['lid']}:{hex4(u['color'])}:{hex4(u['contrast'])}:"
                                 f"{'-' if u['stop'] is None else round(u['stop'], 3)}:{int(u['forced'])}"
                                 for st, u in streams) or "-") +
                       f" {schemes} " + (",".join(f"{bn}:{cs}" for bn, cs, _ in btns) or "-"))
        stats["cells with subpictures"] += bool(streams)

        if a.no_render:
            continue
        for st, u in streams:
            x1, y1, w, hh, img = layers[u["lid"]]
            area = (x1, x1 + w - 1, y1, y1 + hh - 1)
            if any(u["contrast"]):
                r = render_crop(img, area, pal, u["color"], u["contrast"])
                if r:
                    nm = save_img(r[0], ("N", u["lid"], pid, tuple(u["color"]), tuple(u["contrast"])))
                    lines_n.append(f"N {cue} {st} {nm} {r[1]} {r[2]} {r[0].width} {r[0].height} "
                                   f"{'-' if u['stop'] is None else round(u['stop'], 3)}")
            for bn, cs, rect in btns:
                if cs == 0:
                    continue
                sc = pci["schemes"][cs - 1]
                got = []
                for state in ("select", "action"):
                    r = render_crop(img, area, pal, sc[state][0], sc[state][1], clip=rect)
                    got.append(r)
                if not any(got):
                    continue
                # one placement box for both states: the union of their visible pixels
                xs = [g[1] for g in got if g] + [g[1] + g[0].width for g in got if g]
                ys = [g[2] for g in got if g] + [g[2] + g[0].height for g in got if g]
                bx, by, bw, bh = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
                names = []
                for state, g in zip(("select", "action"), got):
                    if not g:
                        names.append("-")
                        continue
                    canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
                    canvas.paste(g[0], (g[1] - bx, g[2] - by))
                    names.append(save_img(canvas, ("H", u["lid"], pid, rect, bx, by, bw, bh,
                                                   tuple(sc[state][0]), tuple(sc[state][1]))))
                lines_h.append(f"H {cue} {st} {bn} {names[0]} {names[1]} {bx} {by} {bw} {bh}")

    with open(out / f"{a.name}_spu.txt", "w") as f:
        f.write(f"D {a.name}\n")
        for key, pid in palettes.items():
            f.write(f"P {pid} " + " ".join(key[i:i + 6] for i in range(0, 96, 6)) + "\n")
        for lid, (x, y, w, h, _) in layers.items():
            f.write(f"L {lid} {x} {y} {w} {h}\n")
        for l in lines_c + lines_n + lines_h:
            f.write(l + "\n")
    stats["palettes"] = len(palettes)
    stats["raw layers"] = len(layers)
    stats["pre-rendered images"] = len(imgs)
    stats["highlight records"] = len(lines_h)
    stats["visible base-layer records"] = len(lines_n)
    print(dict(stats))
    size = sum(p.stat().st_size for p in (out / "SPU").rglob("*.png"))
    print(f"SPU folder: {size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
