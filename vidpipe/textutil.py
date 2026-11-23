import re

ABBREV = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc", "co", "corp", "ltd", "vs", "etc",
    "u.s", "u.k", "e.g", "i.e", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec", "no", "approx", "est", "gen", "gov", "sen", "rep", "prof",
}
CONJ = {"and", "but", "or", "so", "because", "while", "which", "when", "until", "after", "before", "then", "where", "who"}
PART_RE = re.compile(r"^##\s+(?:part\s*(\d+)\s*[:.\-–—]\s*)?(.+?)\s*$", re.I)


def clean_inline(text):
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = re.sub(r"(\*\*|__|\*|`)", "", text)
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_script(md):
    """Split a script into parts and narration paragraphs. Notes (> lines, [..] paragraphs, comments) are skipped."""
    md = re.sub(r"<!--.*?-->", "", md.lstrip("﻿"), flags=re.S)
    has_parts = any(l.strip().startswith("## ") for l in md.splitlines())
    title = ""
    parts = []
    buf = []

    def flush():
        if buf and parts:
            para = clean_inline(" ".join(buf))
            if para and not (para.startswith("[") and para.endswith("]")):
                parts[-1]["paras"].append(para)
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
            parts.append({"n": n, "title": m.group(2).strip(), "paras": []})
            continue
        if not s:
            flush()
            continue
        if s.startswith((">", "#")) or re.fullmatch(r"[-*_=\s]{3,}", s) or re.match(r"^\(?\s*(word count|words?:)", s, re.I):
            continue
        if not parts:
            if has_parts:
                continue
            parts.append({"n": 1, "title": "Intro", "paras": []})
        buf.append(s)
    flush()
    parts = [p for p in parts if p["paras"]]
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
        self.title, self.parts = parse_script(md)
        self.paras = []
        self.words = []
        for part in self.parts:
            for text in part["paras"]:
                toks = text.split(" ")
                w0 = len(self.words)
                for t in toks:
                    self.words.append({"text": t, "part": part["n"], "para": len(self.paras)})
                self.paras.append({"part": part["n"], "text": text, "w0": w0, "w1": len(self.words)})

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
