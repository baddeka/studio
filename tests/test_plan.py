import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidpipe.shorts import clipped_scenes, short_range
from vidpipe.timing import ass_time, shorts_ass
from vidpipe.voice import SR, _spoken_len, sapi_alignment, speech_bounds, spread_over_speech


class VoiceTests(unittest.TestCase):
    def test_speech_bounds(self):
        s = np.zeros(SR * 3, dtype=np.float32)
        s[int(SR * 0.5):int(SR * 2.0)] = 0.3
        a, b = speech_bounds(s)
        self.assertAlmostEqual(a, 0.5, delta=0.02)
        self.assertAlmostEqual(b, 2.0, delta=0.02)

    def test_sapi_alignment_rescales(self):
        text = "Nobody asked where the money went."
        marks = [{"pos": 0, "len": 6, "t": 0.5}, {"pos": 7, "len": 5, "t": 2.0}, {"pos": 13, "len": 5, "t": 3.0},
                 {"pos": 19, "len": 3, "t": 4.0}, {"pos": 23, "len": 5, "t": 4.6}, {"pos": 29, "len": 4, "t": 5.6}]
        s = np.zeros(SR * 4, dtype=np.float32)
        s[int(SR * 0.2):int(SR * 3.5)] = 0.2
        al = sapi_alignment(text, marks, s)
        self.assertEqual(len(al["start"]), len(text))
        self.assertAlmostEqual(al["start"][0], 0.2, delta=0.02)
        self.assertTrue(all(b >= a for a, b in zip(al["start"], al["start"][1:])))
        self.assertLessEqual(max(al["end"]), 4.0)

    def test_spoken_numbers(self):
        self.assertEqual(_spoken_len("1920"), 14)
        self.assertGreater(_spoken_len("$5,400"), 25)
        self.assertIsNone(_spoken_len("word"))

    def test_own_recording_without_aligner(self):
        """Words of a recording are timed from its pauses closely enough for scene cuts."""
        from vidpipe.selftest import SCRIPT, fake_speech
        from vidpipe.textutil import Script
        text = "\n\n".join(p["text"] for p in Script(SCRIPT).paras)
        samples, _, starts, _ = fake_speech(text.replace("\n\n", " "), seed=3)
        est, ends = spread_over_speech(samples, text)
        self.assertEqual(len(est), len(text))
        self.assertTrue(all(b >= a for a, b in zip(est, est[1:])))
        self.assertTrue(all(e >= a for a, e in zip(est, ends)))
        errors, pos = [], 0
        for word in text.replace("\n\n", " ").split(" "):
            errors.append(abs(est[pos] - starts[pos]))
            pos += len(word) + 1
        self.assertLess(sum(errors) / len(errors), 0.3)


class ShortsTests(unittest.TestCase):
    def setUp(self):
        self.timing = {
            "fps": 30,
            "beats": [{"id": i, "start": i * 4.0, "end": i * 4.0 + 3.8} for i in range(1, 30)],
            "scenes": [{"id": f"S1-{i:02d}", "part": 1, "f0": i * 120, "frames": 120,
                        "start": i * 4.0, "end": i * 4.0 + 4.0} for i in range(0, 30)],
        }
        self.words = [{"text": "word." if i % 8 == 7 else "word", "start": i * 0.4, "end": i * 0.4 + 0.35}
                      for i in range(400)]

    def test_short_range_caps_length(self):
        t0, t1 = short_range({"beat_from": 2, "beat_to": 25}, self.timing, self.words, 59)
        self.assertLessEqual(t1 - t0, 59.5)
        self.assertAlmostEqual(t0, 8.0 - 0.12, delta=0.01)

    def test_clipped_scenes_cover_range(self):
        scenes = clipped_scenes(self.timing, 10.0, 30.0)
        self.assertEqual(sum(s["frames"] for s in scenes), 600)
        self.assertEqual(scenes[0]["f0"], 300)

    def test_ass_file(self):
        out = Path(__file__).with_name("_tmp_short.ass")
        shorts_ass(self.words, 10.0, 30.0, "He kept the money", 1080, 1920, out)
        text = out.read_text(encoding="utf-8")
        out.unlink()
        self.assertIn("Style: Cap,Anton", text)
        self.assertIn("HE KEPT THE MONEY", text)
        self.assertEqual(ass_time(61.5), "0:01:01.50")


class FcpxmlTests(unittest.TestCase):
    def test_wellformed(self):
        import tempfile
        from vidpipe import cut
        from vidpipe.config import cfg

        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d) / "x y & z"
            root_dir.mkdir()

            class FakeVideo:
                slug = "t & t"
                voice_wav = root_dir / "voice.wav"

                def edit(self, name):
                    return root_dir / ("_tmp_" + name)

            plan = [{"scene": {"id": f"S1-0{i}"}, "timing": {"frames": 90},
                     "job": {"out": str(root_dir / f"S1-0{i}.mp4")}} for i in range(1, 4)]
            cfg()["video"]["width"], cfg()["video"]["height"] = 1920, 1080
            cut.write_fcpxml(FakeVideo(), plan, {"fps": 30, "duration": 9.0}, root_dir / "bed.flac",
                             root_dir / "sfx.flac", voice_duration=8.0)
            root = ET.parse(FakeVideo().edit("timeline.fcpxml")).getroot()
        self.assertEqual(root.get("version"), "1.8")
        clips = root.findall(".//spine/asset-clip")
        self.assertEqual(len(clips), 3)
        self.assertEqual(clips[1].get("offset"), "90/30s")
        src = root.find(".//asset[@id='a1']").get("src")
        self.assertTrue(src.startswith("file:///"), src)
        self.assertIn("x%20y%20%26%20z", src)
        self.assertEqual(root.find(".//asset[@id='av']").get("duration"), "240/30s")
        lanes = [c.get("lane") for c in clips[0].findall("asset-clip")]
        self.assertEqual(lanes, ["-1", "-2", "-3"])


if __name__ == "__main__":
    unittest.main()
