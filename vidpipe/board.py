import json
import mimetypes
import shutil
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from .assets import sort_inbox
from .cut import CLIP_EXT, IMAGE_EXT, find_assets
from .util import read_json

PAGE = Path(__file__).with_name("board.html")


def state(video):
    shot = read_json(video.shotlist)
    if not shot:
        raise SystemExit("No shot list yet. Run: vp shotlist " + video.slug)
    timing = read_json(video.timing, {})
    times = {s["id"]: s for s in timing.get("scenes", [])}
    beats = {b["id"]: b["text"] for b in shot["beats"]}
    assets = find_assets(video)
    scenes = []
    need_img = have_img = need_clip = have_clip = 0
    for sc in shot["scenes"]:
        a = assets.get(sc["id"].upper(), {})
        item = {k: sc.get(k) for k in ("id", "part", "type", "visual", "camera", "onscreen", "mj_full", "kling_prompt",
                                       "reuse_of", "card")}
        item["said"] = " ".join(beats[b] for b in sc["beats"])
        t = times.get(sc["id"])
        item["time"] = [t["start"], t["end"]] if t else None
        item["image"] = f"/media/images/{a['image'].name}?v={int(a['image'].stat().st_mtime)}" if a.get("image") else None
        item["clip"] = f"/media/clips/{a['clip'].name}?v={int(a['clip'].stat().st_mtime)}" if a.get("clip") else None
        if sc["type"] in ("still", "motion"):
            need_img += 1
            have_img += bool(a.get("image"))
        if sc["type"] == "motion":
            need_clip += 1
            have_clip += bool(a.get("clip"))
        scenes.append(item)
    inbox = video.assets("inbox")
    from .config import cfg
    return {
        "slug": video.slug,
        "kling_negative": cfg()["kling"]["negative"],
        "parts": shot["parts"],
        "scenes": scenes,
        "counts": {"images": [have_img, need_img], "clips": [have_clip, need_clip]},
        "inbox": len([f for f in inbox.iterdir() if f.is_file()]),
    }


def save_asset(video, scene_id, kind, data, filename):
    ext = Path(filename or "").suffix.lower()
    allowed = IMAGE_EXT if kind == "image" else CLIP_EXT
    if ext not in allowed:
        ext = ".png" if kind == "image" else ".mp4"
    folder = video.assets("images" if kind == "image" else "clips")
    for old in folder.glob(f"{scene_id}.*"):
        retire(video, old)
    dest = folder / f"{scene_id}{ext}"
    dest.write_bytes(data)
    return dest


def retire(video, path):
    old = video.assets("_old")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.move(str(path), str(old / f"{path.stem}.{stamp}{path.suffix}"))


def make_handler(video):
    shot_ids = {s["id"] for s in read_json(video.shotlist)["scenes"]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _local(self):
            # Refuse requests that reach this server under another host name (DNS rebinding from a web page).
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host in ("127.0.0.1", "localhost", "::1"):
                return True
            self._json({"error": "forbidden"}, 403)
            return False

        def do_GET(self):
            if not self._local():
                return
            path = urlparse(self.path).path
            if path == "/":
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/state":
                self._json(state(video))
            elif path.startswith("/media/"):
                _, _, kind, name = path.split("/", 3)
                folder = {"images": "images", "clips": "clips"}.get(kind)
                f = (video.dir / "assets" / folder / unquote(name)) if folder else None
                if not f or not f.exists() or f.parent.resolve() != (video.dir / "assets" / folder).resolve():
                    self.send_error(404)
                    return
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)

        def do_POST(self):
            # Only the board page sends this header. A web page on another site cannot add it without a
            # CORS preflight, which this server never answers, so other sites cannot change your files.
            if not self._local():
                return
            if self.headers.get("X-VP-Board") != "1":
                self._json({"error": "forbidden"}, 403)
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            length = int(self.headers.get("Content-Length") or 0)
            if length > 300 * 1024 * 1024:
                self._json({"error": "file too large"}, 413)
                return
            body = self.rfile.read(length) if length else b""
            if parts[:2] == ["api", "upload"] and len(parts) == 4:
                sid, kind = parts[2], parts[3]
                if sid not in shot_ids or kind not in ("image", "clip"):
                    self._json({"error": "unknown scene"}, 400)
                    return
                dest = save_asset(video, sid, kind, body, unquote(self.headers.get("X-Filename", "")))
                self._json({"ok": True, "file": dest.name})
            elif parts[:2] == ["api", "fetch"]:
                req = json.loads(body or b"{}")
                sid, kind, url = req.get("scene"), req.get("kind"), req.get("url", "")
                if sid not in shot_ids or kind not in ("image", "clip") or not url.startswith(("http://", "https://")):
                    self._json({"error": "bad request"}, 400)
                    return
                try:
                    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
                    r.raise_for_status()
                except requests.RequestException as e:
                    self._json({"error": f"download failed: {e}"}, 502)
                    return
                name = Path(urlparse(url).path).name
                dest = save_asset(video, sid, kind, r.content[:300 * 1024 * 1024], name)
                self._json({"ok": True, "file": dest.name})
            elif parts[:2] == ["api", "remove"] and len(parts) == 4:
                sid, kind = parts[2], parts[3]
                if sid not in shot_ids or kind not in ("image", "clip"):
                    self._json({"error": "unknown scene"}, 400)
                    return
                folder = video.assets("images" if kind == "image" else "clips")
                for f in folder.glob(f"{sid}.*"):
                    retire(video, f)
                self._json({"ok": True})
            elif parts[:2] == ["api", "sort"]:
                self._json(sort_inbox(video, by_order=False, quiet=True))
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def serve(video, port=8765, open_browser=True):
    state(video)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(video))
    url = f"http://127.0.0.1:{port}/"
    print(f"Scene board for '{video.slug}' at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
