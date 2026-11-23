"""Number a script's beats exactly the way the pipeline does, for writing a shot list or Shorts ranges by hand.

    python -m vidpipe.beats path/to/02_script.md          readable list: "B12 (9 words): text", by part
    python -m vidpipe.beats path/to/02_script.md --json   the "beats" array to paste into 04_shotlist.json

It needs nothing but Python 3, so it also runs outside an installed pipeline. vp beats <slug> does the same.
"""
import argparse
import json
import sys
from pathlib import Path

from .textutil import Script

MIN_WORDS, MAX_WORDS = 6, 16  # the defaults of shotlist.beat_min_words and beat_max_words in config.py


def sizes():
    try:
        from .config import cfg
        sc = cfg()["shotlist"]
        return sc["beat_min_words"], sc["beat_max_words"]
    except Exception:  # pyyaml or python-dotenv missing: use the defaults
        return MIN_WORDS, MAX_WORDS


def listing(script, beats):
    titles = {p["n"]: p["title"] for p in script.parts}
    lines, part = [], None
    for b in beats:
        if b["part"] != part:
            part = b["part"]
            lines += ([""] if lines else []) + [f"## Part {part}: {titles.get(part, '')}"]
        lines.append(f"B{b['id']} ({b['w1'] - b['w0']} words): {b['text']}")
    return "\n".join(lines)


def as_json(beats):
    """One beat per line, so the list stays readable when pasted into 04_shotlist.json."""
    return "[\n" + ",\n".join("  " + json.dumps(b, ensure_ascii=False) for b in beats) + "\n]"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m vidpipe.beats", description=__doc__.splitlines()[0])
    ap.add_argument("script", help="the 02_script.md file")
    ap.add_argument("--json", action="store_true", help="print the beats as JSON for 04_shotlist.json")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    script = Script(Path(a.script).read_text(encoding="utf-8"))
    beats = script.beats(*sizes())
    print(as_json(beats) if a.json else listing(script, beats))
    print(f"{len(beats)} beats, {script.word_count} words, {len(script.parts)} parts", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
