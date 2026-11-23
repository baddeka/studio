import base64
import io
import json
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidpipe import voice
from vidpipe.textutil import Script

SCRIPT = """# T

## Part 1: Cold open
On November 8, 2022, the withdrawals stopped. Eight billion dollars was gone.

## Part 2: The promise
He said the exchange was safe.
"""


def fake_wav(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.SR)
        t = np.arange(int(voice.SR * seconds)) / voice.SR
        w.writeframes((np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2").tobytes())
    return buf.getvalue()


class Mock(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.seen.append((self.path, self.headers.get("xi-api-key"), body))
        text = body["text"]
        per = 0.06
        out = {
            "audio_base64": base64.b64encode(fake_wav(len(text) * per + 0.5)).decode(),
            "alignment": {"characters": list(text),
                          "character_start_times_seconds": [0.1 + i * per for i in range(len(text))],
                          "character_end_times_seconds": [0.1 + (i + 1) * per for i in range(len(text))]},
        }
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ElevenLabsMockTest(unittest.TestCase):
    def test_tts_and_stitch(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        old_api, old_decode = voice.API, voice.decode
        voice.API = f"http://127.0.0.1:{server.server_address[1]}/v1"

        def decode_any(path):
            with wave.open(str(path), "rb") as w:
                return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768

        voice.decode = decode_any
        import os
        os.environ["ELEVENLABS_API_KEY"] = "test-key"
        try:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)

                class V:
                    dir = root
                    slug = "mock"
                    script = root / "02_script.md"
                    voice_wav = root / "audio" / "voice.wav"
                    words = root / "audio" / "words.json"
                    logs = root / "logs"

                    def p(self, *parts):
                        path = root.joinpath(*parts)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        return path

                V.script.write_text(SCRIPT, encoding="utf-8")
                voice.make_voice(V(), engine="elevenlabs")
                data = json.loads(V.words.read_text(encoding="utf-8"))
                s = Script(SCRIPT)
                self.assertEqual(len(data["words"]), s.word_count)
                starts = [w["start"] for w in data["words"]]
                self.assertEqual(starts, sorted(starts))
                self.assertEqual(len(Mock.seen), 2)
                # paragraphs are requested in parallel, so pick the first paragraph's request by its text
                path, key, body = next(x for x in Mock.seen if x[2]["text"] == s.paras[0]["text"])
                self.assertIn("/with-timestamps", path)
                self.assertEqual(key, "test-key")
                self.assertEqual(body["model_id"], "eleven_multilingual_v2")
                self.assertIn("next_text", body)
                gap = data["paras"][1]["start"] - data["paras"][0]["end"]
                self.assertAlmostEqual(gap, 0.9, delta=0.05)
                Mock.seen.clear()
                voice.make_voice(V(), engine="elevenlabs")
                self.assertEqual(len(Mock.seen), 0, "cached paragraphs must not be requested again")
                costs = (V.logs / "costs.jsonl").read_text(encoding="utf-8").splitlines()
                self.assertEqual(json.loads(costs[0])["chars"], sum(len(p["text"]) for p in s.paras))
        finally:
            voice.API, voice.decode = old_api, old_decode
            os.environ.pop("ELEVENLABS_API_KEY", None)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
