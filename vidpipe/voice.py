import base64
import os
import re
import difflib
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

from .config import cfg, env, ffmpeg_bin
from .content import script_hash
from .llm import log_cost
from .textutil import Script
from .util import read_json, say, sha, warn, write_json

SR = 44100
API = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"


EXPRESSIVE = ("eleven_v3", "eleven_v4")  # models that act on [audio tags]; older ones would read them out loud
PAUSES = {"pause": 0.6, "short pause": 0.35, "brief pause": 0.35, "beat": 0.4, "long pause": 1.2}


def voice_cfg(video=None):
    """config.yaml voice settings, with any 'voice:' block in the video's video.yaml on top."""
    v = dict(cfg()["voice"])
    over = (getattr(video, "meta", None) or {}).get("voice") if video is not None else None
    if isinstance(over, dict):
        moods = dict(v.get("moods") or {})
        moods.update(over.get("moods") or {})
        v.update(over)
        v["moods"] = moods
    return v


def expressive(v):
    tags = str(v.get("tags", "auto")).lower()
    if tags in ("on", "true", "yes"):
        return True
    if tags in ("off", "false", "no"):
        return False
    return str(v["model"]).startswith(EXPRESSIVE)


_warned = set()


def mood_preset(v, mood):
    if not mood:
        return {}
    preset = (v.get("moods") or {}).get(mood)
    if preset is None:
        if mood not in _warned:
            _warned.add(mood)
            warn(f"mood {{{mood}}} is not in config.yaml voice.moods; it is used as a direction tag [{mood}] only")
        return {"tag": f"[{mood}]"}
    return preset or {}


def render_text(para, v):
    """The text sent to ElevenLabs for one paragraph: the script's delivery markup turned into what the model
    understands. Expressive models (v3, v4) get the mood's direction tag, the [tags] and STRESSED words; older
    models get <break> pauses and the tags taken out, and the mood works through the voice settings instead."""
    src = para.get("voice") or para["text"]
    preset = mood_preset(v, para.get("mood"))
    if expressive(v):
        text = re.sub(r"(?<![*\w])\*(?!\*)([^*\n]+?)\*(?![*\w])", lambda m: m.group(1).upper(), src)
        tag = (preset.get("tag") or v.get("direction") or "").strip()
        if tag and not text.startswith("["):
            text = f"{tag} {text}"
        return text

    def tag(m):
        name = re.sub(r"\s+", " ", m.group(1).strip().lower())
        if name in PAUSES:
            return f' <break time="{PAUSES[name]}s" /> '
        return " "

    text = re.sub(r"\[([^\[\]\n]+)\](?!\()", tag, src)
    text = re.sub(r"(?<![*\w])\*(?!\*)([^*\n]+?)\*(?![*\w])", r"\1", text)
    return re.sub(r"\s+", " ", text).replace(" ,", ",").replace(" .", ".").strip()


def settings_for(para, v):
    preset = mood_preset(v, para.get("mood"))
    out = {k: preset.get(k, v[k]) for k in ("stability", "similarity", "style", "speed")}
    out["speed"] = max(0.7, min(1.2, float(out["speed"])))
    return out


def _voice_key(engine, v=None, st=None):
    v = v or cfg()["voice"]
    if engine == "elevenlabs":
        st = st or {k: v[k] for k in ("stability", "similarity", "style", "speed")}
        return [v["voice_id"] or DEFAULT_VOICE, v["model"], st["stability"], st["similarity"], st["style"], st["speed"]]
    return [v["sapi_voice"], v["sapi_rate"]]


def build_jobs(video, s, engine, v, only=None):
    """One job per paragraph. The cache key covers everything that changes the sound (the text as sent, voice,
    model, settings), so a later run only pays for paragraphs whose words, mood, tags or settings changed."""
    cache = video.p("audio", "cache")
    jobs = []
    for i, para in enumerate(s.paras):
        if only is not None and i not in only:
            continue
        prev = s.paras[i - 1]["text"] if i > 0 else ""
        nxt = s.paras[i + 1]["text"] if i + 1 < len(s.paras) else ""
        if engine == "elevenlabs":
            text, st = render_text(para, v), settings_for(para, v)
        else:
            text, st = para["text"], None
        key = sha(engine, text, _voice_key(engine, v, st), n=16)
        jobs.append({"i": i, "text": text, "clean": para["text"], "settings": st, "prev": prev[-400:],
                     "next": nxt[:400], "key": key,
                     "audio": cache / f"{key}.{'mp3' if engine == 'elevenlabs' else 'wav'}",
                     "align": cache / f"{key}.json"})
    return jobs


def voice_plan(video, engine=None):
    """A fingerprint of what the voice-over would be made from; vp run re-voices when it changes."""
    v = voice_cfg(video)
    engine = engine or v["engine"]
    if engine == "elevenlabs" and not env("ELEVENLABS_API_KEY"):
        engine = "sapi"
    s = Script(video.script.read_text(encoding="utf-8"))
    return sha([j["key"] for j in build_jobs(video, s, engine, v)], [p["silence"] for p in s.paras], v["pause_paragraph"], v["pause_part"])


def make_voice(video, engine=None, file=None, force=False, yes=False):
    v = voice_cfg(video)
    engine = engine or ("file" if file else v["engine"])
    if engine == "elevenlabs" and not env("ELEVENLABS_API_KEY"):
        warn("ELEVENLABS_API_KEY is not set; using the free Windows draft voice instead")
        engine = "sapi"
    s = Script(video.script.read_text(encoding="utf-8"))
    if engine == "file":
        return from_file(video, s, Path(file))
    jobs = build_jobs(video, s, engine, v)
    todo = [j for j in jobs if force or not (j["audio"].exists() and j["align"].exists())]
    n = sum(len(j["text"]) for j in todo)
    say(f"voice ({engine}{', ' + v['model'] if engine == 'elevenlabs' else ''}): {len(s.paras)} paragraphs, "
        f"{len(todo)} to generate, {n} characters")
    if engine == "elevenlabs":
        before = subscription()
        if todo and not confirm_spend(n, before, v, yes):
            raise SystemExit("Stopped before spending credits. Try a few paragraphs first with: vp sample " + video.slug)
        _elevenlabs(todo, video, v)
        report_spend(before)
    elif engine == "sapi":
        _sapi(todo)
    else:
        raise SystemExit(f"Unknown voice engine '{engine}'")
    stitch(video, s, jobs, engine, v)


def subscription():
    """ElevenLabs credits used and left this month, or None if it cannot be read."""
    key = env("ELEVENLABS_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(f"{API}/user/subscription", headers={"xi-api-key": key}, timeout=30)
        if r.status_code != 200:
            return None
        d = r.json()
        used, limit = int(d.get("character_count", 0)), int(d.get("character_limit", 0))
        return {"used": used, "limit": limit, "left": max(0, limit - used), "tier": d.get("tier", ""),
                "reset": d.get("next_character_count_reset_unix")}
    except Exception:
        return None


def credits_line(sub):
    if not sub:
        return "ElevenLabs credits: could not be read"
    reset = ""
    if sub.get("reset"):
        reset = ", resets " + time.strftime("%b %d", time.localtime(int(sub["reset"])))
    return f"ElevenLabs credits: {sub['left']:,} left of {sub['limit']:,} this month ({sub['tier']}{reset})"


def confirm_spend(n, sub, v, yes):
    if sub:
        say("  " + credits_line(sub))
        if n > sub["left"]:
            warn(f"this needs about {n:,} credits but only {sub['left']:,} are left; ElevenLabs will stop part way")
    if yes or n <= int(v.get("confirm_chars", 2500)) or not sys.stdin or not sys.stdin.isatty():
        return True
    try:
        ans = input(f"  This uses about {n:,} ElevenLabs credits. Go ahead? [y/N] ")
    except EOFError:
        return True
    return ans.strip().lower() in ("y", "yes")


def report_spend(before):
    if not before:
        return
    after = subscription()
    if after and after["used"] >= before["used"]:
        say(f"  used {after['used'] - before['used']:,} credits; {after['left']:,} left this month")


class _Mode:
    """What the chosen model accepts, learned from the first refusal and kept for the rest of the run."""

    def __init__(self, v):
        self.safe = False         # leave out settings and context the model may not take
        self.plain = False        # no /with-timestamps: plain speech, then forced alignment


def _body(job, v, mode):
    st = job["settings"]
    exp = str(v["model"]).startswith(EXPRESSIVE)
    if exp or mode.safe:
        stab = float(st["stability"])
        if str(v["model"]).startswith("eleven_v3") or mode.safe:
            stab = min((0.0, 0.5, 1.0), key=lambda x: abs(x - stab))  # v3 takes Creative 0, Natural 0.5, Robust 1
        settings = {"stability": stab, "similarity_boost": st["similarity"]}
    else:
        settings = {"stability": st["stability"], "similarity_boost": st["similarity"], "style": st["style"],
                    "use_speaker_boost": True, "speed": st["speed"]}
    body = {"text": job["text"], "model_id": v["model"], "voice_settings": settings}
    if not exp and not mode.safe:
        if job["prev"]:
            body["previous_text"] = job["prev"]
        if job["next"]:
            body["next_text"] = job["next"]
    return body


def _post(url, body, key, expect_json=True):
    for attempt in range(5):
        r = requests.post(url, json=body, headers={"xi-api-key": key}, timeout=300)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503) and attempt < 4:
            time.sleep(3 * (attempt + 1))
            continue
        return r
    return r


def _fail(r):
    detail = r.text[:400]
    if r.status_code == 401:
        raise SystemExit(f"ElevenLabs rejected the request (401). Check the API key or your quota: {detail}")
    if "quota" in detail.lower() or "credits" in detail.lower():
        raise SystemExit(f"ElevenLabs: not enough credits left this month: {detail}")
    raise SystemExit(f"ElevenLabs error {r.status_code}: {detail}")


def forced_align(audio_bytes, text, key, name="clip.mp3"):
    r = requests.post(f"{API}/forced-alignment", headers={"xi-api-key": key},
                      files={"file": (name, audio_bytes)}, data={"text": text}, timeout=600)
    if r.status_code != 200:
        raise SystemExit(f"Forced alignment failed {r.status_code}: {r.text[:400]}")
    chars = r.json().get("characters", [])
    return {"chars": [c["text"] for c in chars], "start": [c["start"] for c in chars],
            "end": [c["end"] for c in chars]}


def _elevenlabs(todo, video, v=None):
    v = v or voice_cfg(video)
    if not todo:
        return
    key = env("ELEVENLABS_API_KEY")
    voice_id = v["voice_id"] or DEFAULT_VOICE
    fmt = "output_format=mp3_44100_128"
    mode = _Mode(v)
    lock = threading.Lock()

    def one(job):
        for _ in range(4):
            plain, safe = mode.plain, mode.safe  # other paragraphs may learn something while this one waits
            body = _body(job, v, mode)
            url = f"{API}/text-to-speech/{voice_id}{'' if plain else '/with-timestamps'}?{fmt}"
            r = _post(url, body, key)
            if r.status_code == 200:
                break
            low = r.text.lower()
            if r.status_code in (400, 404, 422) and not plain and ("timestamp" in low or "alignment" in low):
                with lock:
                    if not mode.plain:
                        warn(f"{v['model']} gives no timings directly; timing the words with forced alignment instead")
                    mode.plain = True
                continue
            if r.status_code in (400, 422) and not safe and any(
                    w in low for w in ("stability", "style", "speed", "previous_text", "next_text", "setting")):
                with lock:
                    if not mode.safe:
                        warn(f"{v['model']} refused some voice settings; using only stability and similarity")
                    mode.safe = True
                continue
            _fail(r)
        else:
            _fail(r)
        job["audio"].parent.mkdir(parents=True, exist_ok=True)
        al = None
        if mode.plain and not r.headers.get("content-type", "").startswith("application/json"):
            audio = r.content
        else:
            data = r.json()
            audio = base64.b64decode(data["audio_base64"])
            a = data.get("alignment") or data.get("normalized_alignment") or {}
            if a.get("characters"):
                al = {"chars": a["characters"], "start": a.get("character_start_times_seconds", []),
                      "end": a.get("character_end_times_seconds", [])}
        if not al:
            al = forced_align(audio, job["clean"], key)
        job["audio"].write_bytes(audio)
        write_json(job["align"], dict(al, text=job["text"]))
        return len(job["text"])

    with ThreadPoolExecutor(int(v.get("parallel", 3))) as ex:
        chars = sum(ex.map(one, todo))
    usd = chars / 1000 * cfg()["prices"]["elevenlabs_per_1000_chars"]
    log_cost(video.logs, "voice", usd, engine="elevenlabs", chars=chars, model=v["model"])


def _sapi(todo):
    if not todo:
        return
    v = cfg()["voice"]
    script = Path(__file__).with_name("sapi_tts.ps1")
    tmp = todo[0]["audio"].parent
    tmp.mkdir(parents=True, exist_ok=True)
    job = {"voice": v["sapi_voice"], "rate": v["sapi_rate"], "out": str(tmp / "_sapi_out.json"),
           "items": [{"text": j["text"], "wav": str(j["audio"])} for j in todo]}
    job_file = tmp / "_sapi_job.json"
    job_file.write_text(json.dumps(job), encoding="utf-8")
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                          "-JobFile", str(job_file)], capture_output=True)
    if res.returncode != 0:
        raise RuntimeError("Windows speech failed: " + res.stderr.decode("utf-8", "replace")[-1500:])
    results = json.loads(Path(job["out"]).read_text(encoding="utf-8-sig"))
    if isinstance(results, dict):
        results = [results]
    for j, res in zip(todo, results):
        write_json(j["align"], sapi_alignment(j["text"], res["marks"], decode(j["audio"])))


def speech_bounds(samples):
    """First and last moment with sound, in seconds."""
    win = int(SR * 0.01)
    if len(samples) < win * 3:
        return 0.0, len(samples) / SR
    frames = samples[:len(samples) // win * win].reshape(-1, win)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    loud = np.nonzero(rms > max(1e-4, rms.max() * 0.04))[0]
    if not len(loud):
        return 0.0, len(samples) / SR
    return loud[0] * win / SR, (loud[-1] + 1) * win / SR


def sapi_alignment(text, marks, samples):
    """Windows speech reports word positions on its own clock. Map that clock onto the real audio
    by matching the first word to the first sound and the estimated end of the last word to the last sound."""
    dur = len(samples) / SR
    t_start, t_end = speech_bounds(samples)
    marks = sorted(marks, key=lambda m: m["t"])
    n = len(text)
    if not marks:
        step = (t_end - t_start) / max(1, n)
        st = [t_start + i * step for i in range(n)]
        return {"chars": list(text), "start": st, "end": [s + step for s in st], "text": text}
    r0 = marks[0]["t"]
    first, last = min(marks, key=lambda m: m["pos"]), max(marks, key=lambda m: m["pos"])
    per_char = (last["t"] - first["t"]) / max(1, last["pos"] - first["pos"]) if last is not first else 0.07
    r_end = max(m["t"] for m in marks) + per_char * (n - last["pos"])
    k = (t_end - t_start) / max(1e-6, r_end - r0)

    def at(t):
        return t_start + (t - r0) * k

    starts = [None] * n
    ends = [None] * n
    for i, mark in enumerate(marks):
        t0 = at(mark["t"])
        t1 = at(marks[i + 1]["t"]) if i + 1 < len(marks) else t_end
        for c in range(mark["pos"], min(n, mark["pos"] + mark["len"])):
            if starts[c] is None:
                starts[c] = t0
            ends[c] = max(ends[c] or 0.0, t1)
    prev = t_start
    for c in range(n):
        if starts[c] is None:
            starts[c] = ends[c] = prev
        prev = ends[c]
    return {"chars": list(text), "start": starts, "end": [min(e, dur) for e in ends], "text": text, "clock": k}


def sapi_voices():
    script = Path(__file__).with_name("sapi_tts.ps1")
    tmp = Path(__file__).resolve().parent.parent / "tools"
    tmp.mkdir(exist_ok=True)
    job_file = tmp / "_sapi_list.json"
    out = tmp / "_sapi_voices.json"
    job_file.write_text(json.dumps({"list": True, "out": str(out)}), encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-JobFile", str(job_file)], capture_output=True, check=True)
    return json.loads(out.read_text(encoding="utf-8-sig"))


def _wav_duration(path):
    import wave
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _read_wav(path):
    import wave
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getframerate() != SR:
            return None
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        return data


def decode(path):
    """Any audio file -> mono float32 numpy array at SR."""
    if str(path).lower().endswith(".wav"):
        data = _read_wav(path)
        if data is not None:
            return data
    res = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"cannot decode {path}: {res.stderr.decode('utf-8', 'replace')[-500:]}")
    return np.frombuffer(res.stdout, dtype=np.float32).copy()


def write_wav(path, samples):
    import wave
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def word_times(text, chars, starts, ends):
    """Map character timings onto the words of text (split on single spaces)."""
    aligned = "".join(chars)
    n = len(text)
    t0 = [None] * n
    t1 = [None] * n
    if aligned == text:
        t0, t1 = list(starts), list(ends)
    else:
        # case-blind, so STRESSED words still match; tags and <break>s in the sent text simply find no partner
        sm = difflib.SequenceMatcher(None, text.lower(), aligned.lower(), autojunk=False)
        for a, b, size in sm.get_matching_blocks():
            for k in range(size):
                t0[a + k] = starts[b + k]
                t1[a + k] = ends[b + k]
    out = []
    pos = 0
    for w in text.split(" "):
        span = range(pos, pos + len(w))
        s = [t0[c] for c in span if t0[c] is not None]
        e = [t1[c] for c in span if t1[c] is not None]
        out.append([min(s) if s else None, max(e) if e else None])
        pos += len(w) + 1
    _fill_gaps(out)
    return out


def _fill_gaps(times):
    known = [i for i, (a, b) in enumerate(times) if a is not None and b is not None]
    if not known:
        return
    for i, (a, b) in enumerate(times):
        if a is not None and b is not None:
            continue
        left = max([k for k in known if k < i], default=None)
        right = min([k for k in known if k > i], default=None)
        lo = times[left][1] if left is not None else 0.0
        hi = times[right][0] if right is not None else lo + 0.3
        span = (right if right is not None else i + 1) - (left if left is not None else i - 1) - 1
        idx = i - (left if left is not None else i - 1) - 1
        step = (hi - lo) / max(1, span)
        times[i] = [lo + step * idx, lo + step * (idx + 1)]


def clip_of(job, para):
    """One paragraph's audio trimmed to its speech, and its word times inside the clip."""
    audio = decode(job["audio"])
    al = read_json(job["align"])
    wt = word_times(para["text"], al["chars"], al["start"], al["end"])
    dur = len(audio) / SR
    first = min((a for a, _ in wt), default=0.0)
    last = max((b for _, b in wt), default=dur)
    cut0 = max(0.0, first - 0.06)
    cut1 = min(dur, last + 0.2)
    return audio[int(cut0 * SR):int(cut1 * SR)], [(a - cut0, b - cut0) for a, b in wt]


def stitch(video, s, jobs, engine, v=None):
    v = v or voice_cfg(video)
    pieces = []
    words_out = []
    paras_out = []
    t = 0.2
    pieces.append(np.zeros(int(SR * t), dtype=np.float32))
    wi = 0
    for j in jobs:
        para = s.paras[j["i"]]
        clip, wt = clip_of(j, para)
        for (a, b), w in zip(wt, para["text"].split(" ")):
            words_out.append({"i": wi, "text": w, "start": round(t + a, 3), "end": round(t + b, 3),
                              "part": para["part"], "para": j["i"]})
            wi += 1
        paras_out.append({"i": j["i"], "part": para["part"], "start": round(t, 3), "end": round(t + len(clip) / SR, 3)})
        pieces.append(clip)
        t += len(clip) / SR
        next_part = s.paras[j["i"] + 1]["part"] if j["i"] + 1 < len(s.paras) else None
        gap = v["pause_part"] if next_part is not None and next_part != para["part"] else v["pause_paragraph"]
        if next_part is None:
            gap = 0.6
        gap += para.get("silence", 0.0)
        pieces.append(np.zeros(int(SR * gap), dtype=np.float32))
        t += gap
    full = np.concatenate(pieces)
    peak = float(np.max(np.abs(full))) if len(full) else 1.0
    if peak > 0:
        full = full * min(1.0, 0.95 / peak)
    write_wav(video.voice_wav, full)
    plan = sha([j["key"] for j in jobs], [p["silence"] for p in s.paras], v["pause_paragraph"], v["pause_part"])
    data = {"engine": engine, "voice": _voice_key(engine, v), "script_hash": script_hash(video), "plan": plan,
            "duration": round(len(full) / SR, 3), "words": words_out, "paras": paras_out}
    write_json(video.words, data)
    minutes = len(full) / SR / 60
    say(f"voice: {minutes:.1f} min, {len(words_out)} words timed -> {video.voice_wav.relative_to(video.dir)}")


def _spoken_len(token):
    """About how many letters a number takes when read aloud ("$5,400" -> "five thousand four hundred dollars")."""
    m = re.fullmatch(r"(\$?)(\d[\d,]*)(\.\d+)?(%?)", token)
    if not m:
        return None
    cur, whole, dec, pct = m.groups()
    digits = whole.replace(",", "")
    if len(digits) == 4 and "," not in whole and not (cur or dec or pct) and 1100 <= int(digits) <= 2099:
        n = 14.0  # a year: "nineteen twenty"
    else:
        groups = whole.split(",") if "," in whole else [digits[max(0, i - 3):i] for i in range(len(digits), 0, -3)][::-1]
        n = 0.0
        for gi, g in enumerate(groups):
            nz = sum(1 for ch in g if ch != "0")
            n += nz * 6.5 + (8 if gi < len(groups) - 1 and nz else 0)
        n = max(n, 4.0)
    if dec:
        n += 6 + 5 * (len(dec) - 1)
    return n + (8 if cur else 0) + (8 if pct else 0)


def _text_weights(text):
    """How long each character takes to say, roughly: letters 1, spaces 0.5, punctuation 0, numbers spread out."""
    w = np.array([1.0 if c.isalpha() else 0.5 if c == " " else 0.0 for c in text])
    for m in re.finditer(r"\$?\d[\d,]*(?:\.\d+)?%?", text):
        n = _spoken_len(m.group())
        if n:
            w[m.start():m.end()] = n / (m.end() - m.start())
    return w


def spread_over_speech(samples, text):
    """Rough character times without an aligner. Each sentence end is pinned to a pause in the recording near
    where it is expected, and the characters in between are spread over the frames that have sound, so pauses
    and slow passages do not make the words drift."""
    n_chars = len(text)
    win = int(SR * 0.02)
    n = len(samples) // win
    dur = len(samples) / SR
    if n < 5 or n_chars == 0:
        return ([i / max(1, n_chars) * dur for i in range(n_chars)], [(i + 1) / max(1, n_chars) * dur for i in range(n_chars)])
    rms = np.sqrt((samples[:n * win].reshape(n, win) ** 2).mean(axis=1))
    voiced = rms > max(1e-4, float(np.percentile(rms, 95)) * 0.08)
    gap = 0
    for i in range(n):  # short dips inside and between words still count as speech
        if voiced[i]:
            if 0 < gap <= 8:
                voiced[i - gap:i] = True
            gap = 0
        else:
            gap += 1
    if not voiced.any():
        voiced[:] = True
    first, last = int(np.argmax(voiced)), int(n - np.argmax(voiced[::-1]))
    pauses, i = [], first
    while i < last:
        if voiced[i]:
            i += 1
            continue
        j = i
        while j < last and not voiced[j]:
            j += 1
        if j - i >= 10:
            pauses.append((i, j))
        i = j
    cum = np.concatenate([[0], np.cumsum(voiced)]).astype(np.float64)
    wcum = np.concatenate([[0.0], np.cumsum(_text_weights(text))])
    if wcum[-1] <= 0:
        wcum = np.arange(n_chars + 1, dtype=np.float64)
    rate = cum[-1] / wcum[-1]
    anchors = []
    last_k, last_v = 0, 0.0
    for m in re.finditer(r"[.?!][\"'”’)]*(?=\s)", text):
        k = m.end()
        span = (wcum[k] - wcum[last_k]) * rate
        expect = last_v + span
        tol = max(30.0, 0.35 * span)
        best = None
        for a, b in pauses:
            if cum[a] <= last_v or abs(cum[a] - expect) > tol:
                continue
            score = (b - a) / (1 + abs(cum[a] - expect) / tol)
            if best is None or score > best[0]:
                best = (score, a, b)
        if best:
            anchors.append((k, best[1], best[2]))
            last_k, last_v = k, cum[best[1]]
    starts, ends = [0.0] * n_chars, [0.0] * n_chars
    k0, f0 = 0, first
    for k1, a, b in anchors + [(n_chars, last, last)]:
        lc = cum[f0:a + 1] - cum[f0]
        lw = wcum[k0:k1 + 1] - wcum[k0]
        scale = lc[-1] / lw[-1] if lw[-1] > 0 else 0.0
        for c in range(k0, k1):
            fa = f0 + max(0, int(np.searchsorted(lc, lw[c - k0] * scale, side="right")) - 1)
            fb = f0 + int(np.searchsorted(lc, lw[c - k0 + 1] * scale, side="left"))
            starts[c] = fa * win / SR
            ends[c] = max(fa, min(fb, a)) * win / SR
        k0, f0 = k1, b
    return starts, [min(dur, e) for e in ends]


def from_file(video, s, path):
    if not path.exists():
        raise SystemExit(f"No such audio file: {path}")
    audio = decode(path)
    dur = len(audio) / SR
    text = "\n\n".join(p["text"] for p in s.paras)
    key = env("ELEVENLABS_API_KEY")
    if key:
        say("voice: aligning your recording with ElevenLabs forced alignment ...")
        with open(path, "rb") as f:
            r = requests.post(f"{API}/forced-alignment", headers={"xi-api-key": key},
                              files={"file": (path.name, f)}, data={"text": text}, timeout=600)
        if r.status_code != 200:
            raise SystemExit(f"Forced alignment failed {r.status_code}: {r.text[:400]}")
        chars = r.json()["characters"]
        flat = "".join(c["text"] for c in chars)
        starts = [c["start"] for c in chars]
        ends = [c["end"] for c in chars]
        sm = difflib.SequenceMatcher(None, text, flat, autojunk=False)
        t0 = [None] * len(text)
        t1 = [None] * len(text)
        for a, b, size in sm.get_matching_blocks():
            for k in range(size):
                t0[a + k] = starts[b + k]
                t1[a + k] = ends[b + k]
        log_cost(video.logs, "voice-align", 0.0, engine="elevenlabs-align", seconds=round(dur))
    else:
        warn("no ELEVENLABS_API_KEY: estimating word times from where the recording has speech (rough timing)")
        t0, t1 = spread_over_speech(audio, text)
    words_out = []
    paras_out = []
    pos = 0
    wi = 0
    for pi, para in enumerate(s.paras):
        p0 = pos
        for w in para["text"].split(" "):
            span = range(pos, pos + len(w))
            a = [t0[c] for c in span if t0[c] is not None]
            b = [t1[c] for c in span if t1[c] is not None]
            words_out.append({"i": wi, "text": w, "start": min(a) if a else None, "end": max(b) if b else None,
                              "part": para["part"], "para": pi})
            wi += 1
            pos += len(w) + 1
        pos = p0 + len(para["text"]) + 2
    pairs = [[w["start"], w["end"]] for w in words_out]
    _fill_gaps(pairs)
    for w, (a, b) in zip(words_out, pairs):
        w["start"], w["end"] = round(a, 3), round(b, 3)
    for pi, para in enumerate(s.paras):
        ws = [w for w in words_out if w["para"] == pi]
        paras_out.append({"i": pi, "part": para["part"], "start": ws[0]["start"], "end": ws[-1]["end"]})
    write_wav(video.voice_wav, audio)
    write_json(video.words, {"engine": "file", "voice": [str(path)], "script_hash": script_hash(video),
                             "duration": round(dur, 3), "words": words_out, "paras": paras_out})
    say(f"voice: {dur / 60:.1f} min from {path.name}, {len(words_out)} words timed")


def list_voices():
    key = env("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("Set ELEVENLABS_API_KEY in .env first.")
    r = requests.get(f"{API}/voices", headers={"xi-api-key": key}, timeout=60)
    r.raise_for_status()
    return [(x["voice_id"], x["name"], x.get("category", ""), ", ".join(f"{k}: {v}" for k, v in (x.get("labels") or {}).items()))
            for x in r.json().get("voices", [])]


def list_paras(video):
    """Numbered paragraphs with their mood and markup, for picking samples. Free: nothing is sent anywhere."""
    v = voice_cfg(video)
    s = Script(video.script.read_text(encoding="utf-8"))
    total = 0
    part = None
    for i, p in enumerate(s.paras, 1):
        if p["part"] != part:
            part = p["part"]
            title = next((x["title"] for x in s.parts if x["n"] == part), "")
            say(f"-- Part {part}: {title}")
        text = render_text(p, v)
        total += len(text)
        mood = f"{{{p['mood']}}} " if p["mood"] else ""
        extra = f"  +{p['silence']:g}s silence" if p["silence"] else ""
        show = p["voice"] if len(p["voice"]) <= 90 else p["voice"][:87] + "..."
        say(f"{i:>4}  {len(text):>4} ch  {mood}{show}{extra}")
    say(f"{len(s.paras)} paragraphs, about {total:,} characters for a full voice-over with {v['model']}"
        f" ({'tags on' if expressive(v) else 'tags off'}).")
    n = len(s.paras)
    say(f"Try a few first:  vp sample {video.slug} 1 {max(1, n // 3)} {max(1, 2 * n // 3)}")


def _pick(s, numbers):
    n = len(s.paras)
    if numbers:
        out = []
        for tok in numbers:
            for part in str(tok).split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    a, b = part.split("-", 1)
                    out.extend(range(int(a), int(b) + 1))
                else:
                    out.append(int(part))
        bad = [x for x in out if not 1 <= x <= n]
        if bad:
            raise SystemExit(f"Paragraph numbers go from 1 to {n} (see: vp paras <slug>); got {bad}")
        return list(dict.fromkeys(x - 1 for x in out))
    # the opening, a paragraph with pauses or stress after it, one from the middle and one from the end
    marked = [i for i, p in enumerate(s.paras) if i > 0 and p["voice"] != p["text"]]
    picks = [0] + marked[:1] + [n // 2, n - 1]
    return sorted(dict.fromkeys(i for i in picks if i < n))[:4]


def _split(value):
    return [x.strip() for x in str(value).split(",") if x.strip()] if value else [None]


def sample(video, numbers=None, voices=None, models=None, moods=None, text=None, stability=None,
           max_chars=4000, open_file=True):
    """Voice a few paragraphs (or one line) in one or more voices, models or moods, so a delivery can be judged
    for a few hundred credits instead of a whole video. The clips go into the same cache as the real voice-over,
    so whatever you keep is not paid for twice."""
    if not env("ELEVENLABS_API_KEY"):
        raise SystemExit("Set ELEVENLABS_API_KEY in .env first.")
    base = voice_cfg(video)
    if text:
        s = Script(f"## Sample\n\n{text}\n")
        idx = list(range(len(s.paras)))
    else:
        s = Script(video.script.read_text(encoding="utf-8"))
        idx = _pick(s, numbers)
    variants = []
    for vid in _split(voices):
        for model in _split(models):
            for mood in _split(moods):
                v = dict(base)
                if vid:
                    v["voice_id"] = vid
                if model:
                    v["model"] = model
                if stability is not None:
                    v["stability"] = float(stability)
                variants.append((v, mood))
    if len(variants) > 6:
        raise SystemExit(f"That is {len(variants)} combinations; keep it to 6 or fewer per sample run.")
    names = {}
    if voices:
        try:
            names = {i: n for i, n, _, _ in list_voices()}
        except Exception:
            pass
    plans = []
    for v, mood in variants:
        sp = Script(f"## Sample\n\n{text}\n") if text else s
        if mood:
            for p in sp.paras:
                p["mood"] = None if mood.lower() in ("none", "normal", "neutral") else mood.lower()
        jobs = build_jobs(video, sp, "elevenlabs", v, only=set(idx))
        plans.append((v, mood, sp, jobs))
    todo = {j["key"]: j for _, _, _, jobs in plans for j in jobs if not (j["audio"].exists() and j["align"].exists())}
    n = sum(len(j["text"]) for j in todo.values())
    say(f"sample: {len(idx)} paragraph(s) x {len(variants)} version(s); {len(todo)} clip(s) to make, about {n:,} credits")
    if n > max_chars:
        raise SystemExit(f"That is more than {max_chars:,} characters. Pick fewer or shorter paragraphs, or pass --max-chars.")
    before = subscription()
    if before:
        say("  " + credits_line(before))
    by_variant = {}
    for v, mood, sp, jobs in plans:
        miss = [j for j in jobs if j["key"] in todo and not j["audio"].exists()]
        _elevenlabs(miss, video, v)
    report_spend(before)
    out_dir = video.p("audio", "samples")
    made = []
    for v, mood, sp, jobs in plans:
        pieces = [np.zeros(int(SR * 0.3), dtype=np.float32)]
        for j in jobs:
            clip, _ = clip_of(j, sp.paras[j["i"]])
            pieces += [clip, np.zeros(int(SR * (0.8 + sp.paras[j["i"]]["silence"])), dtype=np.float32)]
        vname = names.get(v["voice_id"]) or (v["voice_id"] or "George")[:10]
        label = "_".join(x for x in [vname, v["model"].replace("eleven_", ""), mood or "", 
                                     f"stab{v['stability']:g}" if stability is not None else ""] if x)
        label = re.sub(r"[^\w.-]+", "-", label)
        which = "line" if text else "p" + "-".join(str(i + 1) for i in idx[:6])
        path = out_dir / f"{time.strftime('%H%M%S')}_{which}_{label}.wav"
        write_wav(path, np.concatenate(pieces))
        made.append(path)
        say(f"  -> audio/samples/{path.name}")
        by_variant[label] = path
    if open_file and made and hasattr(os, "startfile"):
        try:
            os.startfile(str(made[0]))
        except OSError:
            pass
    if len(made) > 1:
        say(f"Listen to each file in videos/{video.slug}/audio/samples and keep the settings you like in config.yaml.")
    return made
