import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidpipe.cards import count_text
from vidpipe.content import fix_shorts, parse_sources, repair_scenes
from vidpipe.textutil import Script, split_long, split_sentences
from vidpipe.timing import caption_groups, split_lines
from vidpipe.util import fmt_time
from vidpipe.voice import word_times

SCRIPT = """# How a fund lost everything

## Part 1: Cold open
On November 8, 2022, the withdrawals stopped. Eight billion dollars of customer money was gone.

## Part 2: The promise
He told investors in the U.S. and in Asia that the exchange was safe, audited and boring, and that his own token was just a loyalty point.

[Note for the editor: show the office]

Nobody asked where the money went.
"""


class TextTests(unittest.TestCase):
    def test_parse_parts_and_notes(self):
        s = Script(SCRIPT)
        self.assertEqual(s.title, "How a fund lost everything")
        self.assertEqual([p["n"] for p in s.parts], [1, 2])
        self.assertEqual(s.parts[1]["title"], "The promise")
        self.assertEqual(len(s.paras), 3)
        self.assertNotIn("editor", s.narration())

    def test_sentences_keep_abbreviations(self):
        out = split_sentences("He moved to the U.S. in 2019. Then Mr. Smith left. Prices rose 8.7 percent!")
        self.assertEqual(len(out), 3)
        self.assertTrue(out[0].endswith("2019."))

    def test_beats_cover_every_word_once(self):
        s = Script(SCRIPT)
        beats = s.beats(6, 16)
        self.assertEqual(beats[0]["w0"], 0)
        for a, b in zip(beats, beats[1:]):
            self.assertEqual(a["w1"], b["w0"])
        self.assertEqual(beats[-1]["w1"], s.word_count)
        self.assertTrue(all(b["w1"] - b["w0"] <= 16 for b in beats))
        joined = " ".join(b["text"] for b in beats)
        self.assertEqual(joined.split(), s.narration().split())

    def test_split_long_prefers_punctuation(self):
        words = "the exchange was safe, audited and boring, and that his own token was just a loyalty point".split()
        parts = split_long(words, 12, 5)
        self.assertTrue(all(len(p) <= 12 for p in parts))
        self.assertTrue(parts[0][-1].endswith(","))


class TimingTests(unittest.TestCase):
    def test_word_times_exact_and_fuzzy(self):
        text = "Eight billion dollars"
        chars = list(text)
        starts = [i * 0.1 for i in range(len(chars))]
        ends = [s + 0.1 for s in starts]
        wt = word_times(text, chars, starts, ends)
        self.assertEqual(len(wt), 3)
        self.assertAlmostEqual(wt[0][0], 0.0)
        self.assertAlmostEqual(wt[2][1], ends[-1])
        chars2 = list("8 billion dollars")
        wt2 = word_times(text, chars2, starts[:len(chars2)], ends[:len(chars2)])
        self.assertEqual(len(wt2), 3)
        self.assertTrue(all(a is not None and b is not None and b >= a for a, b in wt2))

    def test_captions(self):
        words = [{"text": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate(
            "This is a long sentence that keeps going and going until it has to wrap. Short one.".split())]
        cues = caption_groups(words)
        self.assertGreaterEqual(len(cues), 2)
        for c in cues:
            self.assertLessEqual(len(" ".join(w["text"] for w in c["words"])), 84)
        self.assertTrue(all(len(l) <= 42 for l in split_lines("This is a long sentence that keeps going and going until")))

    def test_fmt_time(self):
        self.assertEqual(fmt_time(59.9996, srt=True), "00:01:00,000")
        self.assertEqual(fmt_time(3725.5), "1:02:05")


class ContentTests(unittest.TestCase):
    def test_repair_scenes(self):
        beats = [{"id": i} for i in range(10, 16)]
        raw = [
            {"beats": [10, 11], "type": "still", "visual": "v", "mj_prompt": "a", "kling_prompt": "", "camera": "push_in",
             "card_kind": "", "card_text": "", "card_sub": "", "card_items": [], "card_tone": "", "reuse_of": 0, "onscreen": ""},
            {"beats": [11, 13], "type": "card", "visual": "", "mj_prompt": "", "kling_prompt": "", "camera": "",
             "card_kind": "stat", "card_text": "$8B", "card_sub": "", "card_items": [], "card_tone": "loss", "reuse_of": 0, "onscreen": ""},
            {"beats": [15], "type": "reuse", "visual": "", "mj_prompt": "", "kling_prompt": "", "camera": "bad",
             "card_kind": "", "card_text": "", "card_sub": "", "card_items": [], "card_tone": "", "reuse_of": 1, "onscreen": ""},
            {"beats": [99], "type": "motion", "visual": "x", "mj_prompt": "x", "kling_prompt": "y", "camera": "",
             "card_kind": "", "card_text": "", "card_sub": "", "card_items": [], "card_tone": "", "reuse_of": 0, "onscreen": ""},
        ]
        out = repair_scenes(raw, beats, max_motion=1)
        covered = sorted(b for s in out for b in s["beats"])
        self.assertEqual(covered, list(range(10, 16)))
        self.assertEqual(out[1]["beats"][0], 13)
        self.assertEqual(out[2]["type"], "reuse")
        self.assertIn(out[2]["camera"], ("push_in", "pull_out", "pan_left", "pan_right"))

    def test_parse_sources(self):
        md = "## Sources\n[1] SEC v. Bankman-Fried complaint - SEC - Dec 13, 2022 - https://www.sec.gov/x.pdf\n" \
             "[2] FTX files for bankruptcy - Reuters - 2022 - https://reuters.com/a\n"
        s = parse_sources(md)
        self.assertEqual([x["n"] for x in s], [1, 2])
        self.assertEqual(s[0]["publisher"], "SEC")
        self.assertTrue(s[1]["url"].startswith("https://reuters.com"))

    def test_fix_shorts(self):
        beats = [{"id": i, "w0": i * 12, "w1": i * 12 + 12} for i in range(1, 40)]
        out = fix_shorts([{"title": "t", "hook": "h", "beat_from": 30, "beat_to": 3, "why": ""}], beats)
        n = sum(12 for i in range(out[0]["beat_from"], out[0]["beat_to"] + 1))
        self.assertTrue(60 <= n <= 150)


class RebaseTests(unittest.TestCase):
    def test_small_edit_keeps_scenes_and_big_edit_redoes_part(self):
        import tempfile
        from vidpipe import content
        from vidpipe.util import write_json

        old_script = Script(SCRIPT)
        old_beats = old_script.beats(6, 16)
        scenes = [{"id": f"S{b['part']}-{i:02d}", "part": b["part"], "beats": [b["id"]], "type": "still"}
                  for i, b in enumerate(old_beats, 1)]
        with tempfile.TemporaryDirectory() as d:
            class V:
                shotlist = Path(d) / "shot.json"
            write_json(V.shotlist, {"beats": old_beats, "scenes": scenes, "script_hash": "x"})
            edited = Script(SCRIPT.replace("Eight billion dollars", "Eight billion US dollars"))
            kept, changed = content.rebase_shotlist(V, edited, edited.beats(6, 16))
            self.assertEqual(changed, [])
            self.assertEqual(len(kept), len(scenes))
            rewritten = Script(SCRIPT.replace("Nobody asked where the money went.",
                                              "Nobody asked where the money went. Nobody checked. Nobody cared at all."))
            kept, changed = content.rebase_shotlist(V, rewritten, rewritten.beats(6, 16))
            self.assertEqual(changed, [2])
            self.assertTrue(all(s["part"] == 1 for s in kept))


class CardTests(unittest.TestCase):
    def test_count_text(self):
        self.assertEqual(count_text("$8,000,000,000", 0.5), "$4,000,000,000")
        self.assertEqual(count_text("1.2M", 0.5), "0.6M")
        self.assertEqual(count_text("97%", 1.0), "97%")
        self.assertEqual(count_text("no number", 0.3), "no number")


if __name__ == "__main__":
    unittest.main()
