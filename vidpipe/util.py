import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


def say(msg):
    print(msg, flush=True)


def warn(msg):
    print(f"! {msg}", flush=True)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha(*parts, n=12):
    h = hashlib.sha256()
    for part in parts:
        if isinstance(part, (dict, list)):
            part = json.dumps(part, sort_keys=True)
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:n]


def file_sig(path):
    """Cheap identity of a file for cache keys."""
    path = Path(path)
    if not path.exists():
        return "missing"
    st = path.stat()
    return f"{path.name}:{st.st_size}:{int(st.st_mtime)}"


def run(cmd, quiet=True, check=True, input_bytes=None, cwd=None):
    cmd = [str(c) for c in cmd]
    res = subprocess.run(
        cmd,
        input=input_bytes,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
        cwd=str(cwd) if cwd else None,
    )
    if check and res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", "replace")[-3000:]
        raise RuntimeError(f"Command failed ({res.returncode}): {' '.join(cmd[:6])} ...\n{err}")
    return res


def backup(path):
    path = Path(path)
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = path.parent / "_old" / f"{path.stem}.{stamp}{path.suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        return dest
    return None


def fmt_time(seconds, srt=False):
    seconds = max(0.0, seconds)
    if srt:
        ms = int(round(seconds * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def hex_rgb(h, alpha=None):
    h = h.lstrip("#")
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return rgb + (alpha,) if alpha is not None else rgb


def ensure_utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
