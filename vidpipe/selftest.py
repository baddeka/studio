"""vp selftest: makes a 40-second test video with no internet and no paid tools, runs every render step on it
(draft cut, full cut with music and sound effects, loudness, captions, DaVinci timeline, a Short, thumbnails)
and checks the results. It proves that Python, ffmpeg, the fonts and the caption renderer work on this computer."""
import colorsys
import json
import math
import os
import platform
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .config import ROOT, VIDEOS, Video, cfg, ffmpeg_bin, font_path
from .util import read_json, run, say, write_json, write_text

SLUG = "_selftest"

SCRIPT = """# The vanishing fund

## Part 1: Cold open
On a cold Monday in March, the withdrawals stopped. Twelve thousand people refreshed the page and saw the same message. Four hundred million dollars was gone.

## Part 2: The promise
The founder promised a steady two percent every month, with no bad years. He said the money sat in safe government bonds. Auditors, he said, checked every account each quarter.

## Part 3: The collapse
Then a reporter asked a simple question. Who was the auditor? Nobody could answer, and within a week the office was empty.
"""

CARDS = {
    "stat": {"kind": "stat", "text": "$400,000,000", "sub": "customer money that vanished", "items": [], "tone": "loss"},
    "quote": {"kind": "quote", "text": "Your money sits in safe government bonds.", "sub": "The founder, 2022",
              "items": [], "tone": "neutral"},
    "document": {"kind": "document", "text": "Audit: not performed", "sub": "Quarterly report",
                 "items": ["Assets under management: $400M", "Holdings: government bonds", "Audit: not performed"],
                 "tone": "alert"},
    "question": {"kind": "text", "text": "Who was the auditor?", "sub": "", "items": [], "tone": "alert"},
}

# Scenes per part: (type, settings). Every kind of scene the pipeline renders appears at least once.
PATTERN = {
    1: [("still", {"camera": "push_in"}), ("motion", {"camera": "push_in"}), ("card", CARDS["stat"])],
    2: [("still", {"camera": "pan_right", "onscreen": "Riverside Capital, New York"}), ("card", CARDS["quote"]),
        ("card", CARDS["document"])],
    3: [("still", {"camera": "pull_out"}), ("card", CARDS["question"]), ("reuse", {"camera": "pan_left"})],
}
LEFT_OUT = "S2-01"  # no image for this scene in the draft, to test the placeholder


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail="", level="FAIL"):
        status = "ok" if ok else level
        self.rows.append((name, status, detail))
        say(f"  {status:<4}  {name}" + (f": {detail}" if detail else ""))
        return bool(ok)

    def failed(self):
        return [r for r in self.rows if r[1] == "FAIL"]

    def warned(self):
        return [r for r in self.rows if r[1] == "warn"]


# ---------------------------------------------------------------- tools


def _names(text, pattern):
    return set(re.findall(pattern, text, re.M))


def check_tools(r):
    """Python, ffmpeg (with the encoders and filters the pipeline uses) and the fonts. False if rendering cannot work."""
    r.check("Python 3.11 or newer", sys.version_info >= (3, 11), f"{platform.python_version()} at {sys.executable}")
    try:
        ff = ffmpeg_bin()
    except RuntimeError as e:
        return r.check("ffmpeg", False, str(e))
    first = run([ff, "-hide_banner", "-version"]).stdout.decode("utf-8", "replace").splitlines()
    r.check("ffmpeg", True, f"{(first or ['?'])[0].split(' Copyright')[0]} ({ff})")
    try:
        ffmpeg_bin("ffprobe")
        r.check("ffprobe", True)
    except RuntimeError as e:
        return r.check("ffprobe", False, str(e))
    enc = _names(run([ff, "-hide_banner", "-encoders"]).stdout.decode("utf-8", "replace"), r"^\s*[VAS][A-Z.]{5}\s+(\S+)")
    need_enc = ("libx264", "aac", "flac", "pcm_s24le")
    miss = [e for e in need_enc if e not in enc]
    ok = r.check("ffmpeg encoders", not miss, "missing " + ", ".join(miss) if miss else ", ".join(need_enc))
    flt = _names(run([ff, "-hide_banner", "-filters"]).stdout.decode("utf-8", "replace"),
                 r"^\s*[A-Z.]{2,3}\s+(\S+)\s+\S+->\S+")
    need_flt = ("ass", "loudnorm", "alimiter", "acompressor", "sidechaincompress", "amix", "acrossfade", "afade",
                "noise", "vignette", "overlay", "tpad", "zoompan")
    miss = [f for f in need_flt if f not in flt]
    ok = r.check("ffmpeg filters", not miss, "missing " + ", ".join(miss) if miss else f"{len(need_flt)} needed, all there") and ok
    lib = (ROOT / "library").resolve()
    bad = []
    for key in ("font_display", "font_body", "font_mono", "font_mono_bold"):
        p = Path(font_path(key))
        if not p.exists() or lib not in p.resolve().parents:
            bad.append(f"{key} -> {p}")
    ok = r.check("fonts in library/fonts", not bad, "; ".join(bad) if bad else "Anton, Inter, IBM Plex Mono") and ok
    return ok


def check_captions(r, work):
    """Burn one caption onto a black frame: proves libass works and finds the Anton font."""
    work.mkdir(parents=True, exist_ok=True)
    ass = work / "captions_test.ass"
    write_text(ass, "[Script Info]\nScriptType: v4.00+\nPlayResX: 540\nPlayResY: 960\n\n[V4+ Styles]\n"
               "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
               "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
               "MarginL, MarginR, MarginV, Encoding\n"
               "Style: Cap,Anton,90,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,10,10,10,1"
               "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
               "Dialogue: 0,0:00:00.00,0:00:05.00,Cap,,0,0,0,,CAPTIONS OK\n")
    fonts_rel = os.path.relpath(ROOT / "library" / "fonts", work).replace("\\", "/")
    res = run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i", "color=black:s=540x960:d=1",
               "-vf", f"ass={ass.name}:fontsdir={fonts_rel}", "-frames:v", "1", "captions_test.png"], cwd=work)
    with Image.open(work / "captions_test.png") as im:
        lit = int((np.asarray(im.convert("L")) > 128).sum())
    r.check("captions burn into the picture (libass)", lit > 1500, f"{lit} text pixels")
    log = (res.stderr or b"").decode("utf-8", "replace")
    m = re.search(r"fontselect: \(Anton[^)]*\) -> ([^,\n]+)", log)
    used = m.group(1).strip() if m else "unknown"
    r.check("captions use the Anton font", "anton" in used.lower(), f"libass picked {used}", level="warn")


# ---------------------------------------------------------------- inputs


def fake_image(scene, path, k, size=(1456, 816)):
    """A stand-in for a Midjourney image: a dark gradient, gold rings and the scene id."""
    from .cards import font, wrap
    w, h = size
    hue = (k * 0.137) % 1.0
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.55, 0.35))
    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img)
    for i in range(0, h, 4):
        shade = int(40 * i / h)
        d.line([(0, i), (w, i)], fill=tuple(max(0, c - shade) for c in top))
    for j in range(6):
        x = (j * 263 + k * 71) % w
        d.ellipse([x - 90, h * 0.35 - 90 + j * 30, x + 90, h * 0.35 + 90 + j * 30], outline=(232, 181, 74), width=6)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    d = ImageDraw.Draw(img)
    d.text((60, 50), scene["id"], font=font("display", 90), fill=(242, 239, 232))
    fv = font("body", 34, 500)
    for i, line in enumerate(wrap(scene.get("visual") or "", fv, w - 120)[:4]):
        d.text((60, 170 + i * 46), line, font=fv, fill=(220, 214, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def fake_clip(image, path, seconds=5):
    """A stand-in for a Kling clip: a slow zoom into the image, 720p at 24 fps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg_bin(), "-y", "-v", "error", "-loop", "1", "-i", str(image), "-vf",
         f"scale=1280:720,zoompan=z='1+0.0015*on':d={seconds * 24}:s=1280x720:fps=24", "-t", str(seconds),
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)])


def fake_speech(text, seed=0):
    """A robotic stand-in for a narrator: one voiced burst per word and pauses after punctuation.
    Returns the samples and per-character timings, in the same form ElevenLabs returns them."""
    from .voice import SR
    rng = np.random.default_rng(seed)
    pieces, chars, starts, ends = [], [], [], []
    pos = int(SR * 0.15)
    pieces.append(np.zeros(pos, np.float32))
    words = text.split(" ")
    for k, word in enumerate(words):
        n = int(SR * (0.12 + 0.045 * len(word)))
        dur = n / SR
        tt = np.arange(n) / SR
        f0 = 112 + 18 * math.sin(k * 0.7) + rng.uniform(-5, 5)
        phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.06 * np.sin(np.pi * tt / dur))) / SR
        tone = sum(np.sin(hm * phase) / hm for hm in range(1, 8))
        env = np.abs(np.sin(np.pi * max(1, round(len(word) / 3)) * tt / dur)) ** 0.5
        env *= np.clip(tt / 0.012, 0, 1) * np.clip((dur - tt) / 0.03, 0, 1)
        pieces.append(((0.25 * tone + 0.03 * rng.standard_normal(n)) * env).astype(np.float32))
        step = dur / len(word)
        for i, ch in enumerate(word):
            chars.append(ch)
            starts.append(round(pos / SR + i * step, 4))
            ends.append(round(pos / SR + (i + 1) * step, 4))
        pos += n
        gap = 0.3 if word.endswith((".", "?", "!")) else 0.15 if word.endswith((",", ";", ":")) else 0.06
        g = int(SR * gap)
        if k + 1 < len(words):
            chars.append(" ")
            starts.append(round(pos / SR, 4))
            ends.append(round((pos + g) / SR, 4))
        pieces.append(np.zeros(g, np.float32))
        pos += g
    pieces.append(np.zeros(int(SR * 0.2), np.float32))
    return np.concatenate(pieces), chars, starts, ends


def fake_music(path, seconds=24.0):
    """A quiet four-chord pad, shorter than the video so the music bed has to loop and crossfade."""
    from .render import SR, _write_wav
    t = np.arange(int(SR * seconds)) / SR
    chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (130.81, 164.81, 196.0), (196.0, 246.94, 293.66)]
    out = np.zeros((len(t), 2), np.float32)
    seg = seconds / len(chords)
    for k, chord in enumerate(chords):
        a, b = int(k * seg * SR), int((k + 1) * seg * SR)
        tt = t[a:b] - t[a]
        env = np.minimum(1, tt / 0.8) * np.minimum(1, (seg - tt) / 0.8)
        for f in chord:
            for side, det in ((0, 0.997), (1, 1.003)):
                out[a:b, side] += (np.sin(2 * np.pi * f * det * tt) * 0.6 + np.sin(4 * np.pi * f * det * tt) * 0.15) * env
    out *= 0.35 / np.abs(out).max()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(path, out.reshape(-1), channels=2)


def make_inputs(video):
    """Script, voice-over with word timings, shot list, package, images (one left out), one clip and music."""
    from .textutil import Script
    from .voice import stitch, write_wav
    write_text(video.dir / "video.yaml", "topic: Self-test video\nangle: ''\nnotes: ''\nwords: 90\n")
    write_text(video.script, SCRIPT)
    s = Script(SCRIPT)
    cache = video.p("audio", "cache")
    cache.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, para in enumerate(s.paras):
        samples, chars, starts, ends = fake_speech(para["text"], seed=i)
        audio, align = cache / f"selftest_{i}.wav", cache / f"selftest_{i}.json"
        write_wav(audio, samples)
        write_json(align, {"chars": chars, "start": starts, "end": ends, "text": para["text"]})
        jobs.append({"i": i, "text": para["text"], "audio": audio, "align": align})
    stitch(video, s, jobs, "selftest")

    sc = cfg()["shotlist"]
    beats = s.beats(sc["beat_min_words"], sc["beat_max_words"])
    scenes = []
    for part in s.parts:
        n = part["n"]
        pb = [b for b in beats if b["part"] == n]
        pattern = PATTERN.get(n, PATTERN[1])[:len(pb)]
        for k, (kind, extra) in enumerate(pattern):
            group = pb[k:k + 1] if k < len(pattern) - 1 else pb[k:]
            text = " ".join(b["text"] for b in group)
            item = {"id": f"S{n}-{k + 1:02d}", "part": n, "beats": [b["id"] for b in group], "type": kind,
                    "visual": f"Test picture for: {text}", "camera": extra.get("camera", "static"),
                    "onscreen": extra.get("onscreen", "")}
            if kind in ("still", "motion"):
                item["mj_prompt"] = "a dark empty office at night, a single desk lamp"
            if kind == "motion":
                item["kling_prompt"] = "slow push in, dust drifting in the lamp light"
            if kind == "card":
                item["card"] = dict(extra)
            if kind == "reuse":
                item["reuse_of"] = f"S{n}-01"
            scenes.append(item)
    # Written the way a hand-made shot list and package arrive (FORMAT.md: no script_hash, no full Midjourney
    # prompts), so the self-test also checks that the pipeline takes them over.
    shot = {"version": 1, "beats": beats, "scenes": scenes}
    write_json(video.shotlist, shot)

    last_of_part2 = max((b["id"] for b in beats if b["part"] == 2), default=beats[-1]["id"])
    write_json(video.package, {
        "titles": ["The Vanishing Fund (self-test)"],
        "thumbnail_texts": ["$400M gone", "Who was the auditor?"],
        "thumbnail_prompt": "a dark empty office at night",
        "description": "Self-test video made by vp selftest.",
        "tags": ["selftest"],
        "hashtags": ["#selftest"],
        "shorts": [{"title": "Self-test short", "hook": "$400 million, gone", "beat_from": beats[0]["id"],
                    "beat_to": last_of_part2, "why": "self-test"}],
        "pinned_comment": "Sources: none, this is a self-test.",
    })

    images = video.assets("images")
    for k, scene in enumerate(scenes):
        if scene["type"] in ("still", "motion") and scene["id"] != LEFT_OUT:
            fake_image(scene, images / f"{scene['id']}.png", k)
    for scene in scenes:
        if scene["type"] == "motion":
            fake_clip(images / f"{scene['id']}.png", video.assets("clips") / f"{scene['id']}.mp4")
    fake_music(video.dir / "music" / "selftest_pad.wav")
    return shot


# ---------------------------------------------------------------- checks


def probe(path):
    res = run([ffmpeg_bin("ffprobe"), "-v", "error", "-show_entries",
               "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,channels,sample_rate:format=duration",
               "-of", "json", str(path)])
    return json.loads(res.stdout.decode("utf-8", "replace"))


def check_video(r, label, path, w, h, length=None, tol=0.25):
    path = Path(path)
    if not r.check(f"{label}: file made", path.exists() and path.stat().st_size > 0, path.name):
        return None
    info = probe(path)
    vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    au = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    fps = cfg()["video"]["fps"]
    r.check(f"{label}: picture", vs.get("codec_name") == "h264" and vs.get("width") == w and vs.get("height") == h
            and vs.get("pix_fmt") == "yuv420p" and vs.get("r_frame_rate") == f"{fps}/1",
            f"{vs.get('codec_name')} {vs.get('width')}x{vs.get('height')} {vs.get('pix_fmt')} {vs.get('r_frame_rate')}")
    r.check(f"{label}: sound", au.get("codec_name") == "aac" and int(au.get("channels") or 0) == 2,
            f"{au.get('codec_name')}, {au.get('channels')} channels")
    got = float(info.get("format", {}).get("duration") or 0)
    if length is not None:
        r.check(f"{label}: length", abs(got - length) <= tol, f"{got:.2f} s (timeline {length:.2f} s)")
    return got


def check_loudness(r, path):
    from .render import measure_loudness
    v = cfg()["video"]
    i, tp = measure_loudness(path)
    ok = i is not None and math.isfinite(i) and abs(i - v["loudness"]) <= 1.0 and tp <= v["true_peak"] + 1.0
    r.check("rough cut: loudness", ok, f"{i:.1f} LUFS, peaks {tp:.1f} dBTP (target {v['loudness']} LUFS, "
            f"ceiling {v['true_peak']} dBTP)" if i is not None else "could not measure")


def check_srt(r, path, length):
    if not r.check("captions.srt: file made", path.exists(), path.name):
        return
    stamps = re.findall(r"(\d+):(\d\d):(\d\d),(\d{3}) --> (\d+):(\d\d):(\d\d),(\d{3})", path.read_text(encoding="utf-8"))
    cues = [(int(a) * 3600 + int(b) * 60 + int(c) + int(d) / 1000, int(e) * 3600 + int(f) * 60 + int(g) + int(h) / 1000)
            for a, b, c, d, e, f, g, h in stamps]
    ordered = all(a < b for a, b in cues) and all(cues[k][1] <= cues[k + 1][0] + 0.001 for k in range(len(cues) - 1))
    r.check("captions.srt: timing", len(cues) >= 5 and ordered and cues[-1][1] <= length + 0.5,
            f"{len(cues)} cues, in order, last ends at {cues[-1][1]:.1f} s" if cues else "no cues")


def _uri_path(uri):
    return Path(url2pathname(urlparse(uri).path))


def check_fcpxml(r, path, n_scenes, n_audio):
    if not r.check("timeline.fcpxml: file made", path.exists(), path.name):
        return
    root = ET.parse(path).getroot()
    srcs = [a.get("src") for a in root.iter("asset")]
    missing = [s for s in srcs if not s or not _uri_path(s).exists()]
    spine = root.find(".//spine")
    clips = list(spine) if spine is not None else []
    lanes = [c.get("lane") for c in root.iter("asset-clip") if c.get("lane")]
    r.check("timeline.fcpxml: DaVinci timeline", root.get("version") == "1.8" and len(clips) == n_scenes
            and len(lanes) == n_audio and not missing,
            f"version {root.get('version')}, {len(clips)} scene clips, {len(lanes)} audio tracks, "
            f"{len(srcs) - len(missing)}/{len(srcs)} media files found")


def check_png(r, label, path, size):
    if not path.exists():
        return r.check(label, False, f"{path.name} missing")
    with Image.open(path) as im:
        return r.check(label, im.size == size, f"{path.name} {im.size[0]}x{im.size[1]}")


# ---------------------------------------------------------------- main


def _step(r, name, fn, timings):
    say(f"\n{name} ...")
    t0 = time.time()
    try:
        out = fn()
    except (Exception, SystemExit) as e:
        msg = str(e).strip().splitlines()
        r.check(name, False, f"{type(e).__name__}: {' | '.join(msg[-6:])[:900]}")
        raise _Stop()
    timings.append((name, time.time() - t0))
    return out


class _Stop(Exception):
    pass


def selftest(keep=False, workers=None):
    from .content import adopt_handmade, script_hash
    from .cut import build_cut
    from .shorts import build_shorts
    from .thumbnail import build_thumbnails
    started = time.time()
    r = Report()
    timings = []
    say("Self-test: a 40-second test video made offline (no Claude, no ElevenLabs, no Midjourney),\n"
        "rendered through every step and checked. Takes 1-3 minutes.\n\nTools")
    video = None
    if check_tools(r):
        d = VIDEOS / SLUG
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        video = Video(SLUG)
        v = cfg()["video"]
        try:
            check_captions(r, video.dir / "edit" / "captions_test")
            shot = _step(r, "Making test inputs (script, voice, scenes, images, clip, music)",
                         lambda: make_inputs(video), timings)
            n = len(shot["scenes"])
            kinds = sorted({s["type"] for s in shot["scenes"]})
            r.check("test inputs", (video.words.exists() and len(read_json(video.words)["words"]) == shot["beats"][-1]["w1"]),
                    f"{n} scenes ({', '.join(kinds)}), {len(read_json(video.words)['words'])} timed words")

            _step(r, "Taking over the hand-made shot list and package", lambda: adopt_handmade(video), timings)
            shot, pkg = read_json(video.shotlist), read_json(video.package)
            r.check("hand-made shot list and package taken over",
                    shot.get("made_by") == pkg.get("made_by") == "hand" and shot.get("script_hash") == script_hash(video)
                    and all(s.get("mj_full") for s in shot["scenes"] if s["type"] in ("still", "motion"))
                    and bool(pkg.get("thumbnail_full")),
                    f"{len(shot['scenes'])} scenes, {len(pkg['shorts'])} Short, prompts in {video.prompts_md.name}")

            _step(r, "Draft cut (one image left out on purpose)",
                  lambda: build_cut(video, draft=True, workers=workers), timings)
            timing = read_json(video.timing)
            check_video(r, "draft.mp4", video.edit("draft.mp4"), 960, 540, timing["duration"])
            report = video.edit("report.md").read_text(encoding="utf-8")
            r.check("draft: placeholder for the missing image", "Missing images (1)" in report and LEFT_OUT in report,
                    f"{LEFT_OUT} listed in edit/report.md")

            fake_image(next(s for s in shot["scenes"] if s["id"] == LEFT_OUT),
                       video.assets("images") / f"{LEFT_OUT}.png", 3)
            _step(r, "Full cut (1080p, music, sound effects, loudness, captions, timeline)",
                  lambda: build_cut(video, workers=workers), timings)
            timing = read_json(video.timing)
            rough = video.edit("roughcut.mp4")
            check_video(r, "roughcut.mp4", rough, v["width"], v["height"], timing["duration"])
            check_loudness(r, rough)
            check_srt(r, video.edit("captions.srt"), timing["duration"])
            n_audio = 2 + (1 if v.get("sfx", True) else 0)
            check_fcpxml(r, video.edit("timeline.fcpxml"), n, n_audio)
            report = video.edit("report.md").read_text(encoding="utf-8")
            r.check("rough cut: every scene has its picture", "Missing images" not in report, "edit/report.md")
            pkg_md = video.package_md.read_text(encoding="utf-8") if video.package_md.exists() else ""
            r.check("05_package.md: chapters in the description", "0:00 " in pkg_md, video.package_md.name)

            made = _step(r, "Short (vertical, word-by-word captions)",
                         lambda: build_shorts(video, only=[1], workers=workers), timings)
            sc = cfg()["shorts"]
            if r.check("short: made", len(made) == 1, ", ".join(p.name for p in made)):
                got = check_video(r, "short_01.mp4", made[0], sc["width"], sc["height"])
                r.check("short: under the Shorts limit", got is not None and 5 < got <= sc["max_seconds"] + 0.5,
                        f"{got:.1f} s" if got else "")

            thumbs = _step(r, "Thumbnails", lambda: build_thumbnails(video), timings)
            for p in thumbs:
                check_png(r, f"thumbnail {p.name}", p, (1280, 720))
        except _Stop:
            pass
        except Exception as e:
            r.check("self-test", False, f"{type(e).__name__}: {e}")

    total = time.time() - started
    say("\nTimes: " + ", ".join(f"{name.split(' (')[0].lower()} {sec:.0f} s" for name, sec in timings)
        + f"; total {total:.0f} s")
    failed, warned = r.failed(), r.warned()
    if failed:
        say(f"\nSELF-TEST FAILED: {len(failed)} of {len(r.rows)} checks failed:")
        for name, _, detail in failed:
            say(f"  - {name}: {detail}")
        if video:
            say(f"The test files are kept in {video.dir} so you can look at them.")
        return 1
    if warned:
        say(f"\nSelf-test passed with {len(warned)} warning(s): " + "; ".join(f"{n}: {d}" for n, _, d in warned))
    else:
        say(f"\nSELF-TEST PASSED: all {len(r.rows)} checks ok. The pipeline is ready on this computer.")
    if video and keep:
        say(f"Test video kept: {video.edit('roughcut.mp4')}")
    elif video:
        shutil.rmtree(video.dir, ignore_errors=True)
    return 0
