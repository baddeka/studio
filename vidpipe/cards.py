import math
import re
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import cfg, font_path
from .util import hex_rgb

FONT_FILES = {"display": "font_display", "body": "font_body", "mono": "font_mono", "monob": "font_mono_bold"}


@lru_cache(maxsize=512)
def font(kind, size, weight=400):
    f = ImageFont.truetype(font_path(FONT_FILES[kind]), max(8, int(size)))
    if kind == "body":
        try:
            f.set_variation_by_axes([min(32, max(14, size / 3)), weight])
        except Exception:
            pass
    return f


def col(name):
    return hex_rgb(cfg()["style"][name])


def mix(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def ease_out(t):
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def ease_io(t):
    t = min(1.0, max(0.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def ramp(t, a, b):
    if b <= a:
        return 1.0 if t >= b else 0.0
    return min(1.0, max(0.0, (t - a) / (b - a)))


@lru_cache(maxsize=8)
def background(w, h):
    vertical = h > w
    cx, cy = (0.5 * w, 0.36 * h) if vertical else (0.68 * w, 0.42 * h)
    r = 0.75 * max(w, h)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
    k = np.clip(1 - d / 0.85, 0, 1) ** 1.8
    bg = np.array(col("bg"), np.float32)
    glow = np.array(col("glow"), np.float32)
    img = bg[None, None, :] * (1 - k[..., None]) + glow[None, None, :] * k[..., None]
    return Image.fromarray(img.clip(0, 255).astype(np.uint8), "RGB")


def wrap(text, f, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if f.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(kind, text, max_w, start, minimum, weight=400, max_lines=1):
    size = start
    while size > minimum:
        f = font(kind, size, weight)
        lines = wrap(text, f, max_w) if max_lines > 1 else [text]
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        size = int(size * 0.93)
    f = font(kind, minimum, weight)
    return f, wrap(text, f, max_w)


def text_h(f):
    a, d = f.getmetrics()
    return a + d


def draw_center(d, x, y, text, f, fill, spacing=0):
    if spacing:
        total = sum(f.getlength(ch) for ch in text) + spacing * (len(text) - 1)
        cx = x - total / 2
        for ch in text:
            d.text((cx, y), ch, font=f, fill=fill)
            cx += f.getlength(ch) + spacing
        return
    d.text((x - f.getlength(text) / 2, y), text, font=f, fill=fill)


def draw_spaced(d, x, y, text, f, fill, spacing):
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + spacing


NUM_RE = re.compile(r"^(?P<pre>[^\d]*?)(?P<num>\d[\d,]*(?:\.\d+)?)(?P<suf>.*)$")


def count_text(text, p):
    """Text with its number counted up to p (0..1), keeping commas and decimals."""
    m = NUM_RE.match(text.strip())
    if not m or p >= 1:
        return text
    raw = m.group("num")
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    value = float(raw.replace(",", ""))
    cur = value * p
    s = f"{cur:,.{decimals}f}" if "," in raw else f"{cur:.{decimals}f}"
    return f"{m.group('pre')}{s}{m.group('suf')}"


# In a Short the hook line sits at the top and the word-by-word captions in the lower third (timing.shorts_ass),
# so card content is kept between these fractions of the height.
SHORT_SAFE = (0.16, 0.57)


class Card:
    def __init__(self, card, w, h):
        self.c = card
        self.w, self.h = w, h
        self.vertical = h > w
        self.u = (w / 1080 * 1.4) if self.vertical else (h / 1080)
        tone = card.get("tone", "neutral")
        self.accent = col("red") if tone in ("loss", "alert") else col("gold")
        self.base = background(w, h)
        self.bg = col("bg")
        self.anim_end = 1.6
        self._fit = False

    def _paint(self, target, t):
        getattr(self, "draw_" + self.c["kind"], self.draw_text)(target, ImageDraw.Draw(target), t)

    def frame(self, t):
        img = self.base.copy()
        if not self.vertical:
            self._paint(img, t)
            return img
        # Vertical (Shorts): draw on a clear layer, then fit it between the hook line and the captions.
        if self._fit is False:
            self._fit = self._short_fit()
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        self._paint(layer, t)
        if self._fit is None:
            img.paste(layer, (0, 0), layer)
            return img
        box, scale, dest = self._fit
        part = layer.crop(box)
        if scale < 0.999:
            part = part.resize((max(1, round(part.width * scale)), max(1, round(part.height * scale))), Image.LANCZOS)
        img.paste(part, dest, part)
        return img

    def _short_fit(self):
        """Where the finished card goes in a Short: one fixed box for every frame, so nothing jumps."""
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        self._paint(layer, 60.0)
        box = layer.getchannel("A").getbbox()
        if not box:
            return None
        top, bottom = SHORT_SAFE[0] * self.h, SHORT_SAFE[1] * self.h
        if box[1] >= top and box[3] <= bottom:
            return None
        bw, bh = box[2] - box[0], box[3] - box[1]
        scale = min(1.0, (bottom - top) / bh, self.w * 0.92 / bw)
        nw, nh = bw * scale, bh * scale
        cx = (box[0] + box[2]) / 2
        x = min(max(cx - nw / 2, self.w * 0.04), self.w * 0.96 - nw)
        y = top + (bottom - top - nh) / 2
        return box, scale, (int(round(x)), int(round(y)))

    def fade(self, color, a):
        return mix(self.bg, color, a)

    def draw_stat(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        text = self.c["text"] or ""
        self.anim_end = 1.5
        p = ease_out(ramp(t, 0.0, 1.3))
        scale = 0.94 + 0.06 * ease_out(ramp(t, 0, 0.6))
        a = ramp(t, 0, 0.25)
        start = 300 * u if not self.vertical else 230 * u
        f, _ = fit("display", text, w * 0.84, start, 60 * u)
        f = font("display", f.size * scale)
        shown = count_text(text, p)
        y = h * (0.40 if not self.vertical else 0.36) - text_h(f) / 2
        draw_center(d, w / 2, y, shown, f, self.fade(self.accent, a))
        line_w = w * (0.16 if not self.vertical else 0.3) * ease_out(ramp(t, 0.45, 1.05))
        ly = y + text_h(f) + 18 * u
        if line_w > 1:
            d.rectangle([w / 2 - line_w / 2, ly, w / 2 + line_w / 2, ly + 6 * u], fill=self.accent)
        sub = self.c.get("sub") or ""
        if sub:
            fs = font("body", 50 * u, 500)
            sa = ramp(t, 0.55, 1.1)
            for i, line in enumerate(wrap(sub, fs, w * 0.72)):
                draw_center(d, w / 2, ly + 40 * u + i * text_h(fs) * 1.1, line, fs, self.fade(col("soft"), sa))

    def draw_quote(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        text = (self.c["text"] or "").strip().strip("\"“”")
        x0 = w * (0.14 if not self.vertical else 0.09)
        max_w = w * (0.72 if not self.vertical else 0.82)
        start = 96 if len(text) < 60 else 82 if len(text) < 110 else 72
        f, lines = fit("body", text, max_w, start * u, 40 * u, 500, max_lines=6 if not self.vertical else 9)
        lh = text_h(f) * 1.18
        block = lh * len(lines)
        y0 = (h - block) / 2 - 20 * u
        qa = ramp(t, 0, 0.3)
        fq = font("display", 220 * u)
        d.text((x0 - 20 * u, y0 - 170 * u), "“", font=fq, fill=self.fade(col("gold"), qa))
        total = sum(len(l) for l in lines)
        reveal = min(2.6, 0.3 + total * 0.03)
        shown = int(total * ease_io(ramp(t, 0.2, 0.2 + reveal)))
        self.anim_end = 0.2 + reveal + 0.8
        used = 0
        for i, line in enumerate(lines):
            vis = line[:max(0, shown - used)]
            if vis:
                d.text((x0, y0 + i * lh), vis, font=f, fill=col("text"))
            used += len(line)
        sub = self.c.get("sub") or ""
        if sub:
            fs = font("body", 38 * u, 400)
            fill = self.fade(col("muted"), ramp(t, 0.2 + reveal, 0.2 + reveal + 0.5))
            for i, line in enumerate(wrap("— " + sub, fs, max_w)):
                d.text((x0, y0 + block + 30 * u + i * text_h(fs) * 1.1), line, font=fs, fill=fill)

    def draw_date(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        text = (self.c["text"] or "").upper()
        self.anim_end = 1.4
        f, _ = fit("display", text, w * 0.84, 170 * u, 50 * u)
        a = ramp(t, 0, 0.35)
        rise = (1 - ease_out(ramp(t, 0, 0.6))) * 30 * u
        y = h * 0.36 - text_h(f) / 2 + rise
        draw_center(d, w / 2, y, text, f, self.fade(col("gold"), a))
        ly = h * 0.58
        x1 = w * 0.1 + (w * 0.8) * ease_io(ramp(t, 0.2, 1.1))
        d.rectangle([w * 0.1, ly, x1, ly + 4 * u], fill=self.fade(col("line"), 1.0))
        d.rectangle([w * 0.1, ly, min(x1, w / 2), ly + 4 * u], fill=col("gold"))
        if x1 >= w / 2:
            r = 13 * u * ease_out(ramp(t, 0.65, 0.95))
            d.ellipse([w / 2 - r, ly + 2 * u - r, w / 2 + r, ly + 2 * u + r], fill=col("gold"))
        sub = self.c.get("sub") or ""
        if sub:
            fs = font("body", 50 * u, 500)
            for i, line in enumerate(wrap(sub, fs, w * 0.74)):
                draw_center(d, w / 2, ly + 50 * u + i * text_h(fs) * 1.1, line, fs,
                            self.fade(col("soft"), ramp(t, 0.6, 1.2)))

    def draw_title(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        self.anim_end = 1.3
        sub = (self.c.get("sub") or "").upper()
        text = self.c["text"] or ""
        f, lines = fit("display", text.upper(), w * 0.8, 150 * u, 60 * u, max_lines=2)
        lh = text_h(f)
        y0 = h * 0.5 - lh * len(lines) / 2
        if sub:
            fs = font("body", 34 * u, 600)
            draw_center(d, w / 2, y0 - 70 * u, sub, fs, self.fade(col("gold"), ramp(t, 0, 0.4)), spacing=6 * u)
        for i, line in enumerate(lines):
            a = ramp(t, 0.1 + 0.15 * i, 0.6 + 0.15 * i)
            draw_center(d, w / 2, y0 + i * lh + (1 - ease_out(a)) * 24 * u, line, f, self.fade(col("text"), a))
        lw = w * 0.12 * ease_out(ramp(t, 0.5, 1.1))
        if lw > 1:
            ly = y0 + lh * len(lines) + 24 * u
            d.rectangle([w / 2 - lw / 2, ly, w / 2 + lw / 2, ly + 5 * u], fill=col("gold"))

    def draw_text(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        text = self.c.get("text") or ""
        f, lines = fit("display", text.upper(), w * 0.8, 130 * u, 50 * u, max_lines=3)
        lh = text_h(f) * 1.05
        y0 = h * 0.47 - lh * len(lines) / 2
        words_total = sum(len(l.split()) for l in lines)
        self.anim_end = 0.25 * words_total + 0.5
        k = 0
        for i, line in enumerate(lines):
            ws = line.split()
            x = w / 2 - f.getlength(line) / 2
            for wd in ws:
                a = ramp(t, 0.12 * k, 0.12 * k + 0.3)
                color = self.accent if k == words_total - 1 else col("text")
                d.text((x, y0 + i * lh), wd, font=f, fill=self.fade(color, a))
                x += f.getlength(wd + " ")
                k += 1
        sub = self.c.get("sub") or ""
        if sub:
            fs = font("body", 42 * u, 500)
            draw_center(d, w / 2, y0 + lh * len(lines) + 30 * u, sub, fs,
                        self.fade(col("muted"), ramp(t, 0.4, 0.9)))

    def draw_list(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        items = self.c.get("items") or []
        x0 = w * (0.14 if not self.vertical else 0.09)
        head = self.c.get("text") or ""
        fh, hlines = fit("display", head.upper(), w * 0.75, 96 * u, 48 * u, max_lines=2)
        fi = font("body", (54 if not self.vertical else 50) * u, 600)
        fn = font("monob", 34 * u)
        item_lines = [wrap(it, fi, w * 0.66) for it in items]
        block = text_h(fh) * len(hlines) + 50 * u + sum(len(l) * text_h(fi) * 1.12 + 34 * u for l in item_lines)
        y = (h - block) / 2
        for line in hlines:
            d.text((x0, y), line, font=fh, fill=self.fade(col("text"), ramp(t, 0, 0.35)))
            y += text_h(fh)
        y += 50 * u
        self.anim_end = 0.4 + 0.45 * len(items) + 0.4
        for i, lines in enumerate(item_lines):
            a = ramp(t, 0.4 + 0.45 * i, 0.75 + 0.45 * i)
            dx = (1 - ease_out(a)) * -30 * u
            d.text((x0 + dx, y + 10 * u), f"{i + 1:02d}", font=fn, fill=self.fade(col("gold"), a))
            for j, line in enumerate(lines):
                d.text((x0 + 90 * u + dx, y + j * text_h(fi) * 1.12), line, font=fi, fill=self.fade(col("text"), a))
            y += len(lines) * text_h(fi) * 1.12 + 34 * u

    def draw_compare(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        items = (self.c.get("items") or [])[:2]
        while len(items) < 2:
            items.append("")
        self.anim_end = 1.8
        head = (self.c.get("text") or "").upper()
        if head:
            fh = font("body", 36 * u, 600)
            draw_center(d, w / 2, h * (0.14 if not self.vertical else 0.12), head, fh,
                        self.fade(col("gold"), ramp(t, 0, 0.4)), spacing=4 * u)
        p = ease_out(ramp(t, 0.3, 1.5))
        for k, item in enumerate(items):
            label, _, value = item.partition(":")
            value = value.strip() or label
            label = label.strip() if value != label else ""
            if self.vertical:
                cx, cy = w / 2, h * (0.36 if k == 0 else 0.66)
                max_w = w * 0.84
            else:
                cx, cy = w * (0.29 if k == 0 else 0.71), h * 0.5
                max_w = w * 0.38
            fv, _ = fit("display", value, max_w, 170 * u, 50 * u)
            color = col("gold") if k == 0 else self.accent
            a = ramp(t, 0.15 + 0.3 * k, 0.6 + 0.3 * k)
            fl = font("body", 40 * u, 500)
            draw_center(d, cx, cy - text_h(fv) / 2 - 60 * u, label, fl, self.fade(col("soft"), a))
            draw_center(d, cx, cy - text_h(fv) / 2 + 10 * u, count_text(value, p), fv, self.fade(color, a))
        if self.vertical:
            d.rectangle([w * 0.3, h * 0.51, w * 0.7, h * 0.51 + 3 * u], fill=col("line"))
        else:
            lh = h * 0.42 * ease_io(ramp(t, 0.2, 0.9))
            d.rectangle([w / 2 - 1.5 * u, h / 2 - lh / 2, w / 2 + 1.5 * u, h / 2 + lh / 2], fill=col("line"))

    def draw_document(self, img, d, t):
        u, w, h = self.u, self.w, self.h
        items = list(self.c.get("items") or [])
        hl = (self.c.get("text") or "").strip()
        if hl and not any(hl.lower() in it.lower() or it.lower() in hl.lower() for it in items):
            items.append(hl)
        hi = next((i for i, it in enumerate(items) if hl and (hl.lower() in it.lower() or it.lower() in hl.lower())), -1)
        self.anim_end = 1.8
        pw = int(w * (0.4 if not self.vertical else 0.82))
        fm = font("mono", 27 * u)
        fb = font("monob", 30 * u)
        body = [wrap(it, fm, pw - 110 * u) for it in items]
        ph = int(150 * u + sum(len(b) * text_h(fm) * 1.3 + 24 * u for b in body) + 30 * u)
        paper = Image.new("RGBA", (pw, ph), col("paper") + (255,))
        pd = ImageDraw.Draw(paper)
        pd.text((55 * u, 50 * u), (self.c.get("sub") or "DOCUMENT").upper()[:40], font=fb, fill=col("ink"))
        pd.rectangle([55 * u, 50 * u + text_h(fb) + 14 * u, pw - 55 * u, 50 * u + text_h(fb) + 17 * u], fill=col("ink"))
        y = 50 * u + text_h(fb) + 50 * u
        sweep = ease_io(ramp(t, 0.9, 1.5))
        for i, lines in enumerate(body):
            if i == hi and sweep > 0:
                bh = len(lines) * text_h(fm) * 1.3
                pd.rectangle([45 * u, y - 6 * u, 45 * u + (pw - 90 * u) * sweep, y + bh - 4 * u],
                             fill=col("gold") + (255,))
            for j, line in enumerate(lines):
                pd.text((55 * u, y + j * text_h(fm) * 1.3), line, font=fm, fill=col("ink"))
            y += len(lines) * text_h(fm) * 1.3 + 24 * u
        a = ease_out(ramp(t, 0, 0.6))
        angle = -2.5 + (1 - a) * -2
        rot = paper.rotate(angle, resample=Image.BICUBIC, expand=True)
        shadow = Image.new("RGBA", rot.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 150), mask=rot.split()[3])
        shadow = shadow.filter(ImageFilter.GaussianBlur(18 * u))
        if self.vertical:
            px, py = (w - rot.width) / 2, h * 0.17 + (1 - a) * 60 * u
        else:
            px, py = w * 0.07, (h - rot.height) / 2 + (1 - a) * 60 * u
        if a < 1:
            alpha = rot.split()[3].point(lambda v: int(v * a))
            rot.putalpha(alpha)
            shadow.putalpha(shadow.split()[3].point(lambda v: int(v * a)))
        img.paste(shadow, (int(px + 14 * u), int(py + 24 * u)), shadow)
        img.paste(rot, (int(px), int(py)), rot)
        if hl:
            f, lines = fit("display", hl, w * (0.4 if not self.vertical else 0.84), 76 * u, 40 * u, max_lines=4)
            if self.vertical:
                tx, ty = w * 0.08, py + rot.height + 50 * u
            else:
                tx, ty = w * 0.54, h / 2 - text_h(f) * len(lines) / 2
            ta = ramp(t, 0.5, 1.0)
            label = (self.c.get("sub") or "").upper()
            if label:
                fl = font("body", 28 * u, 700)
                draw_spaced(d, tx, ty - text_h(fl) - 22 * u, label[:48], fl, self.fade(col("gold"), ta), 3 * u)
            for i, line in enumerate(lines):
                d.text((tx, ty + i * text_h(f) * 1.05), line, font=f, fill=self.fade(col("text"), ta))


def card_frames(card, w, h, fps, frames):
    c = Card(card, w, h)
    last = None
    for i in range(frames):
        t = i / fps
        if last is not None and t > c.anim_end:
            yield last
            continue
        last = c.frame(t).tobytes()
        yield last


def preview(card, w=1920, h=1080, t=3.0):
    return Card(card, w, h).frame(t)


def placeholder(scene, w, h):
    img = Image.new("RGB", (w, h), col("panel"))
    d = ImageDraw.Draw(img)
    vertical = h > w
    u = (w / 1080) if vertical else (h / 1080)
    d.rectangle([24 * u, 24 * u, w - 24 * u, h - 24 * u], outline=col("line"), width=max(1, int(3 * u)))
    # Starts below the chapter title and stays above the lower third (or the captions of a Short).
    x, y = 80 * u, h * (0.20 if vertical else 0.27)
    fid = font("display", 96 * u)
    d.text((x, y), scene["id"], font=fid, fill=col("muted"))
    y += text_h(fid) + 4 * u
    need = "CLIP NEEDED (KLING)" if scene["type"] == "motion" else "IMAGE NEEDED (MIDJOURNEY)"
    if scene.get("_need") == "image":
        need = "IMAGE NEEDED (MIDJOURNEY)"
    fn = font("body", 36 * u, 700)
    d.text((x, y), need, font=fn, fill=col("red"))
    y += text_h(fn) + 40 * u
    fv = font("body", 46 * u, 500)
    for line in wrap(scene.get("visual") or "", fv, w - 160 * u)[:4]:
        d.text((x, y), line, font=fv, fill=col("soft"))
        y += text_h(fv) * 1.15
    fp = font("mono", 24 * u)
    y += 24 * u
    for line in wrap(scene.get("mj_prompt") or "", fp, w - 160 * u)[:3]:
        d.text((x, y), line, font=fp, fill=col("muted"))
        y += text_h(fp) * 1.3
    return img


def soft_shade(width, height, strength, power=1.2, feather=0.35):
    """Black RGBA panel that fades out to the right and softly at the top and bottom edges."""
    width, height = max(2, int(width)), max(2, int(height))
    xs = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    e = np.clip(np.minimum(ys, 1 - ys) / feather, 0, 1)
    e = e * e * (3 - 2 * e)
    alpha = (strength * (1 - xs) ** power * e).clip(0, 255).astype(np.uint8)
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    shade.putalpha(Image.fromarray(alpha, "L"))
    return shade


def lower_third(text, w, h):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    u = (w / 1080) if h > w else (h / 1080)
    f = font("body", 36 * u, 600)
    tw = f.getlength(text)
    x, y = (w * 0.06, h * 0.84) if w > h else (w * 0.07, h * 0.70)
    shade = soft_shade(tw + 220 * u, 150 * u, 190)
    img.alpha_composite(shade, (max(0, int(x - 60 * u)), max(0, int(y - 50 * u))))
    d = ImageDraw.Draw(img)
    d.rectangle([x, y, x + 7 * u, y + text_h(f)], fill=col("gold") + (255,))
    d.text((x + 24 * u, y), text, font=f, fill=col("text") + (255,))
    return img


def chapter_overlay(n, title, w, h):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    u = (w / 1080) if h > w else (h / 1080)
    x, y = w * 0.06, h * 0.08
    shade = soft_shade(w * 0.62, 330 * u, 175, power=1.5, feather=0.3)
    img.alpha_composite(shade, (0, max(0, int(y - 75 * u))))
    d = ImageDraw.Draw(img)
    fl = font("body", 30 * u, 700)
    draw_spaced(d, x, y, f"PART {n}", fl, col("gold") + (255,), 6 * u)
    ft, lines = fit("display", title.upper(), w * 0.7, 86 * u, 40 * u, max_lines=2)
    for i, line in enumerate(lines):
        d.text((x, y + text_h(fl) + 10 * u + i * text_h(ft)), line, font=ft, fill=col("text") + (255,))
    return img
