import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from .cards import chapter_overlay, lower_third, placeholder
from .config import LIBRARY, cfg
from .content import render_package_md
from .render import (concat, job_key, mix_audio, music_bed, mux, probe_duration, render_all, sfx_track)
from .timing import compute, write_srt
from .util import file_sig, read_json, say, sha, warn, write_text

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
CLIP_EXT = (".mp4", ".mov", ".webm", ".mkv")


def find_assets(video):
    found = {}
    for kind, exts in (("image", IMAGE_EXT), ("clip", CLIP_EXT)):
        folder = video.assets("images" if kind == "image" else "clips")
        for f in folder.iterdir():
            if f.suffix.lower() in exts and not f.name.startswith("_"):
                found.setdefault(f.stem.upper(), {})[kind] = f
    return found


def music_tracks(video):
    for folder in (video.dir / "music", LIBRARY / "music"):
        if folder.exists():
            tracks = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac", ".ogg"))
            if tracks:
                return tracks
    return []


def scene_plan(video, tscenes, fps, w, h, draft=False, vertical=False):
    """Decide what each scene shows and build its render job."""
    v = cfg()["video"]
    shot = read_json(video.shotlist)
    by_id = {s["id"]: s for s in shot["scenes"]}
    assets = find_assets(video)
    titles = {p["n"]: p["title"] for p in shot["parts"]}
    seg_dir = video.edit("segments_v" if vertical else ("segments_draft" if draft else "segments"))
    ov_dir = video.edit("overlays")
    seg_dir.mkdir(parents=True, exist_ok=True)
    ov_dir.mkdir(parents=True, exist_ok=True)
    plan = []
    for i, st in enumerate(tscenes):
        sc = by_id[st["id"]]
        prev_part = tscenes[i - 1]["part"] if i > 0 else None
        next_part = tscenes[i + 1]["part"] if i + 1 < len(tscenes) else None
        job = {"w": w, "h": h, "fps": fps, "frames": st["frames"], "grain": v.get("grain", True),
               "grain_strength": v.get("grain_strength", 6), "max_mbps": v.get("max_mbps", 8),
               "preset": v["preset"], "crf": v["crf"]}
        if draft:
            job.update(preset="ultrafast", crf=26, grain=False)
        note = ""
        a = assets.get(sc["id"].upper(), {})
        if sc["type"] == "reuse" and sc.get("reuse_of"):
            a = assets.get(sc["reuse_of"].upper(), {})
        if sc["type"] == "card":
            job.update(kind="card", card=sc["card"])
        elif sc["type"] == "motion" and a.get("clip"):
            job.update(kind="clip", src=str(a["clip"]), clip_dur=probe_duration(a["clip"]), vignette=True)
            if job["clip_dur"] * 1.35 < st["frames"] / fps:
                note = "clip shorter than the scene; last frame is held"
        elif a.get("image"):
            job.update(kind="still", src=str(a["image"]), camera=sc.get("camera") or "push_in", vignette=True)
            if sc["type"] == "motion":
                note = "no Kling clip yet; using the still image"
        else:
            ph = ov_dir / f"ph_{sc['id']}_{w}x{h}_{sha(sc.get('visual'), sc.get('mj_prompt'), n=6)}.png"
            if not ph.exists():
                placeholder(dict(sc, _need="image"), w, h).save(ph)
            job.update(kind="image", src=str(ph))
            note = "missing image"
        if not vertical:
            first = sc["part"] != prev_part and prev_part is not None
            job["fade_in"] = first
            job["fade_out"] = next_part is not None and next_part != sc["part"]
            layers = []
            if first:
                layers.append(("chapter", sc["part"], titles.get(sc["part"], "")))
            if sc.get("onscreen"):
                layers.append(("lower", sc["onscreen"]))
            if layers:
                ov = ov_dir / f"ov_{sha(layers, w, h, n=10)}.png"
                if not ov.exists():
                    img = None
                    for layer in layers:
                        part_img = (chapter_overlay(layer[1], layer[2], w, h) if layer[0] == "chapter"
                                    else lower_third(layer[1], w, h))
                        img = part_img if img is None else _over(img, part_img)
                    img.save(ov)
                dur = st["frames"] / fps
                job.update(overlay=str(ov), ov_in=0.45 if first else 0.5, ov_out=min(dur - 0.3, 3.6 if first else dur - 0.4))
        src_sig = file_sig(job["src"]) if job.get("src") else ""
        job["out"] = str(seg_dir / f"{sc['id']}_{job_key(job, src_sig)}.mp4")
        plan.append({"scene": sc, "timing": st, "job": job, "note": note})
    return plan


def _over(a, b):
    a = a.copy()
    a.alpha_composite(b)
    return a


def cleanup_segments(seg_dir, keep):
    keep = {Path(k).name for k in keep}
    for f in Path(seg_dir).glob("*.mp4"):
        if f.name not in keep:
            f.unlink(missing_ok=True)


def sfx_events(plan, offset=0.0):
    events = []
    for i, p in enumerate(plan):
        t = p["timing"]["start"] - offset
        if p["job"].get("fade_in"):
            events.append((t - 0.45, "whoosh", -15))
        card = p["scene"].get("card") if p["scene"]["type"] == "card" else None
        if card:
            if card["kind"] in ("stat", "compare", "date"):
                events.append((t + 0.05, "hit", -12))
            elif card["kind"] in ("quote", "document", "list"):
                events.append((t + 0.15, "tick", -20))
    return events


def build_cut(video, draft=False, workers=None, music=True):
    v = cfg()["video"]
    timing = compute(video)
    w, h = (960, 540) if draft else (v["width"], v["height"])
    plan = scene_plan(video, timing["scenes"], timing["fps"], w, h, draft=draft)
    jobs = [p["job"] for p in plan]
    seg_dir = Path(jobs[0]["out"]).parent
    cleanup_segments(seg_dir, [j["out"] for j in jobs])
    rendered = render_all(jobs, workers)
    name = "draft" if draft else "roughcut"
    video_only = concat([j["out"] for j in jobs], video.edit(f"{name}_video.mp4"))
    tracks = music_tracks(video) if music else []
    bed = music_bed(tracks, timing["duration"], video.edit("music_bed.flac")) if tracks else None
    if not tracks:
        video.edit("music_bed.flac").unlink(missing_ok=True)
    sfx = sfx_track(sfx_events(plan), timing["duration"], video.edit("sfx.flac")) if v.get("sfx", True) else None
    mix = mix_audio(video.voice_wav, timing["duration"], video.edit(f"{name}_mix.m4a"), music=bed, sfx=sfx)
    out = mux(video_only, mix, video.edit(f"{name}.mp4"))
    for tmp in (video_only, Path(video_only).with_suffix(".txt"), mix):
        Path(tmp).unlink(missing_ok=True)
    words_data = read_json(video.words)
    n_caps = write_srt(video, words_data["words"], video.edit("captions.srt"))
    if not draft:
        write_fcpxml(video, plan, timing, bed, sfx, words_data["duration"])
    render_package_md(video)
    write_report(video, plan, timing, rendered, out, tracks, n_caps)
    say(f"cut: {timing['duration'] / 60:.1f} min -> {out.relative_to(video.dir)}"
        + ("" if draft else ", timeline.fcpxml, captions.srt"))
    return out


def write_report(video, plan, timing, rendered, out, tracks, n_caps):
    missing = [p for p in plan if p["note"] == "missing image"]
    notes = [p for p in plan if p["note"] and p["note"] != "missing image"]
    kinds = {}
    for p in plan:
        kinds[p["job"]["kind"]] = kinds.get(p["job"]["kind"], 0) + 1
    lines = [f"# Cut report: {video.slug}", "",
             f"- Output: {out.name}, {timing['duration'] / 60:.1f} min, {len(plan)} scenes ({rendered} re-rendered)",
             f"- Shown as: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())),
             f"- Music: {len(tracks)} track(s)" + ("" if tracks else " - add files to library/music for a music bed"),
             f"- Captions: {n_caps} cues in captions.srt (upload to YouTube as subtitles)", ""]
    if missing:
        lines += [f"## Missing images ({len(missing)})", "", "Open the scene board (vp board) to add them.", ""]
        lines += [f"- {p['scene']['id']} at {p['timing']['start']:.1f}s: {p['scene'].get('visual', '')}" for p in missing]
        lines.append("")
    if notes:
        lines += ["## Notes", ""] + [f"- {p['scene']['id']}: {p['note']}" for p in notes] + [""]
    write_text(video.edit("report.md"), "\n".join(lines))
    if missing:
        warn(f"{len(missing)} scenes still need images (placeholders used) - see edit/report.md")


def _url(path):
    """file:// URL of a local file (file:///C:/Users/... on Windows), spaces and other characters escaped."""
    return Path(path).resolve().as_uri()


def _attr(text):
    return escape(str(text), {'"': "&quot;"})


def write_fcpxml(video, plan, timing, bed=None, sfx=None, voice_duration=None):
    """A Final Cut Pro XML 1.8 timeline (DaVinci Resolve: File > Import > Timeline). In version 1.8 an asset
    names its file with a src attribute; 1.9 and later moved it into a media-rep element."""
    fps = timing["fps"]
    v = cfg()["video"]
    w, h = v["width"], v["height"]
    total = sum(int(p["timing"]["frames"]) for p in plan)
    voice_frames = min(total, round((voice_duration or timing.get("voice_duration") or timing["duration"]) * fps))

    def rt(frames):
        return f"{int(frames)}/{fps}s" if frames else "0s"

    res = [f'<format id="r1" name="FFVideoFormat{h}p{fps}" frameDuration="1/{fps}s" width="{w}" height="{h}"/>']
    clips = []
    for i, p in enumerate(plan, 1):
        n = p["timing"]["frames"]
        res.append(f'<asset id="a{i}" name="{_attr(p["scene"]["id"])}" src="{_attr(_url(p["job"]["out"]))}" '
                   f'start="0s" duration="{rt(n)}" hasVideo="1" format="r1"/>')
        clips.append((i, p, n))
    audio = [("av", "voice", video.voice_wav, voice_frames, 1)]
    if bed:
        audio.append(("am", "music", bed, total, 2))
    if sfx:
        audio.append(("ax", "effects", sfx, total, 1))
    for aid, name, path, frames, channels in audio:
        res.append(f'<asset id="{aid}" name="{name}" src="{_attr(_url(path))}" start="0s" duration="{rt(frames)}" '
                   f'hasAudio="1" audioSources="1" audioChannels="{channels}" audioRate="44100"/>')
    spine = []
    offset = 0
    for i, p, n in clips:
        inner = ""
        if i == 1:
            inner = "".join(f'<asset-clip ref="{aid}" lane="-{k}" offset="0s" name="{name}" duration="{rt(frames)}" '
                            f'start="0s"/>' for k, (aid, name, _, frames, _) in enumerate(audio, 1))
        spine.append(f'<asset-clip ref="a{i}" offset="{rt(offset)}" name="{_attr(p["scene"]["id"])}" '
                     f'duration="{rt(n)}" start="0s" format="r1">{inner}</asset-clip>')
        offset += n
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.8">\n<resources>\n'
           + "\n".join(res) + "\n</resources>\n<library>\n"
           f'<event name="{_attr(video.slug)}">\n<project name="{_attr(video.slug)} rough cut">\n'
           f'<sequence format="r1" duration="{rt(offset)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" '
           'audioRate="48k">\n'
           "<spine>\n" + "\n".join(spine) + "\n</spine>\n</sequence>\n</project>\n</event>\n</library>\n</fcpxml>\n")
    write_text(video.edit("timeline.fcpxml"), xml)


def export_assets_for_editor(video):
    """Copy the rendered segments in order with readable names, for editors that import a folder."""
    timing = read_json(video.timing)
    out = video.edit("for_davinci")
    out.mkdir(exist_ok=True)
    for f in out.glob("*.mp4"):
        f.unlink()
    plan_dir = video.edit("segments")
    for k, st in enumerate(timing["scenes"], 1):
        matches = sorted(plan_dir.glob(f"{st['id']}_*.mp4"))
        if matches:
            shutil.copy2(matches[-1], out / f"{k:03d}_{st['id']}.mp4")
    return out
