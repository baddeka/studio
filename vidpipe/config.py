import copy
import os
import shutil
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "videos"
LIBRARY = ROOT / "library"
PROMPTS = Path(__file__).resolve().parent / "prompts"

DEFAULTS = {
    "channel": {"name": "Paper Empire", "language": "English"},
    "llm": {
        "engine": "auto",
        "model": "claude-opus-5-5",
        "effort": {
            "research": "high",
            "script": "high",
            "factcheck": "high",
            "bible": "medium",
            "shotlist": "medium",
            "package": "medium",
        },
        "web_search_max_uses": 25,
        "web_fetch_max_uses": 12,
        "fallbacks": True,
        "max_usd_per_call": 8.0,
        "cli_path": "",
        "timeout_minutes": 30,
    },
    "prices": {
        "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.2, "cache_write": 5.0},
        "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.1, "cache_write": 2.5},
        "claude-haiku-5-5": {"in": 0.1, "out": 0.5, "cache_read": 0.01, "cache_write": 0.125},
        "web_search_per_1000": 10.0,
        "elevenlabs_per_1000_chars": 0.08,
    },
    "script": {"words": 3500, "wpm": 150},
    "shotlist": {
        "beat_min_words": 6,
        "beat_max_words": 16,
        "max_images": 130,
        "max_motion": 20,
    },
    "midjourney": {
        "suffix": (
            "cinematic film still, stylized digital painting, dark moody low-key lighting, "
            "deep blacks, warm gold highlights, muted teal shadows, high contrast, subtle film grain"
        ),
        "params": "--ar 16:9 --style raw",
        "sref": "",
    },
    "kling": {"duration": 5, "negative": "morphing, warped faces, extra fingers, text, watermark, flicker, fast motion"},
    "voice": {
        "engine": "elevenlabs",
        "voice_id": "",
        "model": "eleven_multilingual_v2",
        "stability": 0.45,
        "similarity": 0.8,
        "style": 0.1,
        "speed": 1.0,
        "chunk_chars": 2500,
        "pause_paragraph": 0.35,
        "pause_part": 0.9,
        "sapi_voice": "",
        "sapi_rate": 0,
    },
    "video": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "crf": 19,
        "preset": "veryfast",
        "cut_lead": 0.12,
        "tail": 1.6,
        "burn_captions": False,
        "music_db": -21,
        "duck": True,
        "sfx": True,
        "grain": True,
        "grain_strength": 6,
        "max_mbps": 8,
        "voice_compress": True,
        "loudness": -14,
        "true_peak": -2.0,
        "workers": 3,
    },
    "shorts": {"max_seconds": 59, "width": 1080, "height": 1920},
    "style": {
        "bg": "#0B0B0C",
        "glow": "#2a2112",
        "text": "#F2EFE8",
        "soft": "#d8d3c9",
        "muted": "#8f8b84",
        "gold": "#E8B54A",
        "red": "#E5484D",
        "green": "#4FBF8A",
        "panel": "#141416",
        "line": "#34343a",
        "paper": "#ece7dc",
        "ink": "#1a1a1a",
        "font_display": "library/fonts/Anton-Regular.ttf",
        "font_body": "library/fonts/Inter[opsz,wght].ttf",
        "font_mono": "library/fonts/IBMPlexMono-Regular.ttf",
        "font_mono_bold": "library/fonts/IBMPlexMono-SemiBold.ttf",
    },
    "ffmpeg": "",
}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_cfg = None


def cfg():
    global _cfg
    if _cfg is None:
        load_dotenv(ROOT / ".env")
        data = {}
        path = ROOT / "config.yaml"
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _cfg = _merge(DEFAULTS, data)
    return _cfg


def font_path(key):
    rel = cfg()["style"][key]
    p = Path(rel)
    if not p.is_absolute():
        p = ROOT / p
    if p.exists():
        return str(p)
    fallback = {
        "font_display": "C:/Windows/Fonts/impact.ttf",
        "font_body": "C:/Windows/Fonts/segoeui.ttf",
        "font_mono": "C:/Windows/Fonts/consola.ttf",
        "font_mono_bold": "C:/Windows/Fonts/consolab.ttf",
    }[key]
    return fallback


def ffmpeg_bin(name="ffmpeg"):
    custom = cfg().get("ffmpeg")
    candidates = []
    if custom:
        candidates.append(Path(custom).parent / f"{name}.exe" if Path(custom).suffix else Path(custom) / f"{name}.exe")
    candidates.append(ROOT / "tools" / "ffmpeg" / "bin" / f"{name}.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"{name} not found. Put it in tools/ffmpeg/bin or set 'ffmpeg' in config.yaml.")


class Video:
    """Paths and metadata for one video folder."""

    def __init__(self, slug):
        self.slug = slug
        self.dir = VIDEOS / slug
        if not self.dir.exists():
            raise SystemExit(f"No video '{slug}'. Create it with: vp new {slug} \"topic\"")

    @property
    def meta(self):
        p = self.dir / "video.yaml"
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}

    def save_meta(self, meta):
        (self.dir / "video.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def p(self, *parts):
        path = self.dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    research = property(lambda s: s.p("01_research.md"))
    sources = property(lambda s: s.p("sources.json"))
    script = property(lambda s: s.p("02_script.md"))
    factcheck = property(lambda s: s.p("03_factcheck.md"))
    bible = property(lambda s: s.p("04_visual_bible.json"))
    shotlist = property(lambda s: s.p("04_shotlist.json"))
    prompts_md = property(lambda s: s.p("04_prompts.md"))
    package = property(lambda s: s.p("05_package.json"))
    package_md = property(lambda s: s.p("05_package.md"))
    words = property(lambda s: s.p("audio", "words.json"))
    voice_wav = property(lambda s: s.p("audio", "voice.wav"))
    timing = property(lambda s: s.p("edit", "timing.json"))
    logs = property(lambda s: s.p("logs"))

    def assets(self, kind):
        d = self.dir / "assets" / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def edit(self, *parts):
        return self.p("edit", *parts)


def env(name):
    v = os.environ.get(name, "").strip()
    return v or None
