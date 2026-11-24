"""Music chosen by mood: library/music/<mood>/ folders, a mood per part (or from a scene on), crossfades at changes.

Where the moods come from, first match wins:
  1. "music": "<mood>" on a scene in 04_shotlist.json: that mood from this scene on
  2. video.yaml  music: {1: tense, 2: calm, S3-14: heavy}  (a part number = the whole part, a scene id = from there on)
  3. the voice moods in the script ({tense}, {grave} ...), the most-used one in each part, translated by
     config.yaml music: from_voice
A mood with no songs borrows from a related mood (config.yaml music: fallback), then from loose songs in
library/music. With only loose songs (no mood folders) the whole video gets one chained bed, as before.
"none" (or silence/off) means no music for that stretch.
"""
import json
from pathlib import Path

from .config import LIBRARY, cfg, ffmpeg_bin
from .util import file_sig, fmt_time, read_json, run, sha, warn, write_json

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac")
SILENT = ("none", "silence", "off", "no music")
BED_VERSION = 1
_warned = set()


def _audio(folder):
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT
                   and not p.name.startswith(("_", "."))), key=lambda p: p.name.lower())


def pools(video=None):
    """{"": loose songs, "<mood>": songs in that folder}. A video's own music folder wins over the library's."""
    out = {}
    roots = [LIBRARY / "music"] + ([video.dir / "music"] if video is not None else [])
    for root in roots:
        if not root.is_dir():
            continue
        found = {"": _audio(root)}
        for sub in root.iterdir():
            if sub.is_dir() and not sub.name.startswith(("_", ".")):
                found[sub.name.strip().lower()] = _audio(sub)
        for key, tracks in found.items():
            if tracks:
                out[key] = tracks
            else:
                out.setdefault(key, [])
    return out


def all_tracks(video=None):
    seen = []
    for tracks in pools(video).values():
        seen += [t for t in tracks if t not in seen]
    return seen


def _norm(mood):
    return str(mood).strip().lower() if mood not in (None, "") else None


def _key(name):
    """A video.yaml music key: 3 / "3" / "part 3" -> 3; anything else is a scene id."""
    s = str(name).strip()
    low = s.lower().replace("part", "").strip()
    return int(low) if low.isdigit() else s.upper()


def voice_moods(video, shot):
    """Music mood per part from the script's voice moods (most words wins), or {} if the script has none."""
    from .textutil import Script
    if not video.script.exists():
        return {}
    mc = cfg()["music"]
    table = {str(k).lower(): _norm(v) for k, v in (mc.get("from_voice") or {}).items()}
    s = Script(video.script.read_text(encoding="utf-8"))
    votes = {}
    for para in s.paras:
        mood = table.get(para.get("mood") or "")
        if mood:
            part = votes.setdefault(para["part"], {})
            part[mood] = part.get(mood, 0) + (para["w1"] - para["w0"])
    return {n: max(v.items(), key=lambda kv: kv[1])[0] for n, v in votes.items()}


def scene_moods(video, scenes, shot=None):
    """The music mood of each scene (None = no mood given), and where each choice came from."""
    shot = shot if shot is not None else (read_json(video.shotlist) or {})
    by_id = {s["id"]: s for s in shot.get("scenes", [])}
    raw = video.meta.get("music") or {}
    if isinstance(raw, str):
        raw = {n: raw for n in {s["part"] for s in scenes}}
    mapping = {_key(k): _norm(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    auto = voice_moods(video, shot)
    out = []
    cur, src, part = None, "", None
    for sc in scenes:
        if sc["part"] != part:
            part = sc["part"]
            if part in mapping:
                cur, src = mapping[part], "video.yaml"
            elif part in auto:
                cur, src = auto[part], "voice moods"
        sid = str(sc["id"]).upper()
        if sid in mapping:
            cur, src = mapping[sid], "video.yaml"
        own = _norm(by_id.get(sc["id"], {}).get("music"))
        if own:
            cur, src = own, "shot list"
        out.append((cur, src))
    return out


def resolve(mood, pl):
    """Which song folder plays for a mood: the mood's own, a related one, loose songs, then anything."""
    if mood in SILENT:
        return None
    if mood and pl.get(mood):
        return mood
    fb = cfg()["music"].get("fallback") or {}
    for alt in ([fb.get(mood)] if isinstance(fb.get(mood), str) else list(fb.get(mood) or [])) if mood else []:
        alt = _norm(alt)
        if alt and pl.get(alt):
            _note(mood, f"no songs in library/music/{mood} yet, using {alt}")
            return alt
    if pl.get(""):
        if mood:
            _note(mood, f"no songs in library/music/{mood} yet, using the loose songs in library/music")
        return ""
    for k, tracks in pl.items():
        if tracks:
            if mood:
                _note(mood, f"no songs in library/music/{mood} yet, using {k}")
            return k
    return None


def _note(mood, msg):
    if mood not in _warned:
        _warned.add(mood)
        warn(msg)


def plan(video, timing, shot=None):
    """Music sections: [{"mood", "folder", "source", "start", "end"}], consecutive scenes on the same folder merged,
    sections shorter than music.min_section folded into the one before."""
    pl = pools(video)
    scenes = timing["scenes"]
    moods = scene_moods(video, scenes, shot)
    total = timing["duration"]
    secs = []
    for sc, (mood, src) in zip(scenes, moods):
        folder = resolve(mood, pl)
        label = mood or ("music" if folder == "" else folder)
        if secs and secs[-1]["folder"] == folder:
            secs[-1]["end"] = sc["end"]
            continue
        secs.append({"mood": label if folder is not None else (mood or "none"), "folder": folder, "source": src,
                     "start": sc["start"], "end": sc["end"]})
    if secs:
        secs[0]["start"] = 0.0
        secs[-1]["end"] = total
    min_len = float(cfg()["music"].get("min_section", 12))
    merged = []
    for s in secs:
        if merged and s["end"] - s["start"] < min_len:
            merged[-1]["end"] = s["end"]
        elif merged and merged[-1]["folder"] == s["folder"]:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))
    if len(merged) > 1 and merged[0]["end"] - merged[0]["start"] < min_len:
        merged[1]["start"] = 0.0
        merged.pop(0)
    return merged


# ---------------------------------------------------------------- audio


def _levels():
    path = LIBRARY / "music" / "_levels.json"
    return path, read_json(path, {}) or {}


def track_gain(track, cache):
    """dB that brings a song to music.level_lufs, so quiet and loud songs sit the same under the voice."""
    from .render import measure_loudness
    key = file_sig(track) + ":" + str(Path(track).parent.name)
    if key not in cache:
        loud, _ = measure_loudness(track)
        cache[key] = loud if loud is not None and loud > -70 else None
    loud = cache[key]
    if loud is None:
        return 0.0
    return max(-12.0, min(12.0, float(cfg()["music"].get("level_lufs", -16)) - loud))


def duration_of(track, cache):
    from .render import probe_duration
    key = "dur:" + file_sig(track)
    if key not in cache:
        cache[key] = probe_duration(track)
    return cache[key]


def _chain(tracks, cache, length, out, fade_in, fade_out, cursor, folder):
    """Songs of one folder back to back with 3 s crossfades, from the cursor on, cut to length."""
    from .render import SR
    xf = 3.0
    seq, total = [], 0.0
    i = cursor.get(folder, 0)
    usable = [t for t in tracks if duration_of(t, cache) > xf + 2]
    if not usable:
        return None, []
    while total < length + 1 and len(seq) < 200:
        t = usable[i % len(usable)]
        seq.append(t)
        total += duration_of(t, cache) - (xf if seq[1:] else 0)
        i += 1
    cursor[folder] = i % len(usable)
    cmd = [ffmpeg_bin(), "-y", "-v", "error"]
    for t in seq:
        cmd += ["-i", str(t)]
    parts = [f"[{k}:a]aformat=sample_rates={SR}:channel_layouts=stereo,volume={track_gain(t, cache):.2f}dB[a{k}]"
             for k, t in enumerate(seq)]
    last = "a0"
    for k in range(1, len(seq)):
        parts.append(f"[{last}][a{k}]acrossfade=d={xf}:c1=qsin:c2=qsin[x{k}]")
        last = f"x{k}"
    parts.append(f"[{last}]apad,atrim=0:{length:.3f},afade=t=in:d={fade_in:.2f}:curve=qsin,"
                 f"afade=t=out:st={max(0, length - fade_out):.3f}:d={fade_out:.2f}:curve=qsin[m]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[m]", "-c:a", "flac", str(out)]
    run(cmd)
    return out, seq


def build_bed(video, timing, out, shot=None):
    """Write the music bed for the whole video. Returns (path or None, sections with the songs each one used)."""
    from .render import SR
    out = Path(out)
    secs = plan(video, timing, shot)
    pl = pools(video)
    playing = [s for s in secs if s["folder"] is not None]
    if not playing:
        out.unlink(missing_ok=True)
        return None, secs
    mc = cfg()["music"]
    xf = float(mc.get("crossfade", 4))
    total = timing["duration"]
    lpath, cache = _levels()
    sig = sha(BED_VERSION, [(s["folder"], round(s["start"], 2), round(s["end"], 2)) for s in secs],
              {k: [file_sig(t) for t in v] for k, v in pl.items()}, mc.get("level_lufs"), xf, round(total, 2))
    side = out.with_suffix(".json")
    old = read_json(side, {}) or {}
    if out.exists() and old.get("sig") == sig:
        return out, old.get("sections", secs)
    cursor = {}
    pieces = []
    for k, s in enumerate(secs):
        if s["folder"] is None:
            s["songs"] = []
            continue
        first, last = s["start"] <= 0.01, s["end"] >= total - 0.01
        a = 0.0 if first else max(0.0, s["start"] - xf / 2)
        b = total if last else min(total, s["end"] + xf / 2)
        piece = out.with_name(f"{out.stem}_part{k}.flac")
        made, seq = _chain(pl[s["folder"]], cache, b - a, piece, 1.5 if first else xf, 4.0 if last else xf,
                           cursor, s["folder"])
        s["songs"] = list(dict.fromkeys(t.name for t in seq))
        if made:
            pieces.append((a, made))
    write_json(lpath, cache)
    if not pieces:
        out.unlink(missing_ok=True)
        return None, secs
    if len(pieces) == 1 and pieces[0][0] == 0.0:
        Path(pieces[0][1]).replace(out)
    else:
        cmd = [ffmpeg_bin(), "-y", "-v", "error"]
        graph = []
        for k, (a, p) in enumerate(pieces):
            cmd += ["-i", str(p)]
            ms = int(round(a * 1000))
            graph.append(f"[{k}:a]aformat=sample_rates={SR}:channel_layouts=stereo,adelay={ms}|{ms}[p{k}]")
        graph.append("".join(f"[p{k}]" for k in range(len(pieces)))
                     + f"amix=inputs={len(pieces)}:duration=longest:normalize=0,apad,atrim=0:{total:.3f}[m]")
        cmd += ["-filter_complex", ";".join(graph), "-map", "[m]", "-c:a", "flac", str(out)]
        run(cmd)
        for _, p in pieces:
            Path(p).unlink(missing_ok=True)
    write_json(side, {"sig": sig, "sections": secs})
    return out, secs


def describe(secs):
    """Readable lines for the cut report and vp music."""
    lines = []
    for s in secs:
        span = f"{fmt_time(s['start'])}-{fmt_time(s['end'])}"
        if s["folder"] is None:
            lines.append(f"- {span} no music" + (f" ({s['mood']})" if s["mood"] not in SILENT else ""))
            continue
        where = "library/music" + (f"/{s['folder']}" if s["folder"] else "")
        borrowed = s["mood"] != (s["folder"] or "music")
        songs = ", ".join(s.get("songs") or [])
        lines.append(f"- {span} {s['mood']}" + (f" (borrowing {where}, its own folder is empty)" if borrowed else "")
                     + (f": {songs}" if songs else "") + (f"  [from {s['source']}]" if s.get("source") else ""))
    return lines


def folder_status(video=None):
    pl = pools(video)
    want = [str(m).lower() for m in cfg()["music"].get("moods") or []]
    names = want + sorted(k for k in pl if k and k not in want)
    rows = [(n, len(pl.get(n, []))) for n in names]
    if pl.get(""):
        rows.append(("(loose in library/music)", len(pl[""])))
    return rows


# ---------------------------------------------------------------- starter songs and credits

SB = "https://www.scottbuckley.com.au/library/"
SB_UP = SB + "wp-content/uploads/"
# Free under CC BY 4.0: fine on monetized videos as long as the description credits each song (vp does that).
STARTER = [
    ("calm", "Midvinter", SB + "midvinter/", SB_UP + "2021/12/Midvinter.mp3"),
    ("calm", "The Long Way Home", SB + "the-long-way-home/", SB_UP + "2021/05/sb_thelongwayhome.mp3"),
    ("calm", "Solace", SB + "solace/", SB_UP + "2020/05/sb_solace.mp3"),
    ("rise", "A Kind Of Hope", SB + "a-kind-of-hope/", SB_UP + "2022/10/AKindOfHope.mp3"),
    ("rise", "Resolutions", SB + "resolutions/", SB_UP + "2022/01/Resolutions.mp3"),
    ("rise", "Affirmations", SB + "affirmations/", SB_UP + "2021/06/sb_affirmations.mp3"),
    ("tense", "Intervention", SB + "intervention/", SB_UP + "2019/01/sb_intervention.mp3"),
    ("tense", "Never Dying", SB + "never-dying/", "https://www.scottbuckley.com.au/wp-content/audio/sb_neverdying.mp3"),
    ("tense", "Nightfall", SB + "nightfall/", SB_UP + "2019/05/sb_nightfall.mp3"),
    ("heavy", "The Weight of Air - Katabasis I", SB + "katabasis-i/", SB_UP + "2026/09/Katabasis_I.mp3"),
    ("heavy", "Undertow", SB + "undertow/", SB_UP + "2019/12/sb_undertow.mp3"),
    ("heavy", "Desperation", SB + "desperation/", "https://www.scottbuckley.com.au/wp-content/audio/iha/sb_iha_desperation.mp3"),
    ("reflective", "The Long Dark", SB + "the-long-dark/", SB_UP + "2023/01/TheLongDark.mp3"),
    ("reflective", "Penumbra", SB + "penumbra/", SB_UP + "2025/07/Penumbra.mp3"),
    ("reflective", "Phase Shift", SB + "phase-shift/", SB_UP + "2023/10/PhaseShift.mp3"),
]


def _starter_file(url):
    return url.rsplit("/", 1)[-1]


def credit_for(name):
    """The description credit for a song file: from the starter list, or a line in library/music/CREDITS.txt
    written as  file name | credit text."""
    for _, title, page, url in STARTER:
        if _starter_file(url).lower() == name.lower():
            return f"\"{title}\" by Scott Buckley - released under CC-BY 4.0. www.scottbuckley.com.au"
    path = LIBRARY / "music" / "CREDITS.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "|" in line:
                f, text = line.split("|", 1)
                if f.strip().lower() == name.lower() and text.strip():
                    return text.strip()
    return None


def credits(secs):
    """Credit lines for the songs a music plan used (songs with no known credit are left out)."""
    out = []
    for s in secs or []:
        for name in s.get("songs") or []:
            c = credit_for(name)
            if c and c not in out:
                out.append(c)
    return out


def get_starter(only=None):
    """Download the starter songs into library/music/<mood>/. Skips files already there."""
    import urllib.request
    from .util import say
    got, failed = 0, []
    for mood, title, page, url in STARTER:
        if only and mood not in only:
            continue
        dest = LIBRARY / "music" / mood / _starter_file(url)
        if dest.exists() and dest.stat().st_size > 100_000:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if len(data) < 100_000:
                raise RuntimeError(f"only {len(data)} bytes")
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            got += 1
            say(f"  {mood:<11} {title}  ({len(data) / 1e6:.1f} MB)")
        except Exception as e:
            failed.append((mood, title, page, str(e)))
    return got, failed
