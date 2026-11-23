import base64
import re
import difflib
import json
import subprocess
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


def make_voice(video, engine=None, file=None, force=False):
    v = cfg()["voice"]
    engine = engine or ("file" if file else v["engine"])
    if engine == "elevenlabs" and not env("ELEVENLABS_API_KEY"):
        warn("ELEVENLABS_API_KEY is not set; using the free Windows draft voice instead")
        engine = "sapi"
    s = Script(video.script.read_text(encoding="utf-8"))
    if engine == "file":
        return from_file(video, s, Path(file))
    cache = video.p("audio", "cache")
    jobs = []
    for i, para in enumerate(s.paras):
        prev = s.paras[i - 1]["text"] if i > 0 else ""
        nxt = s.paras[i + 1]["text"] if i + 1 < len(s.paras) else ""
        key = sha(engine, para["text"], _voice_key(engine), n=16)
        jobs.append({"i": i, "text": para["text"], "prev": prev[-400:], "next": nxt[:400], "key": key,
                     "audio": cache / f"{key}.{'mp3' if engine == 'elevenlabs' else 'wav'}",
                     "align": cache / f"{key}.json"})
    todo = [j for j in jobs if force or not (j["audio"].exists() and j["align"].exists())]
    say(f"voice ({engine}): {len(s.paras)} paragraphs, {len(todo)} to generate, "
        f"{sum(len(j['text']) for j in todo)} characters")
    if engine == "elevenlabs":
        _elevenlabs(todo, video)
    elif engine == "sapi":
        _sapi(todo)
    else:
        raise SystemExit(f"Unknown voice engine '{engine}'")
    stitch(video, s, jobs, engine)


def _voice_key(engine):
    v = cfg()["voice"]
    if engine == "elevenlabs":
        return [v["voice_id"] or DEFAULT_VOICE, v["model"], v["stability"], v["similarity"], v["style"], v["speed"]]
    return [v["sapi_voice"], v["sapi_rate"]]


def _elevenlabs(todo, video):
    v = cfg()["voice"]
    key = env("ELEVENLABS_API_KEY")
    voice_id = v["voice_id"] or DEFAULT_VOICE
    url = f"{API}/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128"
    settings = {"stability": v["stability"], "similarity_boost": v["similarity"], "style": v["style"],
                "use_speaker_boost": True, "speed": v["speed"]}

    def one(job):
        body = {"text": job["text"], "model_id": v["model"], "voice_settings": settings}
        if job["prev"]:
            body["previous_text"] = job["prev"]
        if job["next"]:
            body["next_text"] = job["next"]
        for attempt in range(5):
            r = requests.post(url, json=body, headers={"xi-api-key": key}, timeout=180)
            if r.status_code == 200:
                break
            if r.status_code in (429, 500, 502, 503) and attempt < 4:
                time.sleep(3 * (attempt + 1))
                continue
            detail = r.text[:400]
            if r.status_code == 401:
                raise SystemExit(f"ElevenLabs rejected the request (401). Check the API key or your quota: {detail}")
            raise SystemExit(f"ElevenLabs error {r.status_code}: {detail}")
        data = r.json()
        job["audio"].parent.mkdir(parents=True, exist_ok=True)
        job["audio"].write_bytes(base64.b64decode(data["audio_base64"]))
        al = data.get("alignment") or data.get("normalized_alignment") or {}
        chars = al.get("characters", [])
        write_json(job["align"], {"chars": chars, "start": al.get("character_start_times_seconds", []),
                                  "end": al.get("character_end_times_seconds", []), "text": job["text"]})
        return len(job["text"])

    if not todo:
        return
    with ThreadPoolExecutor(3) as ex:
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
        sm = difflib.SequenceMatcher(None, text, aligned, autojunk=False)
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


def stitch(video, s, jobs, engine):
    v = cfg()["voice"]
    pieces = []
    words_out = []
    paras_out = []
    t = 0.2
    pieces.append(np.zeros(int(SR * t), dtype=np.float32))
    wi = 0
    for j in jobs:
        audio = decode(j["audio"])
        al = read_json(j["align"])
        para = s.paras[j["i"]]
        wt = word_times(para["text"], al["chars"], al["start"], al["end"])
        dur = len(audio) / SR
        first = min((a for a, _ in wt), default=0.0)
        last = max((b for _, b in wt), default=dur)
        cut0 = max(0.0, first - 0.06)
        cut1 = min(dur, last + 0.2)
        clip = audio[int(cut0 * SR):int(cut1 * SR)]
        for (a, b), w in zip(wt, para["text"].split(" ")):
            words_out.append({"i": wi, "text": w, "start": round(t + a - cut0, 3), "end": round(t + b - cut0, 3),
                              "part": para["part"], "para": j["i"]})
            wi += 1
        paras_out.append({"i": j["i"], "part": para["part"], "start": round(t, 3), "end": round(t + len(clip) / SR, 3)})
        pieces.append(clip)
        t += len(clip) / SR
        next_part = s.paras[j["i"] + 1]["part"] if j["i"] + 1 < len(s.paras) else None
        gap = v["pause_part"] if next_part is not None and next_part != para["part"] else v["pause_paragraph"]
        if next_part is None:
            gap = 0.6
        pieces.append(np.zeros(int(SR * gap), dtype=np.float32))
        t += gap
    full = np.concatenate(pieces)
    peak = float(np.max(np.abs(full))) if len(full) else 1.0
    if peak > 0:
        full = full * min(1.0, 0.95 / peak)
    write_wav(video.voice_wav, full)
    data = {"engine": engine, "voice": _voice_key(engine), "script_hash": script_hash(video),
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
