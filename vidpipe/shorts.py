import os
from pathlib import Path

from .config import ROOT, cfg, ffmpeg_bin
from .cut import scene_plan, sfx_events
from .music import build_bed
from .render import concat, mix_audio, render_all, sfx_track
from .timing import compute, shorts_ass
from .util import read_json, run, say, warn


def short_range(sh, timing, words, max_s):
    bt = {b["id"]: b for b in timing["beats"]}
    t0 = max(0.0, bt[sh["beat_from"]]["start"] - 0.12)
    t1 = bt[sh["beat_to"]]["end"] + 0.45
    if t1 - t0 > max_s:
        ends = [w["end"] for w in words if t0 < w["end"] <= t0 + max_s - 0.4 and w["text"].endswith((".", "?", "!"))]
        if not ends:
            ends = [w["end"] for w in words if t0 < w["end"] <= t0 + max_s - 0.4]
        t1 = (max(ends) if ends else t0 + max_s - 0.4) + 0.4
    return round(t0, 3), round(t1, 3)


def clipped_scenes(timing, t0, t1):
    fps = timing["fps"]
    out = []
    f0, f1 = round(t0 * fps), round(t1 * fps)
    for s in timing["scenes"]:
        a, b = max(s["f0"], f0), min(s["f0"] + s["frames"], f1)
        if b - a >= 1:
            out.append(dict(s, f0=a, frames=b - a, start=round(a / fps, 3), end=round(b / fps, 3)))
    if out and out[0]["frames"] < fps * 0.5 and len(out) > 1:
        out[1] = dict(out[1], f0=out[0]["f0"], frames=out[1]["frames"] + out[0]["frames"], start=out[0]["start"])
        out = out[1:]
    return out


def build_shorts(video, only=None, workers=None):
    pkg = read_json(video.package)
    if not pkg or not pkg.get("shorts"):
        raise SystemExit("No Shorts moments yet. Run: vp package " + video.slug)
    timing = compute(video)
    words = read_json(video.words)["words"]
    sc = cfg()["shorts"]
    w, h = sc["width"], sc["height"]
    out_dir = video.edit("shorts")
    out_dir.mkdir(exist_ok=True)
    full_bed, _ = build_bed(video, timing, video.edit("music_bed.flac"))
    made = []
    for k, sh in enumerate(pkg["shorts"], 1):
        if only and k not in only:
            continue
        t0, t1 = short_range(sh, timing, words, sc["max_seconds"])
        scenes = clipped_scenes(timing, t0, t1)
        plan = scene_plan(video, scenes, timing["fps"], w, h, vertical=True)
        render_all([p["job"] for p in plan], workers)
        base = out_dir / f"short_{k:02d}"
        vid = concat([p["job"]["out"] for p in plan], base.with_name(base.name + "_video.mp4"))
        dur = sum(p["job"]["frames"] for p in plan) / timing["fps"]
        voice = base.with_name(base.name + "_voice.wav")
        run([ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(video.voice_wav),
             "-af", "afade=t=in:d=0.05,afade=t=out:st=" + f"{max(0, dur - 0.3):.3f}" + ":d=0.3", str(voice)])
        bed = None
        if full_bed:
            bed = base.with_name(base.name + "_music.flac")
            run([ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(full_bed),
                 "-af", f"afade=t=in:d=0.6,afade=t=out:st={max(0, dur - 1.2):.3f}:d=1.2", str(bed)])
        sfx = sfx_track(sfx_events(plan, offset=t0), dur, base.with_name(base.name + "_sfx.wav"))
        mix = mix_audio(voice, dur, base.with_name(base.name + "_mix.m4a"), music=bed, sfx=sfx)
        ass = base.with_name(base.name + ".ass")
        shorts_ass(words, t0, t0 + dur, sh.get("hook", ""), w, h, ass)
        fonts_rel = os.path.relpath(ROOT / "library" / "fonts", out_dir).replace("\\", "/")
        final = base.with_suffix(".mp4")
        run([ffmpeg_bin(), "-y", "-v", "error", "-i", vid.name, "-i", Path(mix).name,
             "-vf", f"ass={ass.name}:fontsdir={fonts_rel}", "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "copy", "-shortest", "-movflags", "+faststart", final.name], cwd=out_dir)
        for tmp in (vid, Path(vid).with_suffix(".txt"), voice, sfx, Path(mix)) + ((bed,) if bed else ()):
            Path(tmp).unlink(missing_ok=True)
        made.append(final)
        say(f"short {k}: {dur:.0f}s \"{sh['title']}\" -> {final.relative_to(video.dir)}")
    if not made:
        warn("no shorts were made")
    return made
