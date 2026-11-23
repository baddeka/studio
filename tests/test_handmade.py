"""Files written by hand (by a person or another Claude) are checked and taken over, never sent to Claude again."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidpipe import beats as beats_cli
from vidpipe import cli, config, content
from vidpipe.selftest import SCRIPT
from vidpipe.textutil import Script
from vidpipe.util import read_json, write_json

RESEARCH = """# Dossier

The fund stopped withdrawals [1].

## Sources
[1] Fund halts withdrawals - Example News - 2024-03-04 - https://example.com/a
[2] Regulator notice - Example Regulator - 2024-03-05 - https://example.com/b
"""


def no_claude(*a, **k):
    raise AssertionError("a hand-made file was sent to Claude")


def quiet(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        result = fn(*args)
    return result, out.getvalue() + err.getvalue()


class HandmadeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.VIDEOS, content.complete, content.check_links)
        config.VIDEOS = Path(self.tmp.name)
        content.complete = no_claude
        content.check_links = lambda sources, seen=(): [s.update(status="ok") for s in sources]
        (config.VIDEOS / "hand").mkdir()
        self.v = config.Video("hand")
        self.v.script.write_text(SCRIPT, encoding="utf-8")
        self.beats = Script(SCRIPT).beats(6, 16)

    def tearDown(self):
        config.VIDEOS, content.complete, content.check_links = self.saved
        self.tmp.cleanup()

    def scenes(self):
        """One scene per beat, written the way a person would: some ids missing, flat card fields, a numbered reuse."""
        return [
            {"id": "S1-01", "beats": [1], "type": "still", "mj_prompt": "a dark trading floor at night.", "camera": "push_in"},
            {"id": "s1-2", "beats": [2], "type": "card", "card_kind": "stat", "card_text": "12,000",
             "card_sub": "people locked out", "card_tone": "loss"},
            {"beats": [3], "type": "reuse", "reuse_of": 1, "camera": "zoom"},
            {"id": "S2-01", "part": 2, "beats": [4, 5], "type": "motion", "mj_prompt": "a man at a podium",
             "kling_prompt": "slow push in, flashes from cameras", "onscreen": "Zurich - 2019"},
            {"id": "S2-02", "beats": [6], "type": "card",
             "card": {"kind": "compare", "text": "Every quarter", "items": ["Promised: audited", "Actual: never"]}},
            {"id": "S3-01", "beats": [7, 8], "type": "still", "mj_prompt": "a reporter in a dim newsroom"},
            {"id": "S3-02", "beats": [9], "type": "still", "mj_prompt": "an empty office, chairs on desks",
             "mj_full": "an empty office, chairs on desks, my own style --ar 16:9"},
        ]

    def test_shot_list_is_checked_and_completed(self):
        write_json(self.v.shotlist, {"beats": self.beats, "scenes": self.scenes()})
        _, log = quiet(content.shotlist, self.v)
        shot = read_json(self.v.shotlist)
        self.assertIn("took the hand-made shot list", log)
        self.assertEqual(shot["script_hash"], content.script_hash(self.v))
        self.assertEqual(shot["made_by"], "hand")
        self.assertEqual([p["n"] for p in shot["parts"]], [1, 2, 3])
        by_id = {s["id"]: s for s in shot["scenes"]}
        self.assertEqual(list(by_id), ["S1-01", "S1-02", "S1-03", "S2-01", "S2-02", "S3-01", "S3-02"])
        self.assertEqual(by_id["S1-01"]["mj_prompt"], "a dark trading floor at night")
        self.assertTrue(by_id["S1-01"]["mj_full"].endswith("--ar 16:9 --style raw"))
        self.assertEqual(by_id["S3-02"]["mj_full"], "an empty office, chairs on desks, my own style --ar 16:9")
        self.assertEqual(by_id["S1-02"]["card"], {"kind": "stat", "text": "12,000", "sub": "people locked out",
                                                  "items": [], "tone": "loss"})
        self.assertEqual(by_id["S1-03"]["reuse_of"], "S1-01")
        self.assertIn(by_id["S1-03"]["camera"], content.CAMERAS)
        self.assertIn('unknown camera "zoom"', log)
        self.assertTrue(self.v.prompts_md.exists())
        _, log = quiet(content.shotlist, self.v)
        self.assertIn("already done", log)

    def test_beats_may_be_left_out(self):
        write_json(self.v.shotlist, {"scenes": self.scenes()})
        quiet(content.shotlist, self.v)
        self.assertEqual(read_json(self.v.shotlist)["beats"], self.beats)

    def test_problems_are_listed_and_nothing_is_changed(self):
        scenes = self.scenes()
        scenes[1]["beats"] = [1]                 # B1 twice, B2 in no scene
        scenes[3].pop("kling_prompt")
        scenes[4]["card"]["kind"] = "chart"
        scenes[6]["type"] = "drawing"
        before = {"beats": self.beats, "scenes": scenes}
        write_json(self.v.shotlist, before)
        with self.assertRaises(SystemExit) as e:
            quiet(content.shotlist, self.v)
        msg = str(e.exception.code)
        for part in ("5 problems", "kling_prompt", "card kind", "type must be",
                     "in no scene: B2", "more than one scene: B1"):
            self.assertIn(part, msg)
        self.assertEqual(read_json(self.v.shotlist), before)

    def test_beats_of_another_script_version_are_refused(self):
        other = Script(SCRIPT.replace("Who was the auditor?", "Who, exactly, was the auditor of the fund?")).beats(6, 16)
        write_json(self.v.shotlist, {"beats": other, "scenes": self.scenes()})
        with self.assertRaises(SystemExit) as e:
            quiet(content.shotlist, self.v)
        self.assertIn("stop matching", str(e.exception.code))
        self.assertIn("B8", str(e.exception.code))

    def test_vp_run_takes_every_hand_made_file_without_claude(self):
        self.v.research.write_text(RESEARCH, encoding="utf-8")
        self.v.factcheck.write_text("All claims checked by hand.\n", encoding="utf-8")
        write_json(self.v.shotlist, {"beats": self.beats, "scenes": self.scenes()})
        write_json(self.v.package, {
            "titles": ["The fund that never was"], "thumbnail_texts": ["$400M GONE"],
            "thumbnail_prompt": "a single empty chair under a spotlight", "description": "How a fund vanished.",
            "tags": ["ponzi"], "hashtags": ["#scam"], "pinned_comment": "Would you have spotted it?",
            "shorts": [{"title": "Gone", "hook": "$400 million, gone", "beat_from": 1, "beat_to": 3, "why": "cold open"}]})
        rc, log = quiet(cli.main, ["run", "hand", "--until", "package"])
        self.assertEqual(rc, 0)
        self.assertEqual([s["n"] for s in read_json(self.v.sources)], [1, 2])
        pkg = read_json(self.v.package)
        self.assertEqual(pkg["made_by"], "hand")
        self.assertEqual(pkg["script_hash"], content.script_hash(self.v))
        self.assertTrue(pkg["thumbnail_full"].startswith("a single empty chair under a spotlight, cinematic"))
        self.assertGreaterEqual(sum(b["w1"] - b["w0"] for b in self.beats
                                    if pkg["shorts"][0]["beat_from"] <= b["id"] <= pkg["shorts"][0]["beat_to"]), 60)
        self.assertIn("Sources", self.v.package_md.read_text(encoding="utf-8"))
        # A small script edit later keeps both hand-made files; nothing is sent to Claude.
        self.v.script.write_text(SCRIPT.replace("cold Monday", "cold, grey Monday"), encoding="utf-8")
        rc, log = quiet(cli.main, ["run", "hand", "--until", "package"])
        self.assertEqual(rc, 0)
        self.assertIn("kept every scene", log)
        self.assertIn("check the Shorts beat ranges", log)
        self.assertEqual(read_json(self.v.package)["titles"], ["The fund that never was"])
        self.assertEqual(read_json(self.v.shotlist)["script_hash"], content.script_hash(self.v))

    def test_status_shows_hand_made_files(self):
        write_json(self.v.shotlist, {"beats": self.beats, "scenes": self.scenes()})
        _, log = quiet(cli.main, ["status", "hand"])
        self.assertIn("written by hand; checked on the next vp run", log)

    def test_beats_helper_matches_the_pipeline(self):
        out, _ = io.StringIO(), None
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            beats_cli.main([str(self.v.script), "--json"])
        self.assertEqual(json.loads(out.getvalue()), self.beats)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            beats_cli.main([str(self.v.script)])
        self.assertIn("B8 (4 words): Who was the auditor?", out.getvalue())
        self.assertIn("## Part 3:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
