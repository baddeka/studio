import re
import shutil
import time

from rapidfuzz import fuzz

from .util import read_json, say, warn

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
CLIP_EXT = (".mp4", ".mov", ".webm", ".mkv")
ID_RE = re.compile(r"s(\d)[-_ ]?(\d{2})(?!\d)", re.I)


def norm(text):
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    words = [w for w in text.split() if not re.fullmatch(r"[0-9a-f]{6,}", w) and not w.isdigit()]
    return " ".join(words)


def _store(video, f, sid, kind):
    folder = video.assets("images" if kind == "image" else "clips")
    for old in folder.glob(f"{sid}.*"):
        dest_old = video.assets("_old") / f"{old.stem}.{time.strftime('%Y%m%d-%H%M%S')}{old.suffix}"
        shutil.move(str(old), str(dest_old))
    dest = folder / f"{sid}{f.suffix.lower()}"
    shutil.move(str(f), str(dest))
    return dest


def sort_inbox(video, by_order=False, quiet=False):
    shot = read_json(video.shotlist)
    if not shot:
        raise SystemExit("No shot list yet.")
    inbox = video.assets("inbox")
    files = sorted((f for f in inbox.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime)
    scenes = shot["scenes"]
    ids = {s["id"].upper(): s for s in scenes}
    have_img = {f.stem.upper() for f in video.assets("images").iterdir()}
    have_clip = {f.stem.upper() for f in video.assets("clips").iterdir()}
    matched, rest = [], []

    for f in files:
        kind = "image" if f.suffix.lower() in IMAGE_EXT else "clip" if f.suffix.lower() in CLIP_EXT else None
        if not kind:
            continue
        m = ID_RE.search(f.stem)
        sid = f"S{m.group(1)}-{m.group(2)}" if m else None
        if sid and sid.upper() in ids:
            matched.append((f, sid, kind, 100))
        else:
            rest.append((f, kind))

    taken = {(sid, kind) for _, sid, kind, _ in matched}
    pairs = []
    for i, (f, kind) in enumerate(rest):
        name = norm(f.stem)
        if len(name) < 8:
            continue
        for sc in scenes:
            if kind == "image" and sc["type"] not in ("still", "motion"):
                continue
            if kind == "clip" and sc["type"] != "motion":
                continue
            target = norm(sc.get("mj_prompt", "") if kind == "image" else sc.get("kling_prompt", "") + " " + sc.get("mj_prompt", ""))
            if not target:
                continue
            prefix = target[:len(name) + 10]
            score = max(fuzz.ratio(name, prefix), fuzz.token_set_ratio(name, target) * 0.9)
            missing = sc["id"].upper() not in (have_img if kind == "image" else have_clip)
            pairs.append((score + (5 if missing else 0), i, sc["id"], kind))
    pairs.sort(reverse=True)
    used_files = set()
    for score, i, sid, kind in pairs:
        if score < 72 or i in used_files or (sid, kind) in taken:
            continue
        used_files.add(i)
        taken.add((sid, kind))
        matched.append((rest[i][0], sid, kind, round(score)))
    left = [rest[i] for i in range(len(rest)) if i not in used_files]

    if by_order and left:
        for kind in ("image", "clip"):
            queue = [f for f, k in left if k == kind]
            for sc in scenes:
                if not queue:
                    break
                want = sc["type"] in ("still", "motion") if kind == "image" else sc["type"] == "motion"
                have = have_img if kind == "image" else have_clip
                if want and sc["id"].upper() not in have and (sc["id"], kind) not in taken:
                    f = queue.pop(0)
                    matched.append((f, sc["id"], kind, 0))
                    taken.add((sc["id"], kind))
        moved = {m[0] for m in matched}
        left = [(f, k) for f, k in left if f not in moved]

    report = {"matched": [], "unmatched": [f.name for f, _ in left]}
    for f, sid, kind, score in matched:
        dest = _store(video, f, sid, kind)
        report["matched"].append({"file": f.name, "scene": sid, "kind": kind, "score": score, "saved": dest.name})
    if not quiet:
        for m in report["matched"]:
            how = "by name" if m["score"] == 100 else "by order" if m["score"] == 0 else f"prompt match {m['score']}"
            say(f"  {m['file']} -> {m['saved']} ({how})")
        say(f"sort: {len(report['matched'])} files placed, {len(report['unmatched'])} left in assets/inbox")
        if report["unmatched"]:
            warn("rename the rest to the scene id (S3-07.png) or use the scene board")
    return report
