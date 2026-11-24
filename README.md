# Video pipeline

Turns a topic into a finished documentary video: research with sources, a 7-part script, a fact-check, a shot list with Midjourney and Kling prompts, titles and description, the voice-over, a rendered rough cut with music and sound effects, captions, a DaVinci Resolve timeline, vertical Shorts and thumbnail drafts.

The only manual part is making the images in Midjourney and the moving clips in Kling, because Midjourney has no API. The scene board makes that copy, generate, drop.

## One-time setup

Double-click `setup.cmd`. It sets up Python, ffmpeg and the fonts, then renders a test video to prove everything works. INSTALL.md has the details. After that:

1. Open `.env` in Notepad and paste your ElevenLabs API key (ElevenLabs: Profile, then API keys). Without it the pipeline uses a free robotic Windows voice, which is only good for checking timing.
2. Optional: an Anthropic API key in `.env`. Without it the Claude steps run through the Claude app that is logged in on this computer.
3. Pick a narrator: `.\vp voices` lists your ElevenLabs voices. Put the id in `config.yaml` under `voice: voice_id:`. Empty means George, a warm British narrator. How to direct the delivery (moods, pauses, stress) and try it cheaply is in VOICE.md.
4. Put a few dark cinematic music tracks (YouTube Audio Library is free) in `library\music`. They are chained and ducked under the voice automatically.
5. When you find a Midjourney look you like, put its style reference code in `config.yaml` under `midjourney: sref:` so every image matches.

All commands run from this folder in PowerShell, as `.\vp <command>` (in Command Prompt, just `vp <command>`).

## Making a video

```powershell
.\vp new ftx "How FTX lost 8 billion dollars of customer money" --angle "told as a heist that hid in plain sight"
.\vp run ftx
```

`run` does every automatic step in order and skips what is already done:

| Step | Output in `videos\ftx` | What to check |
|---|---|---|
| research | `01_research.md`, `sources.json` | dead links are listed at the end of the step |
| script | `02_script.md` | edit it freely; keep the `## Part N: Title` headings |
| factcheck | `03_factcheck.md` | flagged claims are fixed in the script automatically (old version kept in `_old`) |
| shotlist | `04_shotlist.json`, `04_prompts.md` | one scene every 4-6 seconds |
| package | `05_package.md` | titles, thumbnail texts, description with chapters and sources, tags, Shorts |
| voice | `audio\voice.wav`, `audio\words.json` | listen once; every word is timed |
| cut | `edit\draft.mp4` | a quick low-resolution cut with grey placeholders for missing images |

Files written by hand, for example by Claude in the project chat, go in the same folder: `01_research.md`, `02_script.md`, `03_factcheck.md`, `04_shotlist.json`, `05_package.json`. `run` checks them against the script and takes them over without sending them to Claude again. FORMAT.md describes each file. Then make the images:

```powershell
.\vp board ftx
```

The board opens in the browser. For each scene: click Copy, paste into Midjourney, download the image you like and drop it on the scene, or copy the image in the browser and press Ctrl+V on the scene. Motion scenes also need a Kling clip: give Kling the same image and the Kling prompt, then drop the mp4. With the checkbox on, the board jumps to the next missing scene and copies its prompt for you.

If you prefer, save files with the scene id as the name (`S3-07.png`, `S3-07.mp4`) into `assets\inbox` and run `.\vp sort ftx`. Midjourney downloads keep the prompt in the file name, so most images are matched even without renaming.

When the board says everything is filled:

```powershell
.\vp run ftx
```

This renders `edit\roughcut.mp4` (1080p, music, sound effects, chapter titles, loudness set to -14 LUFS like YouTube), `edit\captions.srt`, `edit\timeline.fcpxml`, the Shorts in `edit\shorts` (word-by-word captions, cards kept clear of them) and three thumbnail drafts `edit\thumb_1.png`..`thumb_3.png`. Only scenes that changed are re-rendered next time.

Rendering speed: in testing, 4 processor cores cut a 3-minute video in about 2 minutes and its four Shorts in 3 more. Expect a 23-minute video to take roughly half an hour on the laptop. When a video is published, `.\vp clean <slug>` frees the space its render caches use.

## Upload checklist

- Video: `edit\roughcut.mp4`, or your DaVinci export.
- Title, description and tags: `05_package.md`. Chapters and sources are already in the description.
- Subtitles: upload `edit\captions.srt` in YouTube Studio.
- Thumbnail: pick one of `edit\thumb_*.png`, or make a better background with the Midjourney thumbnail prompt in `05_package.md`, save it as `assets\thumb.png` and run `.\vp thumb ftx`.
- Shorts: `edit\shorts\short_01.mp4` and the rest, titles in `05_package.md`.
- If any image shows a realistic real person, tick "altered or synthetic content" in YouTube Studio.

## Polishing in DaVinci Resolve (optional)

File, Import, Timeline, then pick `edit\timeline.fcpxml`. Every scene is its own clip on the timeline, with the voice and the music on separate audio tracks, so you can swap shots, trim or grade. Import `captions.srt` with File, Import, Subtitle. The laptop has 8 GB of RAM, so Resolve will be slow; the rough cut is made to be uploadable as it is.

## Commands

| Command | What it does |
|---|---|
| `.\vp new <slug> "<topic>" [--angle ..] [--notes ..] [--words 3500]` | new video folder |
| `.\vp run <slug> [--until step]` | every automatic step in order |
| `.\vp research / script / factcheck --fix / shotlist / package <slug>` | one step; add `--force` to redo it |
| `.\vp shotlist <slug> --part 4` | redo the scenes of one part only; other parts keep their images |
| `.\vp beats <slug> [--json]` | the script's numbered beats, for writing a shot list or Shorts by hand (FORMAT.md) |
| `.\vp voice <slug> [--engine sapi] [--file my.mp3]` | voice-over; `--file` times your own recording (exactly with an ElevenLabs key, closely from its pauses without one) |
| `.\vp paras <slug>` | the narration paragraphs, numbered, with their moods and the credits a full voice-over needs (free) |
| `.\vp sample <slug> [1 12 40] [--model a,b] [--voice id,id] [--mood tense,grave] [--text "..."]` | voice just those paragraphs (or one line) to judge a voice, model or mood for a few hundred credits; files go to `audio\samples` |
| `.\vp credits` | ElevenLabs credits left this month |
| `.\vp board <slug>` / `.\vp sort <slug>` | add images and clips |
| `.\vp cut <slug> [--draft]` | render the cut |
| `.\vp shorts <slug> [--only 1,3]` / `.\vp thumb <slug>` | Shorts and thumbnails |
| `.\vp status <slug>` | what is done, what is missing, what is out of date |
| `.\vp cost [slug]` | spend per video and per month |
| `.\vp clean <slug> [--all]` | frees disk space by deleting render caches of a finished video |
| `.\vp doctor` | checks Python, ffmpeg, fonts and keys |
| `.\vp selftest [--keep]` | renders a 40-second test video offline and checks every step |

## When you change the script

If you edit `02_script.md` after the shot list or the voice exist, `status` marks them as out of date and `run` updates them:

- Wording fixes inside sentences keep every scene and every image.
- A part whose sentences were added, removed or split gets new scenes; only that part. Its old images move to `assets\_old`, so do big rewrites before you make images.
- The voice re-generates only the paragraphs you changed.

## Costs per video (approximate)

- Claude (Opus 5.5): measured on the test video, see `.\vp cost`. On the Claude app engine it comes out of your plan.
- ElevenLabs: about 21,000 characters for a 3,500-word script. Each paragraph is cached, so re-runs only pay for changed paragraphs.
- Midjourney: about 100-130 images per video by default (`max_images` in `config.yaml`).
- Kling: about 20 clips per video (`max_motion`).

## Settings

`config.yaml` holds the channel name, model, script length, image and clip budgets, the Midjourney style suffix, voice settings and render settings. The prompts Claude gets are plain text files in `vidpipe\prompts`, so you can change the channel's voice there.
