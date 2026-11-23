from .config import cfg
from .util import fmt_time, read_json, warn, write_json, write_text


def compute(video):
    """Scene start/end times from the word timings, snapped to frames."""
    words = read_json(video.words)
    shot = read_json(video.shotlist)
    if not words:
        raise SystemExit("No voice-over yet. Run: vp voice " + video.slug)
    if not shot:
        raise SystemExit("No shot list yet. Run: vp shotlist " + video.slug)
    if words["script_hash"] != shot["script_hash"]:
        warn("the voice-over and the shot list were made from different script versions; redo the older one")
    v = cfg()["video"]
    fps = v["fps"]
    ws = words["words"]
    if len(ws) != shot["beats"][-1]["w1"]:
        raise SystemExit("Word count of the voice-over does not match the shot list. Redo voice or shotlist.")
    beats = []
    for b in shot["beats"]:
        beats.append({"id": b["id"], "part": b["part"], "start": ws[b["w0"]]["start"], "end": ws[b["w1"] - 1]["end"]})
    bt = {b["id"]: b for b in beats}
    total = words["duration"] + v["tail"]
    scenes = shot["scenes"]
    starts = []
    for i, sc in enumerate(scenes):
        t = 0.0 if i == 0 else max(0.0, bt[sc["beats"][0]]["start"] - v["cut_lead"])
        starts.append(round(t * fps))
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            starts[i] = starts[i - 1] + 1
    end_frame = round(total * fps)
    out = []
    for i, sc in enumerate(scenes):
        f0 = starts[i]
        f1 = starts[i + 1] if i + 1 < len(scenes) else end_frame
        out.append({"id": sc["id"], "part": sc["part"], "f0": f0, "frames": f1 - f0,
                    "start": round(f0 / fps, 3), "end": round(f1 / fps, 3)})
    short = [s["id"] for s in out if s["frames"] < fps * 0.6]
    if short:
        warn(f"{len(short)} scenes are shorter than 0.6 s: {', '.join(short[:8])}")
    chapters = []
    titles = {p["n"]: p["title"] for p in shot["parts"]}
    for s in out:
        if not chapters or chapters[-1]["part"] != s["part"]:
            chapters.append({"part": s["part"], "title": titles.get(s["part"], f"Part {s['part']}"), "start": s["start"]})
    data = {"fps": fps, "duration": round(end_frame / fps, 3), "voice_duration": words["duration"],
            "scenes": out, "beats": beats, "chapters": chapters}
    write_json(video.timing, data)
    return data


def caption_groups(words, max_chars=42, max_lines=2, max_dur=6.0):
    """Group timed words into subtitle cues."""
    cues, cur = [], []

    def flush():
        if cur:
            cues.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": list(cur)})
            cur.clear()

    for w in words:
        if cur:
            text = " ".join(x["text"] for x in cur + [w])
            too_long = len(text) > max_chars * max_lines
            too_slow = w["end"] - cur[0]["start"] > max_dur
            gap = w["start"] - cur[-1]["end"] > 0.6
            if too_long or too_slow or gap:
                flush()
        cur.append(w)
        if w["text"].endswith((".", "?", "!")) and len(" ".join(x["text"] for x in cur)) > 18:
            flush()
    flush()
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else c["end"] + 1
        c["end"] = min(max(c["end"] + 0.15, c["start"] + 0.9), nxt - 0.02)
    return cues


def split_lines(text, max_chars=42):
    if len(text) <= max_chars:
        return [text]
    words = text.split(" ")
    best, best_cost = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        cost = abs(len(a) - len(b)) + (0 if len(a) <= max_chars and len(b) <= max_chars else 100)
        if best_cost is None or cost < best_cost:
            best, best_cost = [a, b], cost
    return best


def write_srt(video, words, path, offset=0.0, t0=None, t1=None):
    sel = [w for w in words if (t0 is None or w["start"] >= t0 - 0.01) and (t1 is None or w["end"] <= t1 + 0.01)]
    cues = caption_groups(sel)
    lines = []
    for i, c in enumerate(cues, 1):
        text = " ".join(w["text"] for w in c["words"])
        lines += [str(i), f"{fmt_time(c['start'] - offset, srt=True)} --> {fmt_time(c['end'] - offset, srt=True)}",
                  "\n".join(split_lines(text)), ""]
    write_text(path, "\n".join(lines))
    return len(cues)


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_color, alpha=0):
    h = hex_color.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def shorts_ass(words, t0, t1, hook, w, h, path):
    """Word-by-word captions for a vertical short, plus the hook line on top."""
    st = cfg()["style"]
    sel = [x for x in words if x["start"] >= t0 - 0.01 and x["end"] <= t1 + 0.05]
    # libass sizes a font by its full line height, and Anton's is tall, so these numbers look larger than they render.
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: Cap,Anton,{int(h * 0.075)},{ass_color(st['text'])},{ass_color(st['gold'])},&H00000000,&H64000000,"
            f"0,0,0,0,100,100,1,0,1,{max(4, int(h * 0.004))},3,2,{int(w * 0.07)},{int(w * 0.07)},{int(h * 0.30)},1\n"
            f"Style: Hook,Anton,{int(h * 0.058)},{ass_color(st['text'])},{ass_color(st['gold'])},&H00000000,&H96000000,"
            f"0,0,0,0,100,100,1,0,3,{max(8, int(h * 0.009))},0,8,{int(w * 0.08)},{int(w * 0.08)},{int(h * 0.11)},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events = []
    if hook:
        events.append(f"Dialogue: 1,{ass_time(0)},{ass_time(min(3.2, t1 - t0))},Hook,,0,0,0,,"
                      f"{{\\fad(150,300)}}{hook.upper()}")
    gold = ass_color(st["gold"])
    groups, cur = [], []
    for x in sel:
        if cur and len(" ".join(y["text"] for y in cur + [x])) > 18:
            groups.append(cur)
            cur = []
        cur.append(x)
        if len(cur) == 3 or x["text"].endswith((".", "?", "!", ",")):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[-1]["end"] + 0.3
        for k, x in enumerate(g):
            a = x["start"] - t0
            b = (g[k + 1]["start"] if k + 1 < len(g) else g_end) - t0
            parts = []
            for j, y in enumerate(g):
                word = y["text"].upper()
                parts.append(f"{{\\c{gold}}}{word}{{\\c{ass_color(st['text'])}}}" if j == k else word)
            pop = "{\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)}" if k == 0 else ""
            events.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    write_text(path, head + "\n".join(events) + "\n")
