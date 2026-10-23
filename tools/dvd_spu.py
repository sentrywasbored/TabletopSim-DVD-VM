#!/usr/bin/env python3
"""DVD subpicture (SPU) + button-highlight decoding, straight from VOB sectors.

Library used by the dump tools; also runnable for inspection:
    python dvd_spu.py <cell.vob> <IFO> <pgc offset or 'first'> <out prefix>

Pieces:
  demux_spu(data)          -> {stream: [(pts90k, spu_bytes), ...]}  reassembled SPU units
  decode_spu(buf)          -> display commands + 2-bit pixel map (list of rows)
  read_pci(pack)           -> highlight info from a NAV pack: button colour schemes
                              (select/action colour+contrast) and per-button colour index
  pgc_palette(ifo, pgcoff) -> 16 RGB tuples from the PGC colour table (YCrCb)
  render(...)              -> RGBA PIL image for "normal" display or a button's select/action look
"""
import struct
from PIL import Image


def u16(d, o): return struct.unpack(">H", d[o:o + 2])[0]
def u32(d, o): return struct.unpack(">I", d[o:o + 4])[0]


# ------------------------------------------------------------ demux
def pes_pts(p, o):
    flags = p[o + 7]
    if flags & 0x80:
        b = p[o + 9:o + 14]
        return (((b[0] >> 1) & 7) << 30) | (b[1] << 22) | ((b[2] >> 1) << 15) | (b[3] << 7) | (b[4] >> 1)
    return None


def demux_spu(data):
    """Walk 2048-byte packs; collect private-stream-1 substreams 0x20..0x3F."""
    out, partial = {}, {}
    for s in range(0, len(data) - 2047, 2048):
        p = data[s:s + 2048]
        if p[:4] != b"\x00\x00\x01\xba":
            continue
        o = 14 + (p[13] & 7)
        while o + 6 <= 2048 and p[o:o + 3] == b"\x00\x00\x01":
            sid, ln = p[o + 3], u16(p, o + 4)
            if sid == 0xBD:
                hl = p[o + 8]
                sub = p[o + 9 + hl]
                if 0x20 <= sub < 0x40:
                    st = sub - 0x20
                    pts = pes_pts(p, o)
                    payload = p[o + 10 + hl:o + 6 + ln]
                    cur = partial.get(st)
                    if cur is None or (pts is not None and len(cur[1]) >= cur[2]):
                        size = u16(payload, 0) if len(payload) >= 2 else 0
                        cur = [pts, bytearray(), size]
                        partial[st] = cur
                    cur[1] += payload
                    if cur[2] and len(cur[1]) >= cur[2]:
                        out.setdefault(st, []).append((cur[0], bytes(cur[1][:cur[2]])))
                        partial[st] = None
            o += 6 + ln
    return out


# ------------------------------------------------------------ decode
def decode_spu(buf):
    size, dcsq = u16(buf, 0), u16(buf, 2)
    info = {"seqs": []}
    off = dcsq
    seen = set()
    while off not in seen and off + 4 <= size:
        seen.add(off)
        delay, nxt = u16(buf, off), u16(buf, off + 2)
        i = off + 4
        seq = {"delay": delay * 1024 / 90000}
        while i < size:
            c = buf[i]; i += 1
            if c == 0x00: seq["forced"] = True
            elif c == 0x01: seq["start"] = True
            elif c == 0x02: seq["stop"] = True
            elif c == 0x03:
                v = u16(buf, i); i += 2
                seq["color"] = [(v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15]   # e2 e1 p b
            elif c == 0x04:
                v = u16(buf, i); i += 2
                seq["contrast"] = [(v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15]
            elif c == 0x05:
                b = buf[i:i + 6]; i += 6
                seq["area"] = ((b[0] << 4) | (b[1] >> 4), ((b[1] & 15) << 8) | b[2],
                               (b[3] << 4) | (b[4] >> 4), ((b[4] & 15) << 8) | b[5])  # x1 x2 y1 y2
            elif c == 0x06:
                seq["offsets"] = (u16(buf, i), u16(buf, i + 2)); i += 4
            elif c == 0x07:
                n = u16(buf, i); i += n; seq["chg_colcon"] = True
            elif c == 0xFF: break
            else: break
        info["seqs"].append(seq)
        for k in ("area", "offsets", "color", "contrast"):
            if k in seq and k not in info:
                info[k] = seq[k]
        if nxt == off:
            break
        off = nxt
    if "area" in info and "offsets" in info:
        x1, x2, y1, y2 = info["area"]
        w, h = x2 - x1 + 1, y2 - y1 + 1
        info["pixels"] = rle_decode(buf, info["offsets"], w, h)
    return info


def rle_decode(buf, offsets, w, h):
    rows = [None] * h
    for field in (0, 1):
        pos = [offsets[field] * 2]          # nibble position

        def nib():
            v = (buf[pos[0] // 2] >> (4 if pos[0] % 2 == 0 else 0)) & 15
            pos[0] += 1
            return v
        for y in range(field, h, 2):
            row, x = [], 0
            while x < w:
                v = nib()
                if v < 0x4:
                    v = (v << 4) | nib()
                    if v < 0x10:
                        v = (v << 4) | nib()
                        if v < 0x40:
                            v = (v << 4) | nib()
                run, col = v >> 2, v & 3
                if run == 0:
                    run = w - x
                run = min(run, w - x)
                row += [col] * run
                x += run
            if pos[0] % 2:
                pos[0] += 1                 # rows are byte aligned
            rows[y] = row
    return rows


# ------------------------------------------------------------ NAV / IFO
def read_pci(pack):
    """Highlight info from a NAV pack (PCI starts at 0x2D)."""
    if pack[0x26:0x2A] != b"\x00\x00\x01\xbf":
        return None
    p = 0x2D
    hli = p + 0x60
    hli_ss = u16(pack, hli) & 3
    btn_ns = pack[hli + 17]
    schemes = []
    for i in range(3):
        o = hli + 22 + 8 * i
        def cc(v): return [(v >> 28) & 15, (v >> 24) & 15, (v >> 20) & 15, (v >> 16) & 15], \
                          [(v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15]
        sc, sk = cc(u32(pack, o))
        ac, ak = cc(u32(pack, o + 4))
        schemes.append({"select": (sc, sk), "action": (ac, ak)})
    buttons = []
    for i in range(btn_ns):
        o = hli + 46 + 18 * i
        b = pack[o:o + 18]
        coln = b[0] >> 6
        x0 = ((b[0] & 0x3F) << 4) | (b[1] >> 4)
        x1 = ((b[1] & 3) << 8) | b[2]
        y0 = ((b[3] & 0x3F) << 4) | (b[4] >> 4)
        y1 = ((b[4] & 3) << 8) | b[5]
        buttons.append({"n": i + 1, "coln": coln, "rect": (x0, y0, x1, y1)})
    return {"hli_ss": hli_ss, "schemes": schemes, "buttons": buttons}


def pgc_palette(ifo_bytes, pgc_off):
    """16 RGB entries from a PGC's colour table (0xA4: 16 x [0, Y, Cr, Cb]).
    DVD palettes are BT.601 *limited range* (Y 16-235, Cr/Cb 16-240). The table holds
    no alpha at all: transparency comes only from the 4-bit contrast values (SPU
    SET_CONTR for normal display, PCI colour schemes for highlights)."""
    pal = []
    for i in range(16):
        _, y, cr, cb = ifo_bytes[pgc_off + 0xA4 + 4 * i:pgc_off + 0xA8 + 4 * i]
        yy = 1.164383 * (y - 16)
        r = yy + 1.596027 * (cr - 128)
        g = yy - 0.391762 * (cb - 128) - 0.812968 * (cr - 128)
        b = yy + 2.017232 * (cb - 128)
        pal.append(tuple(max(0, min(255, int(round(v)))) for v in (r, g, b)))
    return pal


def palette_is_empty(ifo_bytes, pgc_off):
    return not any(ifo_bytes[pgc_off + 0xA4:pgc_off + 0xE4])


def pgcs_for_cell(ifo_bytes, pgc_offsets, vobid, cellid):
    """PGC offsets whose cell table plays (vobid, cellid) -- the palette is per PGC,
    so a cell's colours must come from a PGC that actually plays it."""
    out = []
    for o in pgc_offsets:
        ncell, cpos = ifo_bytes[o + 3], u16(ifo_bytes, o + 0xEA)
        for i in range(ncell):
            q = o + cpos + 4 * i
            if u16(ifo_bytes, q) == vobid and ifo_bytes[q + 3] == cellid:
                out.append(o)
                break
    return out


# ------------------------------------------------------------ render
def render(info, palette, colors=None, contrast=None, rect=None, size=(720, 480)):
    """RGBA overlay. colors/contrast: 4 entries in e2,e1,p,b order (pixel values 3,2,1,0).
    rect limits drawing to a button rectangle (highlight); None = whole SPU area."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if "pixels" not in info:
        return img
    colors = colors or info.get("color", [0, 0, 0, 0])
    contrast = contrast or info.get("contrast", [0, 0, 0, 0])
    lut = {}
    for px in range(4):
        k = 3 - px                                   # e2,e1,p,b -> pixel 3,2,1,0
        c = palette[colors[k]]
        a = contrast[k] * 17                         # 4-bit contrast 0..15 -> alpha 0..255
        lut[px] = (c[0], c[1], c[2], a)
    x1, x2, y1, y2 = info["area"]
    pix = img.load()
    for dy, row in enumerate(info["pixels"]):
        y = y1 + dy
        if y >= size[1]: break
        for dx, v in enumerate(row):
            x = x1 + dx
            if x >= size[0]: break
            if rect and not (rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]):
                continue
            px = lut[v]
            if px[3]:
                pix[x, y] = px
    return img
