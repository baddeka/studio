import base64
import io
import json
import os
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
from vidpipe.config import cfg
from vidpipe.textutil import Script, strip_markup
from vidpipe.util import sha

PLAIN = """# T

## Part 1: Cold open
On November 8, 2022, the withdrawals stopped. Eight billion dollars was gone.

The day before, he told the world everything was fine.

## Part 2: The promise
He said the exchange was safe.
"""

MARKED = """# T

## Part 1: Cold open
{tense} On November 8, 2022, the withdrawals stopped. [pause] Eight billion dollars was *gone*.

[silence 1.5s]

The day before, [whispers] he told the world everything was fine.

## Part 2: The promise
{wry}

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
    timestamps = True       # False: /with-timestamps is refused, like a model without timings
    strict_settings = False  # True: refuse style/speed/previous_text, like a newer model might

    def log_message(self, *a):
        pass

    def _send(self, code, data, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Mock.seen.append((self.path, None))
        if self.path.endswith("/user/subscription"):
            used = 1000 + sum(len(b["text"]) for p, b in Mock.seen if isinstance(b, dict) and "text" in b and "model_id" in b)
            return self._send(200, json.dumps({"character_count": used, "character_limit": 30000, "tier": "starter"}).encode())
        if self.path.endswith("/voices"):
            return self._send(200, json.dumps({"voices": [{"voice_id": "abc", "name": "Narrator"}]}).encode())
        self._send(404, b"{}")

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        raw = self.rfile.read(n)
        if self.path.endswith("/forced-alignment"):
            text = raw.split(b'name="text"\r\n\r\n')[1].split(b"\r\n--")[0].decode()
            Mock.seen.append((self.path, {"align_text": text}))
            per = 0.06
            chars = [{"text": c, "start": 0.1 + i * per, "end": 0.1 + (i + 1) * per} for i, c in enumerate(text)]
            return self._send(200, json.dumps({"characters": chars}).encode())
        body = json.loads(raw)
        Mock.seen.append((self.path, body))
        st = body["voice_settings"]
        if Mock.strict_settings and ("style" in st or "speed" in st or "previous_text" in body):
            return self._send(422, b'{"detail": "invalid setting: style is not supported for this model"}')
        text = body["text"]
        per = 0.06
        audio = fake_wav(len(text) * per + 0.5)
        if "/with-timestamps" not in self.path:
            return self._send(200, audio, "audio/mpeg")
        if not Mock.timestamps:
            return self._send(422, b'{"detail": "timestamps are not available for this model"}')
        out = {"audio_base64": base64.b64encode(audio).decode(),
               "alignment": {"characters": list(text),
                             "character_start_times_seconds": [0.1 + i * per for i in range(len(text))],
                             "character_end_times_seconds": [0.1 + (i + 1) * per for i in range(len(text))]}}
        self._send(200, json.dumps(out).encode())


def decode_any(path):
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


class MarkupTests(unittest.TestCase):
    def test_markup_is_not_spoken_or_counted(self):
        a, b = Script(PLAIN), Script(MARKED)
        self.assertEqual([p["text"] for p in a.paras], [p["text"] for p in b.paras])
        self.assertEqual(a.beats(), b.beats())
        self.assertEqual([p["mood"] for p in b.paras], ["tense", "tense", "wry"])
        self.assertEqual(b.paras[0]["silence"], 1.5)
        self.assertIn("[pause]", b.paras[0]["voice"])
        self.assertIn("*gone*", b.paras[0]["voice"])

    def test_stripping_markup_gives_back_the_plain_script(self):
        self.assertEqual(strip_markup(MARKED), PLAIN)
        self.assertEqual(strip_markup(PLAIN), PLAIN)

    def test_mood_ends_at_the_next_chapter_and_with_normal(self):
        s = Script("## Part 1: A\n{grave} One.\n\nTwo.\n\n{normal} Three.\n\n## Part 2: B\nFour.\n")
        self.assertEqual([p["mood"] for p in s.paras], ["grave", "grave", None, None])

    def test_old_bracket_notes_are_still_skipped(self):
        s = Script("## Part 1: A\n[Music swells, cut to the trading floor]\n\nWords here.\n")
        self.assertEqual([p["text"] for p in s.paras], ["Words here."])

    def test_text_for_each_kind_of_model(self):
        b = Script(MARKED)
        v = dict(cfg()["voice"], model="eleven_v4")
        t = voice.render_text(b.paras[0], v)
        self.assertTrue(t.startswith(cfg()["voice"]["moods"]["tense"]["tag"]))
        self.assertIn("[pause]", t)
        self.assertIn("GONE", t)
        v2 = dict(cfg()["voice"], model="eleven_multilingual_v2")
        t2 = voice.render_text(b.paras[0], v2)
        self.assertNotIn("[", t2)
        self.assertIn('<break time="0.6s" />', t2)
        self.assertIn("gone.", t2)
        self.assertNotIn("whispers", voice.render_text(b.paras[1], v2))
        st = voice.settings_for(b.paras[0], v2)
        self.assertEqual(st["speed"], cfg()["voice"]["moods"]["tense"]["speed"])

    def test_unmarked_paragraphs_keep_their_old_cache_key(self):
        """So adding moods to a few paragraphs only re-voices those few."""
        v = cfg()["voice"]
        para = Script(PLAIN).paras[0]
        old = sha("elevenlabs", para["text"], [v["voice_id"] or voice.DEFAULT_VOICE, v["model"], v["stability"],
                                                v["similarity"], v["style"], v["speed"]], n=16)

        class V:
            def p(self, *parts):
                return Path(tempfile.gettempdir()).joinpath(*parts)

        job = voice.build_jobs(V(), Script(PLAIN), "elevenlabs", v)[0]
        self.assertEqual(job["key"], old)


class DeliveryMockTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.old = voice.API, voice.decode
        voice.API = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        voice.decode = decode_any
        os.environ["ELEVENLABS_API_KEY"] = "test-key"
        Mock.seen.clear()
        Mock.timestamps, Mock.strict_settings = True, False
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)

        class V:
            dir = root
            slug = "mock"
            script = root / "02_script.md"
            voice_wav = root / "audio" / "voice.wav"
            words = root / "audio" / "words.json"
            logs = root / "logs"
            meta = {}

            def p(self, *parts):
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return path

        V.script.write_text(MARKED, encoding="utf-8")
        self.V = V

    def tearDown(self):
        voice.API, voice.decode = self.old
        os.environ.pop("ELEVENLABS_API_KEY", None)
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def tts(self):
        return [b for p, b in Mock.seen if isinstance(b, dict) and "model_id" in b]

    def check_words(self, video):
        data = json.loads(video.words.read_text(encoding="utf-8"))
        s = Script(MARKED)
        self.assertEqual([w["text"] for w in data["words"]], [w["text"] for w in s.words])
        starts = [w["start"] for w in data["words"]]
        self.assertEqual(starts, sorted(starts))
        return data

    def test_v4_with_tags_timings_and_silence(self):
        self.V.meta = {"voice": {"model": "eleven_v4"}}
        voice.make_voice(self.V(), engine="elevenlabs")
        data = self.check_words(self.V)
        bodies = self.tts()
        first = next(b for b in bodies if "withdrawals" in b["text"])
        self.assertIn("[pause]", first["text"])
        self.assertNotIn("style", first["voice_settings"])
        self.assertNotIn("previous_text", first)
        # tag characters in the alignment do not shift the words: the first word starts after the mood tag
        tag_len = len(cfg()["voice"]["moods"]["tense"]["tag"]) + 1
        w0 = data["words"][0]
        self.assertAlmostEqual(w0["start"], 0.2 + 0.06, delta=0.02)
        self.assertGreater(tag_len, 5)
        gap = data["paras"][1]["start"] - data["paras"][0]["end"]
        self.assertAlmostEqual(gap, cfg()["voice"]["pause_paragraph"] + 1.5, delta=0.05)

    def test_model_without_timestamps_falls_back_to_forced_alignment(self):
        Mock.timestamps = False
        self.V.meta = {"voice": {"model": "eleven_v4"}}
        voice.make_voice(self.V(), engine="elevenlabs")
        self.check_words(self.V)
        aligned = [b["align_text"] for p, b in Mock.seen if isinstance(b, dict) and "align_text" in b]
        self.assertEqual(len(aligned), 3)
        self.assertTrue(all("[" not in t for t in aligned), "alignment gets the clean words, not the tags")

    def test_refused_settings_are_dropped_and_retried(self):
        Mock.strict_settings = True
        self.V.meta = {"voice": {"model": "eleven_v3", "tags": "auto"}}
        voice.make_voice(self.V(), engine="elevenlabs")
        self.check_words(self.V)
        Mock.seen.clear()
        self.V.meta = {"voice": {"model": "eleven_multilingual_v2"}}
        voice.make_voice(self.V(), engine="elevenlabs", force=True)
        ok = [b for b in self.tts() if "style" not in b["voice_settings"]]
        self.assertTrue(ok, "after a refusal the safe settings are used")
        self.assertTrue(all(b["voice_settings"]["stability"] in (0.0, 0.5, 1.0) for b in ok))

    def test_sample_is_cheap_and_reused_by_the_full_voice_over(self):
        self.V.meta = {"voice": {"model": "eleven_v4"}}
        made = voice.sample(self.V(), ["1"], open_file=False)
        self.assertEqual(len(made), 1)
        self.assertTrue(made[0].exists())
        self.assertEqual(len(self.tts()), 1)
        Mock.seen.clear()
        voice.make_voice(self.V(), engine="elevenlabs")
        self.assertEqual(len(self.tts()), 2, "the sampled paragraph is not paid for again")

    def test_sample_compares_models_and_moods(self):
        made = voice.sample(self.V(), ["3"], models="eleven_v4,eleven_multilingual_v2", moods="none,grave",
                            open_file=False)
        self.assertEqual(len(made), 4)
        texts = [b["text"] for b in self.tts()]
        self.assertTrue(any(t.startswith(cfg()["voice"]["moods"]["grave"]["tag"]) for t in texts))
        with self.assertRaises(SystemExit):
            voice.sample(self.V(), ["1-3"], max_chars=50, open_file=False)

    def test_changing_one_mood_only_revoices_that_paragraph(self):
        voice.make_voice(self.V(), engine="elevenlabs")
        plan = json.loads(self.V.words.read_text(encoding="utf-8"))["plan"]
        self.assertEqual(plan, voice.voice_plan(self.V()))
        Mock.seen.clear()
        self.V.script.write_text(MARKED.replace("{wry}", "{grave}"), encoding="utf-8")
        self.assertNotEqual(plan, voice.voice_plan(self.V()))
        voice.make_voice(self.V(), engine="elevenlabs")
        self.assertEqual(len(self.tts()), 1)


if __name__ == "__main__":
    unittest.main()
