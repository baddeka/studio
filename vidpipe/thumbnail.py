import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .cards import background, col, font, text_h
from .cut import IMAGE_EXT, find_assets
from .render import cover
from .util import read_json, say

W, H = 1280, 720


def pick_background(video):
    for name in ("thumb", "thumbnail"):
        for ext in IMAGE_EXT:
            p = video.dir / "assets" / f"{name}{ext}"
            if p.exists():
                return p
    shot = read_json(video.shotlist, {})
    assets = find_assets(video)
    for t in ("motion", "still"):
        for sc in shot.get("scenes", []):
            a = assets.get(sc["id"].upper(), {})
            if sc["type"] == t and a.get("image"):
                return a["image"]
    return None


def balance_lines(text, max_lines=3):
    words = text.split()
    if len(words) <= 1 or max_lines == 1:
        return [text]
    best = None
    for n in range(1, min(max_lines, len(words)) + 1):
        size = -(-len(words) // n)
        lines = [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
        widest = max(len(l) for l in lines)
        if best is None or widest < best[0]:
            best = (widest, lines)
    return best[1]


def render(text, bg_path, channel=""):
    if bg_path:
        img = cover(Image.open(bg_path).convert("RGB"), W, H)
    else:
        img = background(W, H).resize((W, H))
    arr = np.asarray(img).astype(np.float32)
    x = np.linspace(0, 1, W)[None, :, None]
    shade = np.clip(1.0 - x / 0.68, 0, 1) ** 1.4 * 0.82
    arr = arr * (1 - shade) + np.array(col("bg"), np.float32) * shade
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    lines = balance_lines(text.upper())
    max_w, max_h = W * 0.56, H * 0.74
    size = 260
    while size > 40:
        f = font("display", size)
        lh = text_h(f) * 0.98
        if max(f.getlength(l) for l in lines) <= max_w and lh * len(lines) <= max_h:
            break
        size -= 6
    f = font("display", size)
    lh = text_h(f) * 0.98
    y = (H - lh * len(lines)) / 2 - 10
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, line in enumerate(lines):
        number = bool(re.search(r"\d", line))
        color = col("gold") if number or i == 0 else (col("red") if i == len(lines) - 1 and len(lines) > 1 else col("text"))
        d.text((64, y + i * lh), line, font=f, fill=color + (255,), stroke_width=max(3, size // 28),
               stroke_fill=(0, 0, 0, 255))
    shadow = layer.split()[3].filter(ImageFilter.GaussianBlur(10))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.putalpha(shadow.point(lambda v: int(v * 0.7)))
    img = img.convert("RGBA")
    img.alpha_composite(sh, (6, 8))
    img.alpha_composite(layer)
    if channel:
        d2 = ImageDraw.Draw(img)
        fc = font("body", 26, 700)
        d2.text((66, H - 58), channel.upper(), font=fc, fill=col("soft") + (230,))
    return img.convert("RGB")


def build_thumbnails(video):
    pkg = read_json(video.package)
    if not pkg:
        raise SystemExit("Run vp package first.")
    bg = pick_background(video)
    out = []
    for i, text in enumerate(pkg["thumbnail_texts"], 1):
        p = video.edit(f"thumb_{i}.png")
        render(text, bg).save(p)
        out.append(p)
    src = bg.name if bg else "plain background (add assets/thumb.png from the Midjourney thumbnail prompt)"
    say(f"thumbnails: {len(out)} drafts on {src} -> edit/thumb_1..{len(out)}.png")
    return out
