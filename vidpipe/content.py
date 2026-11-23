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
    if video.package.exists() and not force:
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
    lines = [f"# Package: {video.slug}", "", "## Titles", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(data["titles"], 1)]
    lines += ["", "## Thumbnail", ""]
    lines += [f"- Text option {i}: **{t}**" for i, t in enumerate(data["thumbnail_texts"], 1)]
    lines += ["", "Midjourney background:", "```", data["thumbnail_full"], "```", "", "## Description", "", "```",
              full_description(data, timing, sources), "```", "", "## Tags", "", ", ".join(data["tags"]), "",
              "## Pinned comment", "", data["pinned_comment"], "", "## Shorts", ""]
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


def full_description(data, timing, sources):
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
    src_lines = ["Sources"] + [f"[{s['n']}] {s['title']} - {s['url']}" for s in sources
                               if s.get("url") and s.get("status") != "broken"]
    body = "\n\n".join(parts + ["\n".join(src_lines)] + tail)
    while len(body) > 4900 and len(src_lines) > 2:
        src_lines.pop()
        body = "\n\n".join(parts + ["\n".join(src_lines + ["(full source list in the pinned comment)"])] + tail)
    return body
