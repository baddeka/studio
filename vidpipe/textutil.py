import re

ABBREV = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc", "co", "corp", "ltd", "vs", "etc",
    "u.s", "u.k", "e.g", "i.e", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec", "no", "approx", "est", "gen", "gov", "sen", "rep", "prof",
}
CONJ = {"and", "but", "or", "so", "because", "while", "which", "when", "until", "after", "before", "then", "where", "who"}
PART_RE = re.compile(r"^##\s+(?:part\s*(\d+)\s*[:.\-–—]\s*)?(.+?)\s*$", re.I)

# Delivery markup (see VOICE.md). None of it is spoken as written, shown in captions or counted as words.
#   {tense}          at the start of a paragraph: the mood for it and the rest of the chapter ({normal} ends it)
#   [whispers]       an audio tag / acting direction inside the text
#   *word*           stress this word
#   [silence 2s]     on its own line: two seconds of silence after the paragraph above
MOOD_RE = re.compile(r"^\{\s*([A-Za-z][\w -]*?)\s*\}[ \t]*")
TAG_RE = re.compile(r"\[([^\[\]\n]+)\](?!\()")
SILENCE_RE = re.compile(r"^\[\s*(?:silence|pause)\s+(\d+(?:\.\d+)?)\s*s?(?:ec(?:onds?)?)?\s*\]$", re.I)
EMPH_RE = re.compile(r"(?<![*\w])\*(?!\*)([^*\n]+?)\*(?![*\w])")


def strip_tags(text):
    """Remove [tags] from a line, taking one neighbouring space with each so the words close up again."""
    text = re.sub(r"(^|(?<=\s))\[[^\[\]\n]+\](?!\()[ \t]+", "", text)
    text = re.sub(r"[ \t]*\[[^\[\]\n]+\](?!\()", "", text)
    return text


def strip_markup(md):
    """The script file as it would read without any delivery markup. Adding markup to a script and taking it out
    again gives back the same text, so delivery edits never count as script changes."""
    lines = md.split("\n")
    out = []
    skip_blank = False
    for line in lines:
        s = line.strip()
        if skip_blank and not s:
            skip_blank = False
            continue
        skip_blank = False
        if s.startswith(("#", ">")) or (re.fullmatch(r"\[[^\[\]]*\]", s) and not SILENCE_RE.match(s)):
            out.append(line)  # headings, notes, and whole-line [notes] stay as they are
            continue
        if SILENCE_RE.match(s) or (MOOD_RE.match(s) and not MOOD_RE.sub("", s, count=1).strip()):
            # a line that only holds markup goes, with the blank line after it if one is already above it
            skip_blank = not out or not out[-1].strip() or out[-1].lstrip().startswith("#")
            continue
        line = MOOD_RE.sub("", line.lstrip(), count=1) if MOOD_RE.match(s) else line
        out.append(EMPH_RE.sub(r"\1", strip_tags(line)))
    return "\n".join(out)


def clean_inline(text):
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = re.sub(r"(\*\*|__|\*|`)", "", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def voice_inline(text):
    """Like clean_inline, but keeps the delivery markup ([tags] and *stress*) for the voice engine."""
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_script(md, detail=False):
    """Split a script into parts and narration paragraphs. Notes (> lines, [..] paragraphs, comments) are skipped.
    With detail=True each part also gets "voice": one {"src", "mood", "silence"} per paragraph (delivery markup)."""
    md = re.sub(r"<!--.*?-->", "", md.lstrip("\ufeff"), flags=re.S)
    has_parts = any(l.strip().startswith("## ") for l in md.splitlines())
    title = ""
    parts = []
    buf = []
    state = {"mood": None}

    def flush():
        if buf and parts:
            joined = " ".join(buf)
            m = SILENCE_RE.match(joined.strip())
            if m:
                target = next((p for p in reversed(parts) if p["voice"]), None)
                if target:
                    target["voice"][-1]["silence"] += float(m.group(1))
                buf.clear()
                return
            mm = MOOD_RE.match(joined)
            if mm:
                state["mood"] = None if mm.group(1).strip().lower() in ("normal", "neutral", "default") else mm.group(1).strip().lower()
                joined = joined[mm.end():]
            para = clean_inline(strip_tags(joined))
            if para and not (para.startswith("[") and para.endswith("]")):
                parts[-1]["paras"].append(para)
                parts[-1]["voice"].append({"src": voice_inline(joined), "mood": state["mood"], "silence": 0.0})
        buf.clear()

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("# ") and not title and not parts:
            title = s[2:].strip()
            continue
        m = PART_RE.match(s)
        if m and s.startswith("## "):
            flush()
            n = int(m.group(1)) if m.group(1) else len(parts) + 1
            parts.append({"n": n, "title": m.group(2).strip(), "paras": [], "voice": []})
            state["mood"] = None
            continue
        if not s:
            flush()
            continue
        if s.startswith((">", "#")) or re.fullmatch(r"[-*_=\s]{3,}", s) or re.match(r"^\(?\s*(word count|words?:)", s, re.I):
            continue
        if not parts:
            if has_parts:
                continue
            parts.append({"n": 1, "title": "Intro", "paras": [], "voice": []})
        buf.append(s)
    flush()
    parts = [p for p in parts if p["paras"]]
    if not detail:
        for p in parts:
            p.pop("voice")
    return title, parts


def split_sentences(text):
    out, start = [], 0
    for m in re.finditer(r"[.!?]+[\"'”’)]*\s+", text):
        before = text[start:m.start()]
        last = re.findall(r"[\w.]+$", before)
        if m.group().startswith(".") and last and last[-1].lower().rstrip(".") in ABBREV:
            continue
        nxt = text[m.end():m.end() + 1]
        if nxt and not (nxt.isupper() or nxt.isdigit() or nxt in "\"'“‘($€£"):
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


def _break_score(words, i):
    """Lower is better. i = index of the first word of the second half."""
    prev = words[i - 1]
    cur = words[i].lower().strip("\"'“")
    if re.search(r"[;:]$", prev):
        return 0
    if prev.endswith(","):
        return 1
    if cur in ("-", "–", "—") or prev in ("-", "–", "—"):
        return 1
    if cur in CONJ:
        return 2
    return None


def split_long(words, max_w, min_w):
    n = len(words)
    if n <= max_w:
        return [words]
    best = None
    for floor in (min_w, 3):
        for i in range(floor, n - floor + 1):
            sc = _break_score(words, i)
            if sc is None:
                continue
            cost = sc * 3 + abs(i - n / 2) / max(1, n) * 10
            if best is None or cost < best[0]:
                best = (cost, i)
        if best:
            break
    i = best[1] if best else n // 2
    return split_long(words[:i], max_w, min_w) + split_long(words[i:], max_w, min_w)


class Script:
    def __init__(self, md):
        self.title, self.parts = parse_script(md, detail=True)
        self.paras = []
        self.words = []
        for part in self.parts:
            for text, vo in zip(part["paras"], part.pop("voice")):
                toks = text.split(" ")
                w0 = len(self.words)
                for t in toks:
                    self.words.append({"text": t, "part": part["n"], "para": len(self.paras)})
                self.paras.append({"part": part["n"], "text": text, "w0": w0, "w1": len(self.words),
                                   "voice": vo["src"], "mood": vo["mood"], "silence": vo["silence"]})

    @property
    def word_count(self):
        return len(self.words)

    def part_words(self, n):
        return sum(1 for w in self.words if w["part"] == n)

    def beats(self, min_w=6, max_w=16):
        out = []
        for pi, para in enumerate(self.paras):
            pos = para["w0"]
            for sent in split_sentences(para["text"]):
                toks = sent.split(" ")
                for chunk in split_long(toks, max_w, min_w):
                    out.append({
                        "id": len(out) + 1,
                        "part": para["part"],
                        "para": pi,
                        "w0": pos,
                        "w1": pos + len(chunk),
                        "text": " ".join(chunk),
                    })
                    pos += len(chunk)
            if pos != para["w1"]:
                raise ValueError(f"beat split lost words in paragraph {pi}")
        return out

    def narration(self):
        return "\n\n".join(p["text"] for p in self.paras)


def words_text(words, i0, i1):
    return " ".join(w["text"] for w in words[i0:i1])
