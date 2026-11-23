import json
from collections import defaultdict

from .config import VIDEOS


def load(video_dir):
    f = video_dir / "logs" / "costs.jsonl"
    if not f.exists():
        return []
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def report(slug=None):
    dirs = [VIDEOS / slug] if slug else sorted(d for d in VIDEOS.iterdir() if d.is_dir()) if VIDEOS.exists() else []
    months = defaultdict(float)
    lines = []
    for d in dirs:
        rows = load(d)
        if not rows:
            continue
        by_step = defaultdict(float)
        chars = 0
        for r in rows:
            step = r["step"].split("-p")[0]
            by_step[step] += r.get("usd", 0)
            months[r["ts"][:7]] += r.get("usd", 0)
            chars += r.get("chars", 0)
        total = sum(by_step.values())
        lines.append(f"{d.name}: ${total:.2f}  (" + ", ".join(f"{k} ${v:.2f}" for k, v in by_step.items()) + ")"
                     + (f", {chars} voice characters" if chars else ""))
    if not lines:
        return "No costs logged yet."
    lines.append("")
    lines += [f"{m}: ${v:.2f}" for m, v in sorted(months.items())]
    lines.append("Claude costs on the 'cli' engine are what the same tokens would cost on the API; "
                 "they come out of your Claude plan, not a bill.")
    return "\n".join(lines)
