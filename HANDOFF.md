# Handoff: cloud build of 2026-11-23

The laptop build (see "Laptop build" below) was finished and tested in the cloud, then packaged with a one-command Windows setup. Start from this code; do not rewrite it.

## Install

Double-click `setup.cmd` (INSTALL.md). It makes `.venv`, installs `requirements.txt`, checks or downloads ffmpeg and the fonts, creates `.env`, then runs `vp doctor`, the unit tests and `vp selftest`.

## Tested in the cloud (Linux, Python 3.14, ffmpeg 6.1)

- `vp selftest`: makes a 40-second video offline (synthetic voice with exact character timings, all scene kinds, stand-in images and a clip, music), renders draft, full cut and one Short, and checks 33 things: formats, lengths, loudness, captions, FCPXML, placeholders, thumbnails. Its shot list and package are written the way hand-made ones arrive, so it also checks that they are taken over. About 75 s on 4 cores.
- `setup.ps1` ran start to finish under PowerShell 7.7 on a clean copy (fonts deleted on purpose): venv, packages, font download, `.env`, doctor, tests, selftest. Not exercised here: the gyan.dev ffmpeg download (blocked from the cloud) and winget. The script avoids PowerShell 7-only syntax and is ASCII, so Windows PowerShell 5.1 reads it.
- End to end on `videos/ponzi-test` (3.1 min, 36 scenes, real Claude outputs from the laptop, SAPI voice): `vp run` with one image missing makes the draft with a placeholder; `vp sort` placed a Midjourney-named download by prompt match; the scene board page uploaded an image through its own JS; `vp run` then rendered the 1080p cut, 4 Shorts and 3 thumbnails in 5.5 min on 4 cores; a second `vp cut` re-used every segment (31 s).
- Audio: -14.2 LUFS integrated, -1.7 dBTP peak, voice and picture in sync (0 ms by cross-correlation), music ducks under the voice.
- `timeline.fcpxml` (version 1.8, file URLs with spaces and `&` escaped) parsed by OpenTimelineIO and valid against Apple's DTD. Importing it into DaVinci Resolve itself was not possible in the cloud.
- `vp voice --file` with an mp3 and no ElevenLabs key: word starts within 0.17 s on average of the real timing (sentence ends pinned to pauses, numbers weighted by how long they take to say). With a key, ElevenLabs forced alignment is used as before.
- 28 unit tests pass (`python -m unittest discover -s tests`). The mock ElevenLabs test no longer depends on request order.
- Hand-made files (tests/test_handmade.py): `vp run --until package` on a folder with hand-written research, script, fact-check, shot list and package takes all of them over without calling Claude (the test replaces Claude with a function that fails). After a small script edit, both files are still kept.

## What changed from the laptop build

- render: worker processes use "spawn" everywhere (same as Windows); luma-only film grain and a capped CRF (8 Mbps, YouTube's 1080p30 figure) instead of 17-20 Mbps files; gentle voice compression, sidechain ducking, 6 dB mix headroom, then one constant gain plus a true-peak limiter measured in a loop to reach -14 LUFS without pumping. Segment cache version 5.
- cut: FCPXML 1.8 (1.9+ needs media-rep), voice, music and effects on their own lanes; temp files removed; music bed and effects stored as FLAC.
- cards: feathered shades behind chapter titles and lower thirds; in Shorts, card content is fitted between the hook line and the captions; draft placeholders sit below the chapter title.
- Shorts captions: larger Anton with outline, groups of up to 18 characters, no line overflow.
- board: POSTs need the `X-VP-Board` header and every request must use a local host name, so other web pages cannot change files.
- New commands: `vp selftest`, `vp clean <slug> [--all]`, `vp beats <slug> [--json]`; `vp doctor` checks ffmpeg's encoders and filters (libx264, aac, ass, loudnorm, alimiter, ...).
- Hand-made files (FORMAT.md): a shot list or package without `script_hash` is checked against the script and taken over (`content.adopt_shotlist`, `adopt_package`). Missing `mj_full`, `parts`, ids and cameras are filled in. Problems stop the run with a list, and the file is never redone by Claude. A hand-written dossier gets its `sources.json` and link check. A hand-made package is kept when the script changes, and the Shorts ranges are re-fitted with a warning. `python -m vidpipe.beats` numbers a script's beats with nothing but Python, for writing these files outside the laptop.
- Windows: `setup.cmd` + `setup.ps1`, `requirements.txt`, `vp.cmd` uses `.venv` first, then `py -3`, then `python`.

## Not tested anywhere yet

- Importing `timeline.fcpxml` into DaVinci Resolve 20.
- ElevenLabs with a real key (the mock server test covers the request format and caching).
- A full 3,500-word video. Rendering is about 0.7x real time on 4 cloud cores; the laptop's i5-8250U will likely be slower.

## Decisions to keep

- Scene ids are per part (`S3-07`) so redoing one part never renames another part's images. A script edit keeps every part whose beats barely changed (`content.rebase_shotlist`); changed parts are regenerated and their old images move to `assets/_old`.
- Beats are made in code (sentences, long ones split at punctuation, 6-16 words); Claude groups 1-2 beats per scene and never has to copy text back, so narration never drifts.
- Claude engine `auto`: ANTHROPIC_API_KEY if set, else the local Claude Code CLI with `--safe-mode`, `--tools`, `--json-schema`, `--system-prompt`, `--no-session-persistence` and a scrubbed environment (a parent Claude Code session exports CLAUDE_CODE_* variables, including CLAUDE_CODE_SIMPLE, which break a nested login). The API path uses `output_config` (effort and JSON schema), web search and fetch `_20260209`, and server-side fallbacks.
- ElevenLabs: one `with-timestamps` request per paragraph with previous/next text, cached by a hash of text and voice settings, pauses inserted by us (0.35 s paragraph, 0.9 s part). Default voice George `JBFqnCBsd6RMkjVDRZzb`, model `eleven_multilingual_v2`.
- Ken Burns is done in Python (Pillow `resize` with a float `box`) and piped to ffmpeg, 3 worker processes. OpenCV was measured at only 1.6x faster, not worth the extra dependency.
- Style: bg #0B0B0C, glow #2a2112, gold #E8B54A, red #E5484D, text #F2EFE8; fonts Anton, Inter (variable), IBM Plex Mono, all OFL, in `library/fonts`.

## Windows-only pieces

`setup.cmd`, `setup.ps1`, `vp.cmd`, `vidpipe/sapi_tts.ps1`, the `tools/ffmpeg/bin/*.exe` lookup in `config.ffmpeg_bin` (falls back to PATH), Windows font fallbacks in `config.font_path`. `__main__.py` has the `if __name__ == "__main__"` guard that multiprocessing needs.

## Laptop build (earlier the same day)

- Ran for real on DiasBook1 (Windows 11, Python 3.14.5): Claude steps for `videos/ponzi-test` through the CLI engine (research 26 sources, script, fact-check, visual bible, 36-scene shot list, package with 4 Shorts; about $3.40 at API prices), the SAPI draft voice, the scene board API, cards and thumbnails.
- ffmpeg was never run on the laptop: the agent's safety check refused to run the downloaded binary. ffmpeg 9.1 essentials (gyan.dev build, zip sha256 `b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd`) is in `tools\ffmpeg\bin`; `setup.cmd` runs it when you start it yourself.
- Code lives in `C:\Users\mi\Desktop\automatization of videos`. winget there is v1.2 and crashes on `source update`; setup only uses it when no Python is found.
- 8 GB RAM, i5-8250U, MX150 2 GB: keep render workers at 3 or less; DaVinci Resolve will be slow.
