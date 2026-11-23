import json
import math
import multiprocessing
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

from .config import ROOT, cfg, ffmpeg_bin
from .util import run, say, sha, write_text

RENDER_VERSION = 5
MOVES = {
    "push_in": ((1.0, 0.5, 0.5), (1.12, 0.5, 0.46)),
    "pull_out": ((1.12, 0.5, 0.46), (1.0, 0.5, 0.5)),
    "pan_left": ((1.1, 0.75, 0.5), (1.1, 0.25, 0.5)),
    "pan_right": ((1.1, 0.25, 0.5), (1.1, 0.75, 0.5)),
    "pan_up": ((1.1, 0.5, 0.75), (1.1, 0.5, 0.25)),
    "pan_down": ((1.1, 0.5, 0.25), (1.1, 0.5, 0.75)),
    "static": ((1.02, 0.5, 0.5), (1.05, 0.5, 0.5)),
}


def probe_duration(path):
    res = run([ffmpeg_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(res.stdout.decode().strip())
    except ValueError:
        return 0.0


def cover(img, w, h, scale=1.0):
    """Center-crop img to the w:h aspect and resize so it is scale times the output size."""
    ar = w / h
    iw, ih = img.size
    if iw / ih > ar:
        nw = ih * ar
        img = img.crop((int((iw - nw) / 2), 0, int((iw + nw) / 2), ih))
    else:
        nh = iw / ar
        img = img.crop((0, int((ih - nh) / 2), iw, int((ih + nh) / 2)))
    tw, th = int(round(w * scale)), int(round(h * scale))
    return img.resize((tw, th), Image.LANCZOS)


def still_frames(src, w, h, frames, camera):
    img = Image.open(src).convert("RGB")
    work = cover(img, w, h, 1.12)
    ww, wh = work.size
    (z0, cx0, cy0), (z1, cx1, cy1) = MOVES.get(camera, MOVES["push_in"])
    for i in range(frames):
        p = i / max(1, frames - 1)
        p = 0.75 * p + 0.25 * (0.5 - 0.5 * math.cos(math.pi * p))
        z = z0 + (z1 - z0) * p
        rw, rh = ww / z, wh / z
        cx = (cx0 + (cx1 - cx0) * p) * ww
        cy = (cy0 + (cy1 - cy0) * p) * wh
        x0 = min(max(cx - rw / 2, 0), ww - rw)
        y0 = min(max(cy - rh / 2, 0), wh - rh)
        yield work.resize((w, h), Image.BILINEAR, box=(x0, y0, x0 + rw, y0 + rh)).tobytes()


def _post(job, dur):
    chain = []
    if job.get("grain"):
        # Luma-only moving grain, like film. Chroma noise looks digital and costs a lot of bitrate.
        chain.append(f"noise=c0s={int(job.get('grain_strength', 6))}:c0f=t")
    if job.get("vignette"):
        chain.append("vignette=angle=0.5")
    if job.get("fade_in"):
        chain.append("fade=t=in:st=0:d=0.35")
    if job.get("fade_out"):
        chain.append(f"fade=t=out:st={max(0.0, dur - 0.35):.3f}:d=0.35")
    return chain


def _encode_args(job):
    v = cfg()["video"]
    # Capped CRF: grain and fast motion can push x264 to 20+ Mbps; YouTube asks for about 8 Mbps at 1080p30.
    cap = float(job.get("max_mbps", v.get("max_mbps", 8)))
    return ["-c:v", "libx264", "-preset", job.get("preset", v["preset"]), "-crf", str(job.get("crf", v["crf"])),
            "-maxrate", f"{cap:g}M", "-bufsize", f"{cap * 1.5:g}M",
            "-pix_fmt", "yuv420p", "-r", str(job["fps"]), "-frames:v", str(job["frames"]),
            "-video_track_timescale", "15360", "-movflags", "+faststart", "-an"]


def render_segment(job):
    """Render one scene to an mp4 segment. Runs in a worker process."""
    w, h, fps, n = job["w"], job["h"], job["fps"], job["frames"]
    dur = n / fps
    out = Path(job["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    cmd = [ffmpeg_bin(), "-y", "-v", "error"]
    kind = job["kind"]
    if kind in ("still", "card"):
        cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
        base = "setsar=1"
    elif kind == "clip":
        clip_dur = job["clip_dur"] or dur
        speed = min(1.35, max(1.0, dur / clip_dur)) if clip_dur < dur else 1.0
        pad = max(0.0, dur - clip_dur * speed) + 0.1
        cmd += ["-i", job["src"]]
        base = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1,"
                f"setpts={speed:.4f}*PTS,fps={fps},tpad=stop_mode=clone:stop_duration={pad:.3f}")
    else:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 0.5:.3f}", "-i", job["src"]]
        base = f"scale={w}:{h},setsar=1"
    post = _post(job, dur)
    if job.get("overlay"):
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 0.5:.3f}", "-i", job["overlay"]]
        oi, oo = job.get("ov_in", 0.4), job.get("ov_out", dur - 0.4)
        graph = (f"[0:v]{base}[b];[1:v]format=rgba,fade=t=in:st={oi:.3f}:d=0.4:alpha=1,"
                 f"fade=t=out:st={max(oi + 0.5, oo - 0.4):.3f}:d=0.4:alpha=1[o];[b][o]overlay=0:0:shortest=1"
                 + ("," + ",".join(post) if post else "") + ",format=yuv420p[v]")
        cmd += ["-filter_complex", graph, "-map", "[v]"]
    else:
        cmd += ["-vf", ",".join([base] + post + ["format=yuv420p"])]
    cmd += _encode_args(job) + [str(tmp)]
    if kind in ("still", "card"):
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        if kind == "still":
            frames = still_frames(job["src"], w, h, n, job.get("camera", "push_in"))
        else:
            from .cards import card_frames
            frames = card_frames(job["card"], w, h, fps, n)
        try:
            for fr in frames:
                proc.stdin.write(fr)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RuntimeError(f"segment {out.name} failed: {err[-1500:]}")
    else:
        run(cmd)
    tmp.replace(out)
    return str(out)


def render_all(jobs, workers=None):
    """Render the jobs whose output file does not exist yet, in parallel."""
    todo = [j for j in jobs if not Path(j["out"]).exists()]
    if not todo:
        return 0
    workers = workers or cfg()["video"].get("workers", 3)
    say(f"  rendering {len(todo)} segments with {workers} workers ...")
    done = 0
    # "spawn" is what Windows always uses; using it everywhere keeps behaviour the same on every computer.
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as ex:
        futures = {ex.submit(render_segment, j): j for j in todo}
        for fut in as_completed(futures):
            fut.result()
            done += 1
            if done % 10 == 0 or done == len(todo):
                say(f"  {done}/{len(todo)} segments")
    return len(todo)


def job_key(job, extra=""):
    keep = {k: v for k, v in job.items() if k != "out"}
    return sha(RENDER_VERSION, keep, extra, n=10)


def concat(segments, out):
    out = Path(out)
    lst = out.with_suffix(".txt")
    lines = []
    for s in segments:
        p = str(Path(s).resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{p}'")
    write_text(lst, "\n".join(lines) + "\n")
    run([ffmpeg_bin(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


SR = 44100


def _write_wav(path, samples, channels=1):
    import wave
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def ensure_sfx():
    """Make simple whoosh / hit / tick sounds once if the library has none."""
    d = ROOT / "library" / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    if not (d / "whoosh.wav").exists():
        n = int(SR * 0.9)
        t = np.arange(n) / SR
        noise = rng.standard_normal(n)
        env = np.sin(np.pi * np.clip(t / 0.9, 0, 1)) ** 2.2
        out = np.zeros(n)
        y = 0.0
        for i in range(n):
            a = 0.02 + 0.25 * (t[i] / 0.9)
            y += a * (noise[i] - y)
            out[i] = y
        out = out * env
        _write_wav(d / "whoosh.wav", out / (np.abs(out).max() + 1e-9) * 0.8)
    if not (d / "hit.wav").exists():
        n = int(SR * 1.4)
        t = np.arange(n) / SR
        freq = 38 + 70 * np.exp(-t * 6)
        phase = 2 * np.pi * np.cumsum(freq) / SR
        boom = np.sin(phase) * np.exp(-t * 3.2)
        click = rng.standard_normal(n) * np.exp(-t * 90) * 0.4
        s = boom + click
        _write_wav(d / "hit.wav", s / np.abs(s).max() * 0.9)
    if not (d / "tick.wav").exists():
        n = int(SR * 0.08)
        t = np.arange(n) / SR
        s = rng.standard_normal(n) * np.exp(-t * 120)
        s = np.diff(np.concatenate([[0], s]))
        _write_wav(d / "tick.wav", s / np.abs(s).max() * 0.6)
    return d


def _read_wav(path):
    import wave
    with wave.open(str(path), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        return data


def sfx_track(events, duration, out):
    """events: list of (time, name, gain_db). Writes a mono track of the whole video length
    (FLAC when out ends in .flac: it is mostly silence, so it stays small)."""
    d = ensure_sfx()
    cache = {}
    track = np.zeros(int(SR * duration) + SR, dtype=np.float32)
    for t, name, db in events:
        p = d / f"{name}.wav"
        if not p.exists():
            continue
        if name not in cache:
            cache[name] = _read_wav(p)
        s = cache[name] * (10 ** (db / 20))
        i = int(max(0, t) * SR)
        j = min(len(track), i + len(s))
        track[i:j] += s[:j - i]
    out = Path(out)
    if out.suffix.lower() == ".flac":
        tmp = out.with_suffix(".tmp.wav")
        _write_wav(tmp, track[:int(SR * duration)])
        run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(tmp), "-c:a", "flac", str(out)])
        tmp.unlink(missing_ok=True)
    else:
        _write_wav(out, track[:int(SR * duration)])
    return out


def music_bed(tracks, duration, out):
    """Chain the music tracks with crossfades until they cover duration; loop if needed."""
    if not tracks:
        return None
    seq = []
    total = 0.0
    lengths = {t: probe_duration(t) for t in tracks}
    i = 0
    while total < duration + 2 and i < 200:
        t = tracks[i % len(tracks)]
        if lengths[t] <= 5:
            i += 1
            continue
        seq.append(t)
        total += lengths[t] - 3
        i += 1
    if not seq:
        return None
    cmd = [ffmpeg_bin(), "-y", "-v", "error"]
    for t in seq:
        cmd += ["-i", str(t)]
    parts = [f"[{k}:a]aformat=sample_rates={SR}:channel_layouts=stereo[a{k}]" for k in range(len(seq))]
    last = "a0"
    for k in range(1, len(seq)):
        parts.append(f"[{last}][a{k}]acrossfade=d=3:c1=tri:c2=tri[x{k}]")
        last = f"x{k}"
    parts.append(f"[{last}]atrim=0:{duration:.3f},afade=t=in:d=1.5,afade=t=out:st={max(0, duration - 4):.3f}:d=4[m]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[m]", "-c:a", "flac", str(out)]
    run(cmd)
    return out


def measure_loudness(path, chain=""):
    """Integrated loudness (LUFS) and true peak (dBTP) of an audio file, optionally after a filter chain."""
    af = (chain + "," if chain else "") + "loudnorm=print_format=json"
    res = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af", af, "-f", "null", "-"])
    text = res.stderr.decode("utf-8", "replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not m:
        return None, None
    st = json.loads(m.group(0))
    try:
        return float(st["input_i"]), float(st["input_tp"])
    except (KeyError, ValueError):
        return None, None


def mix_audio(voice, duration, out, music=None, sfx=None):
    """Voice + ducked music + effects, brought to the target loudness with a gain and a peak limiter."""
    v = cfg()["video"]
    target = v["loudness"]
    raw = Path(out).with_suffix(".mix.wav")
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(voice)]
    # Gentle narration compression evens out the voice, so the loudness target is reached without hard limiting.
    comp = ",acompressor=threshold=0.1:ratio=3:attack=5:release=150:knee=4" if v.get("voice_compress", True) else ""
    graph = [f"[0:a]aformat=sample_rates={SR}:channel_layouts=stereo{comp},apad=whole_dur={duration:.3f}[vo]"]
    inputs = ["[vo]"]
    if music:
        cmd += ["-i", str(music)]
        graph.append("[vo]asplit=2[vm][sc]")
        inputs = ["[vm]"]
        duck = ("threshold=0.02:ratio=7:attack=30:release=700" if v.get("duck", True) else "ratio=1")
        graph.append(f"[1:a]aformat=sample_rates={SR}:channel_layouts=stereo,volume={v['music_db']}dB[mu];"
                     f"[mu][sc]sidechaincompress={duck}[md]")
        inputs.append("[md]")
    if sfx:
        k = 2 if music else 1
        cmd += ["-i", str(sfx)]
        graph.append(f"[{k}:a]aformat=sample_rates={SR}:channel_layouts=stereo[fx]")
        inputs.append("[fx]")
    # 6 dB of headroom so voice + music + effects never clip in the intermediate file; the gain below restores it.
    graph.append(f"{''.join(inputs)}amix=inputs={len(inputs)}:duration=first:normalize=0,volume=-6dB,"
                 f"atrim=0:{duration:.3f}[out]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[out]", "-c:a", "pcm_s24le", str(raw)]
    run(cmd)
    # One constant gain to the target loudness (no pumping), then a look-ahead limiter keeps the peaks below
    # the true-peak ceiling. The ceiling leaves room for the small overshoot that AAC encoding adds.
    loud, _ = measure_loudness(raw)
    gain = target - loud if loud is not None and math.isfinite(loud) else 0.0
    ceiling = 10 ** (float(v.get("true_peak", -2.0)) / 20)

    def chain(g):
        g = max(-30.0, min(30.0, g))
        return f"volume={g:.2f}dB,alimiter=limit={ceiling:.4f}:attack=5:release=80:level=0:latency=1"

    for _ in range(3):
        # The limiter takes a little loudness off the peaks; measure the result and top the gain up.
        got, _ = measure_loudness(raw, chain(gain))
        if got is None or not math.isfinite(got) or abs(got - target) <= 0.4:
            break
        gain += target - got
    af = chain(gain)
    run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(raw), "-af", af, "-ar", str(SR), "-c:a", "aac", "-b:a", "192k",
         str(out)])
    raw.unlink(missing_ok=True)
    return out


def mux(video, audio, out):
    run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
         "-c", "copy", "-shortest", "-movflags", "+faststart", str(out)])
    return out
