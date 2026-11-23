import datetime
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import PROMPTS, cfg
from .llm import LLMError, complete
from .textutil import Script, parse_script
from .util import backup, read_json, say, sha, warn, write_json, write_text

PART_SHARES = [0.035, 0.12, 0.17, 0.15, 0.24, 0.15, 0.135]
CAMERAS = ["push_in", "pull_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
CARD_KINDS = ["stat", "quote", "date", "title", "text", "document", "list", "compare"]


def prompt(name, **values):
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def style_guide():
    return prompt("style_guide", channel=cfg()["channel"]["name"])


def system_for(role):
    return f"{role}\n\n{style_guide()}"


def _meta(video):
    m = video.meta
    words = int(m.get("words") or cfg()["script"]["words"])
    return {
        "topic": m.get("topic", ""),
        "angle": m.get("angle") or "the full story, told as an investigation",
        "notes": m.get("notes") or "none",
        "words": words,
    }


def script_hash(video):
    return sha(video.script.read_text(encoding="utf-8")) if video.script.exists() else ""


def research(video, force=False):
    if video.research.exists() and not force:
        say("research: already done (use --force to redo)")
        adopt_research(video)
        return
    m = _meta(video)
    text = prompt("research", today=datetime.date.today().isoformat(), **m)
    system = system_for("You are the research lead of a documentary team. You verify everything against primary sources.")
    res = complete("research", system, text, web=True, max_tokens=64000, log_dir=video.logs)
    body = res.text.strip()
    if "## Sources" not in body:
        raise LLMError("Research came back without a Sources section; see logs/ for the raw output.")
    backup(video.research)
    write_text(video.research, body + "\n")
    sources = parse_sources(body)
    check_links(sources, res.seen_urls)
    write_json(video.sources, sources)
    bad = [s for s in sources if s["status"] in ("broken", "unreachable")]
    say(f"research: {len(body.split())} words, {len(sources)} sources, {len(bad)} with dead links -> {video.research.name}")
    for s in bad:
        warn(f"source [{s['n']}] {s['status']}: {s['url']}")


def parse_sources(md):
    section = md.split("## Sources", 1)[1]
    out = []
    for line in section.splitlines():
        m = re.match(r"^\s*[-*]?\s*\[(\d+)\]\s*(.+)$", line)
        if not m:
            continue
        rest = m.group(2).strip()
        url_m = re.search(r"https?://[^\s)>\]]+", rest)
        url = url_m.group(0).rstrip(".,;") if url_m else ""
        label = rest[:url_m.start()].strip(" -–—|") if url_m else rest
        bits = [b.strip() for b in re.split(r"\s+[-–—|]\s+", label) if b.strip()]
        out.append({
            "n": int(m.group(1)),
            "title": bits[0] if bits else label,
            "publisher": bits[1] if len(bits) > 1 else "",
            "date": bits[2] if len(bits) > 2 else "",
            "url": url,
        })
    return out


def check_links(sources, seen_urls=()):
    seen = {u.rstrip("/") for u in seen_urls}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) vidpipe-linkcheck"}

    def one(s):
        s["seen_in_search"] = s["url"].rstrip("/") in seen if seen else None
        if not s["url"]:
            s["status"] = "no url"
            return
        try:
            r = requests.get(s["url"], headers=headers, timeout=12, stream=True, allow_redirects=True)
            code = r.status_code
            r.close()
            if code < 400:
                s["status"] = "ok"
            elif code in (404, 410):
                s["status"] = "broken"
            elif code >= 500:
                s["status"] = "server error"
            else:
                s["status"] = "blocked"
            s["http"] = code
        except requests.RequestException:
            s["status"] = "unreachable"

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(one, sources))


def part_targets(words):
    return [max(25, round(words * s)) for s in PART_SHARES]


def script(video, force=False):
    if video.script.exists() and not force:
        say("script: already done (use --force to redo)")
        return
    if not video.research.exists():
        raise SystemExit("Run research first.")
    m = _meta(video)
    wpm = cfg()["script"]["wpm"]
    targets = part_targets(m["words"])
    values = dict(m, wpm=wpm, minutes=round(m["words"] / wpm, 1), research=video.research.read_text(encoding="utf-8"))
    values.update({f"w{i + 1}": t for i, t in enumerate(targets)})
    system = system_for("You are the head writer of a documentary YouTube channel.")
    res = complete("script", system, prompt("script", **values), max_tokens=48000, log_dir=video.logs)
    text = _strip_fences(res.text)
    text = _fix_script(video, system, text, m["words"])
    backup(video.script)
    write_text(video.script, text.strip() + "\n")
    s = Script(text)
    say(f"script: {s.word_count} words in {len(s.parts)} parts -> {video.script.name}")


def _strip_fences(text):
    text = text.strip()
    m = re.match(r"^```(?:markdown|md)?\s*(.*?)```$", text, re.S)
    return m.group(1).strip() if m else text


def _fix_script(video, system, text, target):
    s = Script(text)
    problems = []
    if len(s.parts) != 7:
        problems.append(f"it has {len(s.parts)} parts instead of 7 '## Part N:' sections")
    if abs(s.word_count - target) > target * 0.12:
        problems.append(f"it has {s.word_count} words instead of about {target}")
    if not problems:
        return text
    say(f"  script needs a fix: {'; '.join(problems)}")
    ask = (f"This script has a problem: {'; '.join(problems)}. Rewrite it so it has exactly 7 parts with "
           f"'## Part N: Title' headings and about {target} words of narration, keeping the facts, tone and best "
           f"lines. Output only the full corrected script.\n\n{text}")
    res = complete("script-fix", system, ask, max_tokens=48000, effort="medium", log_dir=video.logs)
    fixed = _strip_fences(res.text)
    s2 = Script(fixed)
    if len(s2.parts) == 7 or len(s2.parts) > len(s.parts):
        return fixed
    warn("script fix did not help; keeping the first version")
    return text


FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["supported", "weak", "unsupported", "wrong", "risky"]},
                    "sources": {"type": "array", "items": {"type": "integer"}},
                    "note": {"type": "string"},
                    "fix_old": {"type": "string"},
                    "fix_new": {"type": "string"},
                },
                "required": ["claim", "verdict", "sources", "note", "fix_old", "fix_new"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["claims", "summary"],
    "additionalProperties": False,
}


def factcheck(video, fix=False):
    if not video.script.exists():
        raise SystemExit("Run script first.")
    text = video.script.read_text(encoding="utf-8")
    system = system_for("You are a meticulous fact-checker and media lawyer for a documentary channel.")
    res = complete("factcheck", system,
                   prompt("factcheck", script=text, research=video.research.read_text(encoding="utf-8")),
                   schema=FACT_SCHEMA, max_tokens=48000, log_dir=video.logs)
    data = res.data
    claims = data["claims"]
    flagged = [c for c in claims if c["verdict"] != "supported"]
    applied, missing = [], []
    if fix and flagged:
        backup(video.script)
        for c in flagged:
            old, new = c["fix_old"], c["fix_new"]
            if not old or old == new:
                continue
            if old in text:
                text = text.replace(old, new, 1)
                applied.append(c)
            else:
                missing.append(c)
        if applied:
            if len(parse_script(text)[1]) != len(parse_script(video.script.read_text(encoding="utf-8"))[1]):
                warn("fixes broke the part structure; not saving them")
                applied = []
            else:
                write_text(video.script, text)
    lines = [f"# Fact-check: {video.slug}", "", data["summary"], "",
             f"{len(claims)} claims checked, {len(flagged)} flagged."]
    if fix:
        lines.append(f"Fixes applied to the script: {len(applied)}. Could not place: {len(missing)}.")
    lines += ["", "| # | Verdict | Claim | Sources | Note | Fix |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(claims, 1):
        if c["verdict"] == "supported":
            continue
        status = "applied" if c in applied else ("not found" if c in missing else "")
        fixtxt = f"{c['fix_old']} -> {c['fix_new']} {status}".strip() if c["fix_old"] else ""
        lines.append(f"| {i} | {c['verdict']} | {_cell(c['claim'])} | {c['sources']} | {_cell(c['note'])} | {_cell(fixtxt)} |")
    lines += ["", "## Supported claims", ""]
    lines += [f"- {c['claim']} {c['sources']}" for c in claims if c["verdict"] == "supported"]
    write_text(video.factcheck, "\n".join(lines) + "\n")
    write_json(video.p("logs", "factcheck.json"), data)
    say(f"factcheck: {len(claims)} claims, {len(flagged)} flagged"
        + (f", {len(applied)} fixed in the script" if fix else "") + f" -> {video.factcheck.name}")
    return data


def _cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


BIBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "figures": {"type": "array", "items": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "who": {"type": "string"}, "look": {"type": "string"}},
            "required": ["ref", "who", "look"], "additionalProperties": False}},
        "places": {"type": "array", "items": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "look": {"type": "string"}},
            "required": ["ref", "look"], "additionalProperties": False}},
        "motifs": {"type": "array", "items": {"type": "string"}},
        "lighting": {"type": "string"},
    },
    "required": ["figures", "places", "motifs", "lighting"],
    "additionalProperties": False,
}


def bible(video, force=False):
    if video.bible.exists() and not force:
        return read_json(video.bible)
    system = system_for("You are the art director of a documentary channel.")
    res = complete("bible", system, prompt("bible", script=video.script.read_text(encoding="utf-8")),
                   schema=BIBLE_SCHEMA, max_tokens=16000, log_dir=video.logs)
    write_json(video.bible, res.data)
    return res.data


def bible_text(b):
    lines = ["People (use the reference name, never the real name):"]
    lines += [f"- {f['ref']} ({f['who']}): {f['look']}" for f in b["figures"]]
    lines += ["Places:"] + [f"- {p['ref']}: {p['look']}" for p in b["places"]]
    lines += ["Motifs: " + "; ".join(b["motifs"]), "Lighting: " + b["lighting"]]
    return "\n".join(lines)


SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "beats": {"type": "array", "items": {"type": "integer"}},
                "type": {"type": "string", "enum": ["still", "motion", "card", "reuse"]},
                "visual": {"type": "string"},
                "mj_prompt": {"type": "string"},
                "kling_prompt": {"type": "string"},
                "camera": {"type": "string", "enum": CAMERAS + [""]},
                "card_kind": {"type": "string", "enum": CARD_KINDS + [""]},
                "card_text": {"type": "string"},
                "card_sub": {"type": "string"},
                "card_items": {"type": "array", "items": {"type": "string"}},
                "card_tone": {"type": "string", "enum": ["neutral", "loss", "gain", "alert", ""]},
                "reuse_of": {"type": "integer"},
                "onscreen": {"type": "string"},
            },
            "required": ["beats", "type", "visual", "mj_prompt", "kling_prompt", "camera", "card_kind",
                         "card_text", "card_sub", "card_items", "card_tone", "reuse_of", "onscreen"],
            "additionalProperties": False}},
    },
    "required": ["scenes"],
    "additionalProperties": False,
}


def _same_beats(old, new):
    import difflib
    if len(old) != len(new):
        return False
    return all(difflib.SequenceMatcher(None, o["text"], n["text"]).ratio() >= 0.6 for o, n in zip(old, new))


def retire_part_assets(video, part_n):
    import shutil
    import time
    moved = 0
    for kind in ("images", "clips"):
        for f in video.assets(kind).glob(f"S{part_n}-*.*"):
            shutil.move(str(f), str(video.assets("_old") / f"{f.stem}.{time.strftime('%Y%m%d-%H%M%S')}{f.suffix}"))
            moved += 1
    return moved


def rebase_shotlist(video, s, beats):
    """After a script edit, keep the scenes of every part whose beats barely changed and
    return the parts that need new scenes."""
    old = read_json(video.shotlist)
    old_parts, new_parts = {}, {}
    for b in old["beats"]:
        old_parts.setdefault(b["part"], []).append(b)
    for b in beats:
        new_parts.setdefault(b["part"], []).append(b)
    idmap, changed = {}, []
    for n, nb in new_parts.items():
        ob = old_parts.get(n, [])
        if _same_beats(ob, nb):
            idmap.update({o["id"]: x["id"] for o, x in zip(ob, nb)})
        else:
            changed.append(n)
    kept = []
    for x in old["scenes"]:
        if x["part"] in new_parts and x["part"] not in changed:
            kept.append(dict(x, beats=[idmap[b] for b in x["beats"]]))
    return kept, changed


def shotlist(video, force=False, only_part=None):
    if not video.script.exists():
        raise SystemExit("Run script first.")
    sc = cfg()["shotlist"]
    s = Script(video.script.read_text(encoding="utf-8"))
    beats = s.beats(sc["beat_min_words"], sc["beat_max_words"])
    old = read_json(video.shotlist, {})
    kept = []
    if old and not old.get("script_hash") and not force:
        adopt_shotlist(video, old)
        return
    if old and not force and only_part is None:
        if old.get("script_hash") == script_hash(video):
            say("shotlist: already done (use --force to redo)")
            return
        kept, changed = rebase_shotlist(video, s, beats)
        if not changed:
            old.update(script_hash=script_hash(video), beats=beats, scenes=kept)
            write_json(video.shotlist, old)
            write_text(video.prompts_md, prompts_sheet(old))
            say("shotlist: script edits were small; kept every scene and its images")
            return
        say(f"shotlist: the script changed in part(s) {', '.join(map(str, changed))}; redoing only those")
    elif only_part is not None:
        if old.get("script_hash") != script_hash(video):
            raise SystemExit("The script changed since the shot list was made; run vp shotlist without --part first.")
        kept = [x for x in old["scenes"] if x["part"] != only_part]
        changed = [only_part]
    else:
        changed = [p["n"] for p in s.parts]
    if old:
        moved = sum(retire_part_assets(video, n) for n in changed)
        if moved:
            warn(f"moved {moved} images/clips of the redone part(s) to assets/_old")
    b = bible(video, force=force and only_part is None)
    btxt = bible_text(b)
    total = max(1, s.word_count)

    def run_part(part):
        n = part["n"]
        pbeats = [x for x in beats if x["part"] == n]
        share = s.part_words(n) / total
        max_img = max(3, round(sc["max_images"] * share))
        max_mot = max(1, round(sc["max_motion"] * share))
        lines = "\n".join(f"B{x['id']} ({x['w1'] - x['w0']} words): {x['text']}" for x in pbeats)
        text = prompt("shotlist", part=n, part_title=part["title"], max_motion=max_mot, max_images=max_img,
                      bible=btxt, beats=lines)
        system = system_for("You are the director and storyboard artist of a documentary channel.")
        res = complete(f"shotlist-p{n}", system, text, schema=SCENE_SCHEMA, max_tokens=48000,
                       effort=cfg()["llm"]["effort"]["shotlist"], log_dir=video.logs)
        return n, repair_scenes(res.data["scenes"], pbeats, max_mot)

    parts = [p for p in s.parts if p["n"] in changed]
    with ThreadPoolExecutor(4) as ex:
        results = dict(ex.map(run_part, parts))
    scenes = kept + [x for n in sorted(results) for x in finalize_part(results[n], n)]
    scenes.sort(key=lambda x: (x["part"], x["beats"][0]))
    data = {
        "version": 1,
        "script_hash": script_hash(video),
        "parts": [{"n": p["n"], "title": p["title"]} for p in s.parts],
        "beats": beats,
        "scenes": scenes,
    }
    backup(video.shotlist)
    write_json(video.shotlist, data)
    write_text(video.prompts_md, prompts_sheet(data))
    counts = {t: sum(1 for x in scenes if x["type"] == t) for t in ("still", "motion", "card", "reuse")}
    say(f"shotlist: {len(scenes)} scenes for {len(beats)} beats - {counts['still']} stills, {counts['motion']} motion, "
        f"{counts['card']} cards, {counts['reuse']} reused -> {video.shotlist.name}, {video.prompts_md.name}")


def repair_scenes(raw, pbeats, max_motion):
    """Make the model's scenes cover every beat once, in order, with valid fields."""
    valid = {x["id"] for x in pbeats}
    used = set()
    scenes = []
    for sc in raw:
        bs = sorted({bid for bid in sc.get("beats", []) if bid in valid and bid not in used})
        if not bs:
            continue
        used.update(bs)
        sc = dict(sc, beats=bs)
        scenes.append(sc)
    scenes.sort(key=lambda x: x["beats"][0])
    missing = sorted(valid - used)
    for bid in missing:
        prev = [x for x in scenes if x["beats"][0] < bid]
        if prev:
            prev[-1]["beats"] = sorted(prev[-1]["beats"] + [bid])
        elif scenes:
            scenes[0]["beats"] = sorted([bid] + scenes[0]["beats"])
        else:
            scenes.append({"beats": [bid], "type": "still", "visual": "", "mj_prompt": "", "kling_prompt": "",
                           "camera": "push_in", "card_kind": "", "card_text": "", "card_sub": "", "card_items": [],
                           "card_tone": "", "reuse_of": 0, "onscreen": ""})
    if missing:
        warn(f"shotlist: {len(missing)} beats were not covered and were attached to neighbours")
    motion = 0
    for i, sc in enumerate(scenes):
        t = sc["type"]
        if t == "card" and (sc["card_kind"] not in CARD_KINDS or not (sc["card_text"] or sc["card_items"])):
            t = "still"
        if t == "reuse":
            ref = sc.get("reuse_of", 0)
            if not (1 <= ref <= i and scenes[ref - 1]["type"] in ("still", "motion")):
                t = "still"
        if t in ("still", "motion") and not sc["mj_prompt"].strip():
            sc["mj_prompt"] = sc["visual"] or "a dark empty office at night, a single desk lamp"
        if t == "motion":
            motion += 1
            if motion > max_motion or not sc["kling_prompt"].strip():
                t = "still"
        if t in ("still", "reuse") and sc["camera"] not in CAMERAS:
            sc["camera"] = CAMERAS[i % 4]
        sc["type"] = t
    return scenes


def scene_id(part_n, i):
    return f"S{part_n}-{i:02d}"


def finalize_part(scenes, part_n):
    """Scene ids are per part (S3-07), so regenerating one part never renames another part's files."""
    out = []
    local_ids = {}
    m = cfg()["midjourney"]
    for i, sc in enumerate(scenes, 1):
        sid = scene_id(part_n, i)
        local_ids[i] = sid
        item = {
            "id": sid,
            "part": part_n,
            "beats": sc["beats"],
            "type": sc["type"],
            "visual": sc["visual"].strip(),
            "camera": sc["camera"] or "push_in",
            "onscreen": sc["onscreen"].strip(),
        }
        if sc["type"] in ("still", "motion"):
            item["mj_prompt"] = sc["mj_prompt"].strip().rstrip(".")
            item["mj_full"] = mj_full(item["mj_prompt"], m)
        if sc["type"] == "motion":
            item["kling_prompt"] = sc["kling_prompt"].strip()
        if sc["type"] == "card":
            item["card"] = {
                "kind": sc["card_kind"],
                "text": sc["card_text"].strip(),
                "sub": sc["card_sub"].strip(),
                "items": [x.strip() for x in sc["card_items"] if x.strip()],
                "tone": sc["card_tone"] or "neutral",
            }
        if sc["type"] == "reuse":
            item["reuse_of"] = local_ids.get(sc["reuse_of"])
        out.append(item)
    return out


def mj_full(core, m=None):
    m = m or cfg()["midjourney"]
    text = f"{core}, {m['suffix']}".strip(", ")
    params = m["params"].strip()
    if m.get("sref"):
        params += f" --sref {m['sref']}"
    return f"{text} {params}".strip()


def prompts_sheet(data):
    beats = {b["id"]: b for b in data["beats"]}
    titles = {p["n"]: p["title"] for p in data["parts"]}
    lines = ["# Prompt sheet", "",
             "Generate each image in Midjourney and each clip in Kling, then drop them on the scene board "
             "(vp board) or name the files after the scene id (S3-07.png, S3-07.mp4).", ""]
    part = None
    for sc in data["scenes"]:
        if sc["part"] != part:
            part = sc["part"]
            lines += [f"## Part {part}: {titles.get(part, '')}", ""]
        said = " ".join(beats[b]["text"] for b in sc["beats"])
        lines.append(f"### {sc['id']} - {sc['type']}")
        lines.append(f"Narration: {said}")
        if sc.get("mj_full"):
            lines += ["", "Midjourney:", "```", sc["mj_full"], "```"]
        if sc.get("kling_prompt"):
            lines += ["Kling (image to video, use the image above):", "```", sc["kling_prompt"], "```",
                      f"Kling negative prompt: {cfg()['kling']['negative']}"]
        if sc["type"] == "reuse":
            lines.append(f"Reuses the image of {sc['reuse_of']} ({sc['camera']}).")
        if sc["type"] == "card":
            c = sc["card"]
            lines.append(f"Card ({c['kind']}, rendered automatically): {c['text']} | {c['sub']} | {'; '.join(c['items'])}")
        lines.append("")
    return "\n".join(lines)


PACKAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "thumbnail_texts": {"type": "array", "items": {"type": "string"}},
        "thumbnail_prompt": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "shorts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "hook": {"type": "string"},
                "beat_from": {"type": "integer"},
                "beat_to": {"type": "integer"},
                "why": {"type": "string"},
            },
            "required": ["title", "hook", "beat_from", "beat_to", "why"],
            "additionalProperties": False}},
        "pinned_comment": {"type": "string"},
    },
    "required": ["titles", "thumbnail_texts", "thumbnail_prompt", "description", "tags", "hashtags", "shorts",
                 "pinned_comment"],
    "additionalProperties": False,
}


def package(video, force=False):
    old = read_json(video.package, {})
    if old and not force:
        if not old.get("script_hash") or old.get("made_by") == "hand":
            adopt_package(video, old)
        else:
            say("package: already done (use --force to redo)")
        render_package_md(video)
        return
    sc = cfg()["shotlist"]
    s = Script(video.script.read_text(encoding="utf-8"))
    beats = s.beats(sc["beat_min_words"], sc["beat_max_words"])
    lines = "\n".join(f"B{b['id']}: {b['text']}" for b in beats)
    system = system_for("You are the packaging strategist (titles, thumbnails, SEO) of a documentary channel.")
    res = complete("package", system, prompt("package", script=video.script.read_text(encoding="utf-8"), beats=lines),
                   schema=PACKAGE_SCHEMA, max_tokens=24000, log_dir=video.logs)
    data = res.data
    data["shorts"] = fix_shorts(data["shorts"], beats)
    data["thumbnail_full"] = mj_full(data["thumbnail_prompt"])
    data["script_hash"] = script_hash(video)
    write_json(video.package, data)
    render_package_md(video)
    say(f"package: {len(data['titles'])} titles, {len(data['shorts'])} shorts -> {video.package_md.name}")


def fix_shorts(shorts, beats):
    ids = [b["id"] for b in beats]
    words = {b["id"]: b["w1"] - b["w0"] for b in beats}
    out = []
    for sh in shorts:
        a, z = sorted((sh["beat_from"], sh["beat_to"]))
        a = min(max(a, ids[0]), ids[-1])
        z = min(max(z, a), ids[-1])
        while sum(words[i] for i in range(a, z + 1)) > 150 and z > a:
            z -= 1
        while sum(words[i] for i in range(a, z + 1)) < 60 and z < ids[-1]:
            z += 1
        out.append(dict(sh, beat_from=a, beat_to=z))
    return out


def render_package_md(video):
    data = read_json(video.package)
    if not data:
        return
    timing = read_json(video.timing)
    sources = read_json(video.sources, [])
    description, more = describe(data, timing, sources)
    comment, lost = pinned_comment(data, more)
    if lost:
        warn(f"{lost} sources fit in neither the description nor the pinned comment")
    lines = [f"# Package: {video.slug}", "", "## Titles", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(data["titles"], 1)]
    lines += ["", "## Thumbnail", ""]
    lines += [f"- Text option {i}: **{t}**" for i, t in enumerate(data["thumbnail_texts"], 1)]
    lines += ["", "Midjourney background:", "```", data["thumbnail_full"], "```", "", "## Description", "", "```",
              description, "```", "", "## Tags", "", ", ".join(data["tags"]), "", "## Pinned comment", ""]
    if more:
        lines += [f"Post this under the video and pin it: it holds the {len(more) - lost} sources that did not fit "
                  "in the description.", ""]
    lines += ["```", comment, "```", "", "## Shorts", ""]
    beat_times = {b["id"]: b for b in (timing or {}).get("beats", [])}
    for i, sh in enumerate(data["shorts"], 1):
        span = ""
        if beat_times:
            t0 = beat_times[sh["beat_from"]]["start"]
            t1 = beat_times[sh["beat_to"]]["end"]
            span = f" ({_mmss(t0)}-{_mmss(t1)}, {t1 - t0:.0f}s)"
        lines += [f"{i}. **{sh['title']}** - beats B{sh['beat_from']}-B{sh['beat_to']}{span}",
                  f"   Hook on screen: {sh['hook']}", f"   Why: {sh['why']}"]
    write_text(video.package_md, "\n".join(lines) + "\n")


def _mmss(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


DESCRIPTION_LIMIT = 4900  # YouTube allows 5,000 characters in a description
COMMENT_LIMIT = 9900  # and 10,000 in a comment


def full_description(data, timing, sources):
    return describe(data, timing, sources)[0]


def describe(data, timing, sources):
    """The YouTube description, and the source lines that did not fit in it (they go in the pinned comment)."""
    parts = [data["description"].strip()]
    if timing and timing.get("chapters"):
        ch = ["Chapters"] + [f"{_mmss(c['start'])} {c['title']}" for c in timing["chapters"]]
        if timing["chapters"][0]["start"] > 0:
            ch[1] = f"0:00 {timing['chapters'][0]['title']}"
        parts.append("\n".join(ch))
    else:
        parts.append("Chapters: filled in after the voice-over is made (run vp cut).")
    disclaimer = ("This video is for education and entertainment, not financial or legal advice. "
                  "People who have not been convicted are presumed innocent; allegations are described as such.")
    tags = " ".join(h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", []))
    tail = [disclaimer, tags]
    src = [f"[{s['n']}] {s['title']} - {s['url']}" for s in sources if s.get("url") and s.get("status") != "broken"]

    def body(n):
        block = ["Sources"] + src[:n] + (["(more sources in the pinned comment)"] if n < len(src) else [])
        text = "\n\n".join(parts + (["\n".join(block)] if src else []) + tail)
        return text.replace("<", "\u2039").replace(">", "\u203a")  # YouTube refuses < and > in a description

    keep = len(src)
    text = body(keep)
    while len(text) > DESCRIPTION_LIMIT and keep > 1:
        keep -= 1
        text = body(keep)
    return text, src[keep:]


def pinned_comment(data, more):
    """The viewer question, then the sources the description had no room for. Returns (text, sources left out)."""
    question = str(data.get("pinned_comment") or "").strip()
    lines = list(more)
    while lines:
        text = "\n\n".join(x for x in (question, "More sources\n" + "\n".join(lines)) if x)
        if len(text) <= COMMENT_LIMIT:
            return text, len(more) - len(lines)
        lines.pop()
    return question, len(more)


# Hand-made files: a dossier, shot list or package written outside the pipeline, by a person or another Claude.
# They are checked and taken over as they are. Nothing here calls Claude, and a file with problems stops the run
# with a list of what to fix, so a hand-made file is never silently thrown away and paid for again.

TONES = ["neutral", "loss", "gain", "alert"]
SCENE_TYPES = ["still", "motion", "card", "reuse"]
BEATS_HELP = ("The beat numbers come from: vp beats {slug} --json  "
              "(or, without the pipeline installed: python -m vidpipe.beats 02_script.md --json)")


def script_beats(video):
    sc = cfg()["shotlist"]
    s = Script(video.script.read_text(encoding="utf-8"))
    return s, s.beats(sc["beat_min_words"], sc["beat_max_words"])


def _stop(name, problems, hint):
    shown = problems[:25]
    lines = [f"{name} was written by hand and has {len(problems)} problem{'s' if len(problems) != 1 else ''}; "
             "nothing was changed:"]
    lines += [f"  - {p}" for p in shown]
    if len(problems) > len(shown):
        lines.append(f"  ... and {len(problems) - len(shown)} more")
    raise SystemExit("\n".join(lines + [hint]))


def _beat_list(nums):
    """[3, 7, 8, 9, 12] -> 'B3, B7-B9, B12'."""
    runs = []
    for n in sorted(set(nums)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    out = [f"B{a}" if a == b else f"B{a}-B{b}" for a, b in runs]
    return ", ".join(out[:12]) + (f" and {len(out) - 12} more" if len(out) > 12 else "")


def _scene_ref(value, part):
    """'s3-7', 'S3-07', or 7 (the 7th scene of the part, as the shot-list prompt counts them) -> 'S3-07'."""
    if isinstance(value, int) and not isinstance(value, bool):
        return scene_id(part, value) if value > 0 else ""
    m = re.fullmatch(r"\s*[Ss](\d+)-(\d+)\s*", str(value or ""))
    return scene_id(int(m.group(1)), int(m.group(2))) if m else str(value or "").strip()


def _strings(value):
    value = [value] if isinstance(value, str) else value
    return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else []


def adopt_research(video):
    """sources.json (the source list of the video description) for a dossier written or edited by hand."""
    if video.sources.exists() and video.sources.stat().st_mtime >= video.research.stat().st_mtime:
        return
    body = video.research.read_text(encoding="utf-8")
    if "## Sources" not in body:
        warn("01_research.md has no '## Sources' section, so the video description will list no sources")
        return
    sources = parse_sources(body)
    check_links(sources)
    write_json(video.sources, sources)
    bad = [s for s in sources if s["status"] in ("broken", "unreachable")]
    say(f"research: read {len(sources)} sources from {video.research.name}, {len(bad)} with dead links")
    for s in bad:
        warn(f"source [{s['n']}] {s['status']}: {s['url']}")


def adopt_shotlist(video, data):
    """Check a hand-made shot list against the script and fill in what the pipeline adds itself."""
    s, beats = script_beats(video)
    by_id = {b["id"]: b for b in beats}
    hint = "Fix them and run this again. " + BEATS_HELP.format(slug=video.slug)
    given = data.get("beats")
    if given:
        if not isinstance(given, list):
            _stop(video.shotlist.name, ['"beats" must be the list that the beats helper prints'], hint)
        for b, g in zip(beats, given):
            text = " ".join(str(g.get("text", "")).split()) if isinstance(g, dict) else ""
            if not isinstance(g, dict) or g.get("id") != b["id"] or text != b["text"]:
                _stop(video.shotlist.name, [
                    f"its beats stop matching {video.script.name} at B{b['id']} (\"{b['text'][:60]}\"): the script "
                    "changed after the shot list was written, or the beats were not made with the beats helper"], hint)
        if len(given) != len(beats):
            _stop(video.shotlist.name, [f"it has {len(given)} beats, but {video.script.name} splits into {len(beats)}"],
                  hint)
    raw = data.get("scenes")
    if not isinstance(raw, list) or not raw:
        _stop(video.shotlist.name, ["it has no scenes"], hint)
    problems, notes, scenes, covered, counters = [], [], [], [], {}
    beats_ok = True
    for i, sc in enumerate(raw, 1):
        if not isinstance(sc, dict):
            problems.append(f"scene {i} is not an object")
            beats_ok = False
            continue
        name = str(sc.get("id") or f"scene {i}")
        bl = sc.get("beats")
        bl = [bl] if isinstance(bl, int) else bl
        if not isinstance(bl, list) or not bl or not all(isinstance(x, int) and x in by_id for x in bl):
            problems.append(f"{name}: beats must be a list of beat numbers between 1 and {len(beats)}")
            beats_ok = False
            continue
        bl = sorted(bl)
        parts = sorted({by_id[x]["part"] for x in bl})
        if len(parts) > 1:
            problems.append(f"{name}: its beats ({_beat_list(bl)}) are in different parts")
            beats_ok = False
            continue
        part = parts[0]
        covered += bl
        if sc.get("part") not in (None, "", part):
            problems.append(f"{name}: part is {sc['part']}, but its beats are in part {part}")
        counters[part] = counters.get(part, 0) + 1
        sid = _scene_ref(sc.get("id") or counters[part], part)
        if not re.fullmatch(rf"S{part}-\d{{2,}}", sid):
            problems.append(f"{name}: the id must be S{part}-NN, because its beats are in part {part}")
        elif any(x["id"] == sid for x in scenes):
            problems.append(f"{sid} is the id of two scenes")
        t = str(sc.get("type") or "still").strip().lower()
        if t not in SCENE_TYPES:
            problems.append(f"{sid}: type must be one of {', '.join(SCENE_TYPES)}")
            continue
        cam = str(sc.get("camera") or "").strip().lower()
        if cam not in CAMERAS:
            if cam and t in ("still", "reuse"):
                notes.append(f"{sid}: unknown camera \"{cam}\", used {CAMERAS[len(scenes) % 4]}")
            cam = CAMERAS[len(scenes) % 4] if t in ("still", "reuse") else "push_in"
        item = {"id": sid, "part": part, "beats": bl, "type": t, "visual": str(sc.get("visual") or "").strip(),
                "camera": cam, "onscreen": str(sc.get("onscreen") or "").strip()}
        if t in ("still", "motion"):
            core = str(sc.get("mj_prompt") or "").strip().rstrip(".")
            full = str(sc.get("mj_full") or "").strip()
            if not (core or full):
                problems.append(f"{sid}: a {t} scene needs an mj_prompt")
            item["mj_prompt"] = core or full
            item["mj_full"] = full or mj_full(core)
        if t == "motion":
            item["kling_prompt"] = str(sc.get("kling_prompt") or "").strip()
            if not item["kling_prompt"]:
                problems.append(f"{sid}: a motion scene needs a kling_prompt")
        if t == "card":
            c = sc["card"] if isinstance(sc.get("card"), dict) else {
                "kind": sc.get("card_kind"), "text": sc.get("card_text"), "sub": sc.get("card_sub"),
                "items": sc.get("card_items"), "tone": sc.get("card_tone")}
            card = {"kind": str(c.get("kind") or "").strip().lower(), "text": str(c.get("text") or "").strip(),
                    "sub": str(c.get("sub") or "").strip(), "items": _strings(c.get("items")),
                    "tone": str(c.get("tone") or "neutral").strip().lower()}
            if card["kind"] not in CARD_KINDS:
                problems.append(f"{sid}: the card kind must be one of {', '.join(CARD_KINDS)}")
            elif not (card["text"] or card["items"]):
                problems.append(f"{sid}: the card has no text")
            elif card["kind"] == "compare" and len(card["items"]) != 2:
                problems.append(f"{sid}: a compare card needs exactly 2 items, each \"Label: value\"")
            if card["tone"] not in TONES:
                notes.append(f"{sid}: unknown card tone \"{card['tone']}\", used neutral")
                card["tone"] = "neutral"
            item["card"] = card
        if t == "reuse":
            ref = _scene_ref(sc.get("reuse_of"), part)
            src = next((x for x in scenes if x["id"] == ref), None)
            if not src or src["type"] not in ("still", "motion"):
                problems.append(f"{sid}: reuse_of must be the id of an earlier still or motion scene")
            item["reuse_of"] = ref
        scenes.append(item)
    want = [b["id"] for b in beats]
    if covered != want and beats_ok:
        seen = {}
        for x in covered:
            seen[x] = seen.get(x, 0) + 1
        missing = [x for x in want if x not in seen]
        twice = [x for x, n in seen.items() if n > 1]
        if missing:
            problems.append(f"beats that are in no scene: {_beat_list(missing)}")
        if twice:
            problems.append(f"beats that are in more than one scene: {_beat_list(twice)}")
        if not missing and not twice:
            at = next(i for i, (a, b) in enumerate(zip(covered, want)) if a != b)
            problems.append(f"the scenes are not in script order (around B{want[at]}); list them in the order of the script")
    if problems:
        _stop(video.shotlist.name, problems, hint)
    lim = cfg()["shotlist"]
    n_img = sum(1 for x in scenes if x["type"] in ("still", "motion"))
    n_mot = sum(1 for x in scenes if x["type"] == "motion")
    if n_img > lim["max_images"]:
        notes.append(f"{n_img} scenes need a new image; config.yaml plans for at most {lim['max_images']}")
    if n_mot > lim["max_motion"]:
        notes.append(f"{n_mot} scenes need a Kling clip; config.yaml plans for at most {lim['max_motion']}")
    long = [x["id"] for x in scenes if sum(by_id[b]["w1"] - by_id[b]["w0"] for b in x["beats"]) > 32]
    if long:
        notes.append(f"{len(long)} scenes hold more than 32 words (over about 12 seconds on screen): "
                     f"{', '.join(long[:8])}{' ...' if len(long) > 8 else ''}")
    out = {"version": 1, "script_hash": script_hash(video), "made_by": "hand",
           "parts": [{"n": p["n"], "title": p["title"]} for p in s.parts], "beats": beats, "scenes": scenes}
    backup(video.shotlist)
    write_json(video.shotlist, out)
    write_text(video.prompts_md, prompts_sheet(out))
    for n in notes:
        warn(n)
    counts = {t: sum(1 for x in scenes if x["type"] == t) for t in SCENE_TYPES}
    say(f"shotlist: took the hand-made shot list: {len(scenes)} scenes for {len(beats)} beats - {counts['still']} stills, "
        f"{counts['motion']} motion, {counts['card']} cards, {counts['reuse']} reused -> {video.prompts_md.name}")


def adopt_package(video, data):
    """Check a hand-made package, fit its Shorts to 60-150 words and add the full thumbnail prompt."""
    s, beats = script_beats(video)
    cur = script_hash(video)
    if data.get("script_hash") == cur and data.get("made_by") == "hand":
        say(f"package: already done (written by hand; vp package {video.slug} --force has Claude redo it)")
        return
    problems, shorts = [], []
    titles = _strings(data.get("titles"))
    if not titles:
        problems.append("titles: give at least one title")
    description = str(data.get("description") or "").strip()
    if not description:
        problems.append("description is empty")
    raw = data.get("shorts") or []
    for i, sh in enumerate(raw if isinstance(raw, list) else [], 1):
        a = sh.get("beat_from") if isinstance(sh, dict) else None
        z = sh.get("beat_to") if isinstance(sh, dict) else None
        if not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= len(beats) for x in (a, z)):
            problems.append(f"short {i}: beat_from and beat_to must be beat numbers between 1 and {len(beats)}")
            continue
        shorts.append({"title": str(sh.get("title") or "").strip() or f"Short {i}", "hook": str(sh.get("hook") or "").strip(),
                       "beat_from": min(a, z), "beat_to": max(a, z), "why": str(sh.get("why") or "").strip()})
    if problems:
        _stop(video.package.name, problems, "Fix them and run this again. " + BEATS_HELP.format(slug=video.slug))
    fixed = fix_shorts(shorts, beats)
    moved = [str(i) for i, (a, b) in enumerate(zip(shorts, fixed), 1)
             if (a["beat_from"], a["beat_to"]) != (b["beat_from"], b["beat_to"])]
    stale = bool(data.get("script_hash")) and data["script_hash"] != cur
    tp = str(data.get("thumbnail_prompt") or "").strip()
    out = dict(data, titles=titles, thumbnail_texts=_strings(data.get("thumbnail_texts")), thumbnail_prompt=tp,
               description=description, tags=_strings(data.get("tags")), hashtags=_strings(data.get("hashtags")),
               shorts=fixed, pinned_comment=str(data.get("pinned_comment") or "").strip(),
               thumbnail_full=str(data.get("thumbnail_full") or "").strip() or (mj_full(tp) if tp else ""),
               script_hash=cur, made_by="hand")
    backup(video.package)
    write_json(video.package, out)
    if stale:
        warn("the script changed after the hand-made package was taken; check the Shorts beat ranges in "
             f"{video.package_md.name} (or run vp package {video.slug} --force to have Claude redo the package)")
    if moved:
        warn(f"Shorts {', '.join(moved)}: beat range adjusted to hold 60-150 words of narration")
    say(f"package: took the hand-made package: {len(titles)} titles, {len(fixed)} Shorts -> {video.package_md.name}")


def adopt_handmade(video):
    """Before a step that reads the shot list or package (board, sort, cut, shorts, thumb, timing)."""
    if not video.script.exists():
        return
    shot = read_json(video.shotlist, {})
    if shot and not shot.get("script_hash"):
        adopt_shotlist(video, shot)
    pkg = read_json(video.package, {})
    if pkg and not pkg.get("script_hash"):
        adopt_package(video, pkg)
        render_package_md(video)
