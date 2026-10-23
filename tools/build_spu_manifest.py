#!/usr/bin/env python3
"""
Subpicture overlay manifest for GenerateSubpictureCues.cs.

    python build_spu_manifest.py <name>_spu.txt SPU --name 24 --out-dir manifests
                                 [--unity-dir Assets/24/SPU]

Every pre-rendered image dvd_spu_export.py wrote to SPU/img/ is one placement
(its id hashes the layer, palette, colours AND position), so each becomes one
Looping Effect, named "S_<id>". The host shows a highlight or base layer by
playing that effect on an overlay object -- the same mechanism as video cues,
no image URLs. Effect order:

    0  None           empty: nothing shown (also what the overlay autoplays)
    1  SPU.Calibrate  full-frame alignment chart (border, centre cross, corners)
    2+ S_<id>         every image, with its rectangle on the 720x480 DVD frame

Writes:
    manifests/<name>_spu_unity.json   -> GenerateSubpictureCues.cs
    SPU/calibrate.png                 the alignment chart
Sprite paths in the manifest point at --unity-dir (default Assets/SPU): copy the
SPU folder's img/ and calibrate.png to that folder in the Unity project.
"""
import argparse, json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FRAME_W, FRAME_H = 720, 480


def calibration_chart(path):
    img = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(4):                                   # 4 px red border
        d.rectangle([i, i, FRAME_W - 1 - i, FRAME_H - 1 - i], outline=(255, 40, 40, 255))
    for x in range(80, FRAME_W, 80):                     # faint grid every 80 px
        d.line([x, 0, x, FRAME_H], fill=(255, 255, 255, 70))
    for y in range(80, FRAME_H, 80):
        d.line([0, y, FRAME_W, y], fill=(255, 255, 255, 70))
    cx, cy = FRAME_W // 2, FRAME_H // 2                  # centre cross
    d.line([cx - 40, cy, cx + 40, cy], fill=(40, 255, 40, 255), width=3)
    d.line([cx, cy - 40, cx, cy + 40], fill=(40, 255, 40, 255), width=3)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
    except OSError:
        try:
            font = ImageFont.truetype("arialbd.ttf", 28)
        except OSError:
            font = ImageFont.load_default()
    for text, xy in (("TL", (12, 10)), ("TR", (FRAME_W - 52, 10)),
                     ("BL", (12, FRAME_H - 42)), ("BR", (FRAME_W - 52, FRAME_H - 42))):
        d.text(xy, text, fill=(255, 230, 40, 255), font=font)
    img.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spu_txt")
    ap.add_argument("spu_dir", help="the SPU folder dvd_spu_export.py wrote (holds img/)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out-dir", default="manifests")
    ap.add_argument("--unity-dir", default="Assets/SPU",
                    help="where SPU/ sits in the Unity project, e.g. Assets/24/SPU")
    a = ap.parse_args()
    spu = Path(a.spu_dir)
    udir = a.unity_dir.replace("\\", "/").rstrip("/")

    place, base = {}, set()                              # id -> (x, y, w, h); ids that are base layers
    for line in open(a.spu_txt):
        f = line.split()
        if f and f[0] == "H":                            # H cue s btn sel act x y w h
            for im in (f[4], f[5]):
                if im != "-":
                    place.setdefault(im, tuple(map(int, f[6:10])))   # both states share the box
        elif f and f[0] == "N":                          # N cue s img x y w h stop
            place.setdefault(f[3], tuple(map(int, f[4:8])))
            base.add(f[3])

    calibration_chart(spu / "calibrate.png")
    # lift: how far in front of the video each sits, in steps -- base layers 1, highlights 2
    # (both overlays load this bundle and can show at once; equal depths would flicker)
    cues = [{"cueName": "None", "spritePath": "", "x": 0, "y": 0, "w": 0, "h": 0, "lift": 0},
            {"cueName": "SPU.Calibrate", "spritePath": f"{udir}/calibrate.png",
             "x": 0, "y": 0, "w": FRAME_W, "h": FRAME_H, "lift": 3}]
    missing = 0
    have = {p.stem for p in (spu / "img").glob("*.png")}  # sizes come from the records, not the files
    for im, (x, y, w, h) in sorted(place.items()):
        if im not in have:
            missing += 1
            continue
        cues.append({"cueName": f"S_{im}", "spritePath": f"{udir}/img/{im}.png",
                     "x": x, "y": y, "w": w, "h": h, "lift": 1 if im in base else 2})
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.name}_spu_unity.json").write_text(json.dumps(
        {"frameWidth": FRAME_W, "frameHeight": FRAME_H, "cues": cues}, indent=1))
    print(f"{len(cues)} effects ({len(cues) - 2} images, {missing} missing) -> "
          f"{out / (a.name + '_spu_unity.json')}; wrote {spu / 'calibrate.png'}")


if __name__ == "__main__":
    main()
