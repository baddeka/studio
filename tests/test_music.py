"""Music by mood: which folder plays where (no audio rendering; vp selftest renders a two-mood bed)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vidpipe import music


def _song(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "music").mkdir(parents=True)
    monkeypatch.setattr(music, "LIBRARY", root)
    music._warned.clear()
    return root / "music"


def _video(tmp_path, meta=None, script=None, scenes_extra=None):
    d = tmp_path / "videos" / "v"
    d.mkdir(parents=True, exist_ok=True)
    sp = d / "02_script.md"
    if script:
        sp.write_text(script, encoding="utf-8")
    shot = {"scenes": [dict(id=f"S{p}-0{k}", part=p, **(scenes_extra or {}).get(f"S{p}-0{k}", {}))
                       for p in (1, 2, 3) for k in (1, 2)]}
    (d / "04_shotlist.json").write_text(json.dumps(shot), encoding="utf-8")
    return SimpleNamespace(dir=d, meta=meta or {}, script=sp, shotlist=d / "04_shotlist.json")


def _timing(seconds_per_scene=20.0):
    scenes = []
    t = 0.0
    for p in (1, 2, 3):
        for k in (1, 2):
            scenes.append({"id": f"S{p}-0{k}", "part": p, "start": t, "end": t + seconds_per_scene})
            t += seconds_per_scene
    return {"scenes": scenes, "duration": t}


def test_only_loose_songs_make_one_section(tmp_path, lib):
    _song(lib / "a.mp3")
    _song(lib / "b.mp3")
    secs = music.plan(_video(tmp_path), _timing())
    assert [(s["folder"], s["start"], s["end"]) for s in secs] == [("", 0.0, 120.0)]


def test_parts_and_scene_ids_pick_folders(tmp_path, lib):
    for m in ("tense", "calm", "heavy"):
        _song(lib / m / f"{m}.mp3")
    v = _video(tmp_path, meta={"music": {1: "tense", "part 2": "calm", "S2-02": "Heavy", 3: "none"}})
    secs = music.plan(v, _timing())
    assert [(s["mood"], s["folder"], s["start"], s["end"]) for s in secs] == [
        ("tense", "tense", 0.0, 40.0), ("calm", "calm", 40.0, 60.0), ("heavy", "heavy", 60.0, 80.0),
        ("none", None, 80.0, 120.0)]


def test_shot_list_scene_field_wins(tmp_path, lib):
    for m in ("tense", "calm"):
        _song(lib / m / f"{m}.mp3")
    v = _video(tmp_path, meta={"music": {1: "calm", 2: "calm", 3: "calm"}}, scenes_extra={"S3-01": {"music": "tense"}})
    secs = music.plan(v, _timing())
    assert [(s["mood"], s["start"]) for s in secs] == [("calm", 0.0), ("tense", 80.0)]
    assert secs[-1]["source"] == "shot list"


def test_empty_mood_borrows_a_related_folder(tmp_path, lib):
    _song(lib / "tense" / "t.mp3")
    (lib / "heavy").mkdir()
    v = _video(tmp_path, meta={"music": {1: "heavy", 2: "heavy", 3: "heavy"}})
    secs = music.plan(v, _timing())
    assert [(s["mood"], s["folder"]) for s in secs] == [("heavy", "tense")]


def test_short_sections_keep_the_music_before(tmp_path, lib):
    for m in ("tense", "calm"):
        _song(lib / m / f"{m}.mp3")
    v = _video(tmp_path, meta={"music": {1: "tense", "S1-02": "calm", 2: "tense", 3: "tense"}})
    secs = music.plan(v, _timing(seconds_per_scene=5.0))
    assert [s["folder"] for s in secs] == ["tense"]


def test_voice_moods_choose_when_video_yaml_is_silent(tmp_path, lib):
    for m in ("tense", "calm", "heavy"):
        _song(lib / m / f"{m}.mp3")
    script = ("# T\n\n## Part 1: A\n\n{tense} One two three four five.\n\n{warm} Six.\n\n"
              "## Part 2: B\n\n{warm} One two three.\n\n## Part 3: C\n\n{grave} One two three.\n")
    secs = music.plan(_video(tmp_path, script=script), _timing())
    assert [s["mood"] for s in secs] == ["tense", "calm", "heavy"]


def test_video_music_folder_wins_over_library(tmp_path, lib):
    _song(lib / "calm" / "lib.mp3")
    v = _video(tmp_path)
    _song(v.dir / "music" / "calm" / "own.mp3")
    assert [p.name for p in music.pools(v)["calm"]] == ["own.mp3"]


def test_starter_songs_get_credits(lib):
    assert music.credit_for("sb_intervention.mp3") == \
        '"Intervention" by Scott Buckley - released under CC-BY 4.0. www.scottbuckley.com.au'
    (lib / "CREDITS.txt").write_text("my song.mp3 | My Song by Someone (CC BY)\n", encoding="utf-8")
    secs = [{"songs": ["sb_intervention.mp3", "my song.mp3", "unknown.mp3"]}, {"songs": ["sb_intervention.mp3"]}]
    assert len(music.credits(secs)) == 2


def test_description_lists_music_credits():
    from vidpipe.content import describe
    data = {"description": "About FTX.", "hashtags": []}
    text, _ = describe(data, None, [], ["\"Solace\" by Scott Buckley - released under CC-BY 4.0. www.scottbuckley.com.au"])
    assert "Music\n\"Solace\" by Scott Buckley" in text
