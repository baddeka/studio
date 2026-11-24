import argparse
import re

import yaml

from .config import VIDEOS, Video, cfg, env
from .util import ensure_utf8_console, read_json, say, warn


def cmd_new(a):
    slug = re.sub(r"[^a-z0-9-]+", "-", a.slug.lower()).strip("-")
    d = VIDEOS / slug
    if d.exists():
        raise SystemExit(f"videos/{slug} already exists")
    for sub in ("assets/inbox", "assets/images", "assets/clips", "audio", "edit", "logs"):
        (d / sub).mkdir(parents=True)
    meta = {"topic": a.topic, "angle": a.angle or "", "notes": a.notes or "", "words": a.words or cfg()["script"]["words"]}
    (d / "video.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
    say(f"created videos/{slug}. Next: vp run {slug}")


def cmd_research(a):
    from .content import research
    research(Video(a.slug), force=a.force)


def cmd_script(a):
    from .content import script
    script(Video(a.slug), force=a.force)


def cmd_factcheck(a):
    from .content import factcheck
    factcheck(Video(a.slug), fix=a.fix)


def cmd_shotlist(a):
    from .content import shotlist
    shotlist(Video(a.slug), force=a.force, only_part=a.part)


def cmd_beats(a):
    from .beats import main as beats_main
    v = Video(a.slug)
    if not v.script.exists():
        raise SystemExit(f"No script yet: put it in videos/{v.slug}/02_script.md")
    return beats_main([str(v.script)] + (["--json"] if a.json else []))


def cmd_package(a):
    from .content import package
    package(Video(a.slug), force=a.force)


def cmd_voice(a):
    from .voice import make_voice
    make_voice(Video(a.slug), engine=a.engine, file=a.file, force=a.force, yes=a.yes)


def cmd_sample(a):
    from .voice import sample
    sample(Video(a.slug), a.paras, voices=a.voice, models=a.model, moods=a.mood, text=a.text,
           stability=a.stability, max_chars=a.max_chars, open_file=not a.no_open)


def cmd_paras(a):
    from .voice import list_paras
    list_paras(Video(a.slug))


def cmd_credits(a):
    from .voice import credits_line, subscription
    if not env("ELEVENLABS_API_KEY"):
        raise SystemExit("Set ELEVENLABS_API_KEY in .env first.")
    say(credits_line(subscription()))


def cmd_voices(a):
    from .voice import list_voices, sapi_voices
    if env("ELEVENLABS_API_KEY"):
        say("ElevenLabs voices (put the id in config.yaml -> voice.voice_id):")
        for vid, name, cat, labels in list_voices():
            say(f"  {vid}  {name}  [{cat}]  {labels}")
    else:
        say("No ELEVENLABS_API_KEY in .env, so only the free Windows voices are listed.")
    if env("ELEVENLABS_API_KEY"):
        from .voice import credits_line, subscription
        say(credits_line(subscription()))
    say("Windows draft voices (voice.sapi_voice):")
    for v in sapi_voices():
        say(f"  {v}")


def cmd_board(a):
    from .board import serve
    serve(Video(a.slug), port=a.port, open_browser=not a.no_browser)


def cmd_sort(a):
    from .assets import sort_inbox
    sort_inbox(Video(a.slug), by_order=a.by_order)


def cmd_cut(a):
    from .cut import build_cut
    build_cut(Video(a.slug), draft=a.draft, workers=a.workers, music=not a.no_music)


def cmd_music(a):
    from .music import build_bed, describe, folder_status
    video = Video(a.slug)
    say("Song folders (library/music):")
    for name, n in folder_status(video):
        say(f"  {name:<26} {n} song(s)" + ("   <- empty, add 2-3 songs (see MUSIC.md)" if n == 0 else ""))
    if not (video.words.exists() and video.shotlist.exists()):
        say("The music plan needs the voice-over and shot list first (vp run).")
        return 0
    from .timing import compute
    timing = compute(video)
    bed, secs = build_bed(video, timing, video.edit("music_bed.flac"))
    say("\nMusic plan (change it in videos\\%s\\video.yaml under music:)" % video.slug)
    for line in describe(secs):
        say("  " + line[2:])
    if bed:
        say(f"\nListen to the music alone: {bed}")
    return 0


def cmd_get_music(a):
    from .music import STARTER, folder_status, get_starter
    say(f"Downloading the starter songs ({len(STARTER)} free Scott Buckley tracks, CC BY 4.0) into library\\music ...")
    got, failed = get_starter()
    say(f"{got} downloaded" + (", the rest were already there." if not failed else "."))
    for mood, title, page, err in failed:
        warn(f"could not download {title} ({err}). Open {page}, click the MP3 download and put the file in "
             f"library\\music\\{mood}")
    say("Songs per folder: " + ", ".join(f"{n} {c}" for n, c in folder_status()))
    say("The credits for the songs a video uses are added to its description (05_package.md) automatically.")
    return 1 if failed else 0


def cmd_shorts(a):
    from .shorts import build_shorts
    only = [int(x) for x in a.only.split(",")] if a.only else None
    build_shorts(Video(a.slug), only=only, workers=a.workers)


def cmd_thumb(a):
    from .thumbnail import build_thumbnails
    build_thumbnails(Video(a.slug))


def cmd_timing(a):
    from .timing import compute
    t = compute(Video(a.slug))
    say(f"timing: {len(t['scenes'])} scenes, {t['duration'] / 60:.1f} min")


def missing_assets(video):
    from .cut import find_assets
    shot = read_json(video.shotlist, {})
    assets = find_assets(video)
    miss_img = [s["id"] for s in shot.get("scenes", []) if s["type"] in ("still", "motion") and "image" not in assets.get(s["id"].upper(), {})]
    miss_clip = [s["id"] for s in shot.get("scenes", []) if s["type"] == "motion" and "clip" not in assets.get(s["id"].upper(), {})]
    return miss_img, miss_clip


def cmd_run(a):
    from .content import factcheck, package, research, script, script_hash, shotlist
    v = Video(a.slug)
    steps = ["research", "script", "factcheck", "shotlist", "package", "voice", "cut"]
    stop = a.until or "cut"
    if stop not in steps:
        raise SystemExit(f"--until must be one of {', '.join(steps)}")
    last = steps.index(stop)
    say(f"== {v.slug}: running {', '.join(steps[:last + 1])}")
    research(v)
    if last >= 1:
        script(v)
    if last >= 2:
        if not v.factcheck.exists() or a.force_check:
            factcheck(v, fix=True)
        else:
            say("factcheck: already done")
    if last >= 3:
        shotlist(v)
    if last >= 4:
        pkg = read_json(v.package)
        # A package Claude made is redone when the script changes; a hand-made one is kept and only re-checked.
        stale = bool(pkg) and bool(pkg.get("script_hash")) and pkg["script_hash"] != script_hash(v)
        package(v, force=stale and pkg.get("made_by") != "hand")
    if last >= 5:
        from .voice import make_voice, voice_plan
        words = read_json(v.words)
        # re-voiced when the words, the delivery markup or the voice settings changed; only changed paragraphs cost credits
        if not words or words.get("script_hash") != script_hash(v) or \
                (words.get("engine") != "file" and words.get("plan") != voice_plan(v)):
            make_voice(v)
        else:
            say(f"voice: already done ({words['engine']})")
    if last >= 6:
        from .cut import build_cut
        miss_img, miss_clip = missing_assets(v)
        if miss_img or miss_clip:
            say(f"{len(miss_img)} images and {len(miss_clip)} Kling clips are still missing; making a quick draft cut "
                "with placeholders so you can check the pacing.")
            build_cut(v, draft=True)
            say(f"Next: vp board {v.slug}  (add the images and clips), then run this again.")
            return
        build_cut(v)
        from .shorts import build_shorts
        from .thumbnail import build_thumbnails
        build_shorts(v)
        build_thumbnails(v)
        say(f"Done. Review edit/roughcut.mp4, edit/shorts/, edit/thumb_*.png and 05_package.md in videos/{v.slug}")


def cmd_status(a):
    from .content import script_hash
    from .textutil import Script
    v = Video(a.slug)
    cur = script_hash(v)
    rows = []

    def row(name, ok, info):
        rows.append(f"  {'[x]' if ok else '[ ]'} {name:<10} {info}")

    src = read_json(v.sources, [])
    dead = sum(1 for s in src if s.get("status") in ("broken", "unreachable"))
    row("research", v.research.exists(), f"{len(src)} sources, {dead} dead links" if src else "")
    if v.script.exists():
        s = Script(v.script.read_text(encoding="utf-8"))
        row("script", True, f"{s.word_count} words, {len(s.parts)} parts, ~{s.word_count / cfg()['script']['wpm']:.0f} min")
    else:
        row("script", False, "")
    fc = read_json(v.p("logs", "factcheck.json"))
    row("factcheck", v.factcheck.exists(),
        f"{sum(1 for c in fc['claims'] if c['verdict'] != 'supported')} flagged of {len(fc['claims'])}" if fc else "")
    shot = read_json(v.shotlist)
    if shot:
        if not shot.get("script_hash"):
            stale = " (written by hand; checked on the next vp run)"
        else:
            stale = (" (STALE: script changed)" if shot["script_hash"] != cur else "") + \
                (" (written by hand)" if shot.get("made_by") == "hand" else "")
        types = {}
        for s in shot["scenes"]:
            types[s["type"]] = types.get(s["type"], 0) + 1
        row("shotlist", True, f"{len(shot['scenes'])} scenes {types}{stale}")
    else:
        row("shotlist", False, "")
    pkg = read_json(v.package)
    info = ""
    if pkg:
        info = f"{len(pkg.get('shorts') or [])} shorts"
        if pkg.get("made_by") == "hand" or not pkg.get("script_hash"):
            info += " (written by hand)"
    row("package", bool(pkg), info)
    words = read_json(v.words)
    if words:
        stale = " (STALE: script changed)" if words["script_hash"] != cur else ""
        row("voice", True, f"{words['duration'] / 60:.1f} min, {words['engine']}{stale}")
    else:
        row("voice", False, "")
    if shot:
        mi, mc = missing_assets(v)
        n_img = sum(1 for s in shot["scenes"] if s["type"] in ("still", "motion"))
        n_clip = sum(1 for s in shot["scenes"] if s["type"] == "motion")
        row("assets", not mi and not mc, f"images {n_img - len(mi)}/{n_img}, clips {n_clip - len(mc)}/{n_clip}")
    rc = v.dir / "edit" / "roughcut.mp4"
    row("cut", rc.exists(), "edit/roughcut.mp4" if rc.exists() else ("draft only" if (v.dir / "edit" / "draft.mp4").exists() else ""))
    shorts = sorted((v.dir / "edit" / "shorts").glob("short_*.mp4")) if (v.dir / "edit" / "shorts").exists() else []
    row("shorts", bool(shorts), f"{len(shorts)} made" if shorts else "")
    say(f"{v.slug}: {v.meta.get('topic', '')}")
    say("\n".join(rows))


def cmd_clean(a):
    import shutil
    v = Video(a.slug)
    edit = v.dir / "edit"
    # Drafts and Shorts caches are not needed once the final cut exists. The 1080p segments are what
    # timeline.fcpxml points at, so they go only with --all.
    targets = [edit / "segments_draft", edit / "segments_v", edit / "draft.mp4"]
    if a.all:
        targets += [edit / "segments", edit / "overlays"]
    freed = 0
    for t in targets:
        if t.is_dir():
            freed += sum(f.stat().st_size for f in t.rglob("*") if f.is_file())
            shutil.rmtree(t, ignore_errors=True)
        elif t.exists():
            freed += t.stat().st_size
            t.unlink()
    say(f"clean: freed {freed / 1e6:.0f} MB in videos/{v.slug}/edit"
        + (". The DaVinci timeline needs a new 'vp cut' before it opens again." if a.all else
           " (kept the 1080p segments that timeline.fcpxml uses; --all removes them too)"))


def cmd_cost(a):
    from .costs import report
    say(report(a.slug))


def cmd_doctor(a):
    from .selftest import Report, check_tools
    from .llm import find_cli, pick_engine
    r = Report()
    say("Tools")
    ok = check_tools(r)
    say("Services")
    say(f"  Claude: engine {cfg()['llm']['engine']} -> {pick_engine()}, model {cfg()['llm']['model']}, "
        f"API key {'set' if env('ANTHROPIC_API_KEY') else 'not set'}, CLI {find_cli() or 'not found'}")
    say(f"  ElevenLabs key: {'set' if env('ELEVENLABS_API_KEY') else 'not set (free Windows voice will be used)'}")
    from .music import folder_status
    say("  music: " + ", ".join(f"{name} {n}" for name, n in folder_status()))
    say("All tools found. Run 'vp selftest' to render a test video." if ok else "Some tools are missing; see above.")
    return 0 if ok else 1


def cmd_selftest(a):
    from .selftest import selftest
    return selftest(keep=a.keep, workers=a.workers)


def build_parser():
    p = argparse.ArgumentParser(prog="vp", description="Faceless documentary video pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text, slug=True):
        sp = sub.add_parser(name, help=help_text)
        if slug:
            sp.add_argument("slug")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("new", cmd_new, "start a new video folder")
    sp.add_argument("topic")
    sp.add_argument("--angle")
    sp.add_argument("--notes")
    sp.add_argument("--words", type=int)
    add("research", cmd_research, "research dossier with sources (Claude + web search)").add_argument("--force", action="store_true")
    add("script", cmd_script, "7-part narration script").add_argument("--force", action="store_true")
    add("factcheck", cmd_factcheck, "check the script against the research").add_argument("--fix", action="store_true")
    sp = add("shotlist", cmd_shotlist, "scenes with Midjourney and Kling prompts")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--part", type=int)
    add("package", cmd_package, "titles, thumbnail text, description, tags, Shorts moments").add_argument("--force", action="store_true")
    add("beats", cmd_beats, "the script's numbered beats, for writing a shot list or Shorts by hand").add_argument(
        "--json", action="store_true", help="print them as JSON for 04_shotlist.json")
    sp = add("voice", cmd_voice, "voice-over with word timings")
    sp.add_argument("--engine", choices=["elevenlabs", "sapi", "file"])
    sp.add_argument("--file", help="use your own recording (mp3/wav)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--yes", action="store_true", help="do not ask before spending credits")
    sp = add("sample", cmd_sample, "voice a few paragraphs to try a voice, model or mood cheaply")
    sp.add_argument("paras", nargs="*", help="paragraph numbers from vp paras, e.g. 1 12 40 or 3-5")
    sp.add_argument("--voice", help="voice id(s), comma-separated to compare")
    sp.add_argument("--model", help="model(s), e.g. eleven_v4,eleven_multilingual_v2")
    sp.add_argument("--mood", help="force a mood (or 'none'); comma-separated to compare")
    sp.add_argument("--stability", type=float)
    sp.add_argument("--text", help="voice this line instead of script paragraphs (markup allowed)")
    sp.add_argument("--max-chars", type=int, default=4000)
    sp.add_argument("--no-open", action="store_true")
    add("paras", cmd_paras, "numbered narration paragraphs with their moods (free)")
    add("credits", cmd_credits, "ElevenLabs credits left this month", slug=False)
    add("voices", cmd_voices, "list available voices", slug=False)
    sp = add("board", cmd_board, "open the scene board to add images and clips")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--no-browser", action="store_true")
    add("sort", cmd_sort, "move files from assets/inbox to their scenes").add_argument("--by-order", action="store_true")
    sp = add("cut", cmd_cut, "render the rough cut, captions and DaVinci timeline")
    sp.add_argument("--draft", action="store_true")
    sp.add_argument("--no-music", action="store_true")
    sp.add_argument("--workers", type=int)
    add("get-music", cmd_get_music, "download the free starter songs into the mood folders", slug=False)
    add("music", cmd_music, "show which music mood plays where, and make the music bed to listen to")
    sp = add("shorts", cmd_shorts, "render the vertical Shorts")
    sp.add_argument("--only", help="comma-separated short numbers, e.g. 1,3")
    sp.add_argument("--workers", type=int)
    add("thumb", cmd_thumb, "thumbnail drafts")
    add("timing", cmd_timing, "recompute scene timings")
    sp = add("run", cmd_run, "run every automatic step in order")
    sp.add_argument("--until", help="stop after this step")
    sp.add_argument("--force-check", action="store_true")
    add("status", cmd_status, "what is done and what is missing")
    add("clean", cmd_clean, "delete render caches of a finished video to free disk space").add_argument(
        "--all", action="store_true", help="also the 1080p segments (the DaVinci timeline needs them)")
    sp = add("cost", cmd_cost, "spend per video and per month", slug=False)
    sp.add_argument("slug", nargs="?")
    add("doctor", cmd_doctor, "check tools, fonts and keys", slug=False)
    sp = add("selftest", cmd_selftest, "render a small test video offline and check every step", slug=False)
    sp.add_argument("--keep", action="store_true", help="keep videos/_selftest afterwards")
    sp.add_argument("--workers", type=int)
    return p


def main(argv=None):
    ensure_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        if args.fn in (cmd_board, cmd_sort, cmd_cut, cmd_shorts, cmd_thumb, cmd_timing, cmd_music):
            from .content import adopt_handmade
            adopt_handmade(Video(args.slug))
        return args.fn(args) or 0
    except KeyboardInterrupt:
        warn("stopped")
        return 130
    except Exception as e:
        from .llm import LLMError
        if isinstance(e, (LLMError, RuntimeError)):
            warn(str(e))
            return 1
        raise
