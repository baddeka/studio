# File formats

For anyone (a person or another Claude) who prepares material for a video by hand instead of letting `vp run` make it. Put the files in `videos\<slug>\` (`.\vp new <slug> "<topic>"` makes the folder, or just create it) and run `.\vp run <slug>`. Steps whose file already exists are skipped, and everything after them is generated. Hand-written files are checked and taken over as they are. Nothing written by hand is sent to Claude to be redone.

## 01_research.md

Free Markdown. It must end with a `## Sources` section, one source per line:

```
## Sources
[1] SEC v. Samuel Bankman-Fried, complaint - SEC - 2022-12-13 - https://www.sec.gov/...
[2] FTX files for bankruptcy - Reuters - 2022-11-11 - https://www.reuters.com/...
```

Facts in the body carry the numbers in brackets, like `[2]`. The fact-check step checks the script against this file. The pipeline reads the Sources section into the video description and reports dead links.

## 02_script.md

```
# Working title

## Part 1: Cold open
Narration paragraph. Two to five sentences.

Another paragraph.

## Part 2: The promise
...
## Part 7: The lesson
...
```

- Exactly 7 parts, headings `## Part N: Title`. The title after the colon becomes the YouTube chapter name.
- Only narration under the headings: no bullet points, bold, brackets or stage directions. A paragraph wrapped in `[ ]` or a line starting with `>` is treated as a note and not read aloud.
- Written to be spoken: "$8 billion", "percent" instead of %, no symbols.
- About 3,500 words for a 23-minute video.

## 03_factcheck.md

Your own fact-check notes, in any form. When this file exists, `vp run` skips the automatic fact-check. That check sends the script to Claude and can rewrite it, which would also move the beats your shot list points to.

## 04_shotlist.json

`.\vp shotlist` makes it, or you write it by hand. The script is split into numbered beats (one sentence or clause, 6-16 words). Each scene covers 1-2 consecutive beats, about 4-6 seconds.

Get the beats from the finished script, numbered exactly the way the pipeline numbers them:

```
.\vp beats ftx               readable list: B12 (9 words): text, by part
.\vp beats ftx --json        the "beats" array for the file
```

Outside the installed pipeline, run `python -m vidpipe.beats path\to\02_script.md --json` from the `video-pipeline` folder. It needs only Python 3. Write the shot list after the script is final, because any edit can move the numbers.

A hand-written shot list:

```json
{
  "version": 1,
  "beats": [ ...the --json output, unchanged... ],
  "scenes": [
    {"id": "S1-01", "beats": [1], "type": "still", "visual": "Empty trading floor at night",
     "camera": "push_in", "onscreen": "Nassau, Bahamas - 2022",
     "mj_prompt": "an empty open-plan trading floor at night, rows of dark monitors, one desk lamp still on"},
    {"id": "S1-02", "beats": [2], "type": "card",
     "card": {"kind": "stat", "text": "$8,000,000,000", "sub": "of customer money missing", "items": [], "tone": "loss"}},
    {"id": "S1-03", "beats": [3, 4], "type": "motion", "mj_prompt": "a lone figure in a glass penthouse in the rain",
     "kling_prompt": "slow dolly in, rain streaks on the glass, screens flicker"},
    {"id": "S1-04", "beats": [5], "type": "reuse", "reuse_of": "S1-01", "camera": "pan_left"}
  ]
}
```

- Leave out `script_hash`. `vp run` (or `vp shotlist`) then checks the file against the script and takes it over. It never sends a hand-written file to Claude. If something is wrong, it stops and lists every problem: a beat in no scene or in two scenes, scenes out of order, a missing prompt, an unknown card kind. Nothing is changed until the file is fixed.
- Scenes follow the script order. Together they cover every beat exactly once, and a scene's beats are all in one part.
- `id`: per part, `S<part>-<number>` (`S3-07` is scene 7 of part 3). Image and clip files use the id as their name: `assets\images\S3-07.png`, `assets\clips\S3-07.mp4`. An id that is left out is numbered automatically.
- `type`: `still` (Midjourney image plus an automatic camera move), `motion` (Midjourney image animated in Kling), `card` (drawn automatically), `reuse` (an earlier still or motion image with another camera move).
- `mj_prompt` (still and motion): 20-45 words on subject, setting, composition, light and mood. Leave out the style words and `--ar`, because the pipeline adds the channel style from `config.yaml`. To control the whole prompt yourself, give `mj_full` too.
- `kling_prompt` (motion only): 8-25 words of slow, plausible camera movement and motion.
- `camera` (still and reuse): `push_in`, `pull_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `static`. Vary it from scene to scene.
- `card.kind`: `stat`, `quote`, `date`, `title`, `text`, `document`, `list`, `compare`. `card.tone`: `neutral`, `loss`, `gain`, `alert`. The rules for each kind are in `vidpipe\prompts\shotlist.md`. For example, `compare` needs exactly 2 items, each written as "Label: value".
- `reuse_of`: the id of an earlier still or motion scene.
- `visual` (one line for the editor) and `onscreen` (a lower-third, under 40 characters) are optional.
- Midjourney prompts never contain real names, logos or readable text. Real people are stylized, faceless figures.

## 05_package.json

Titles, thumbnail, description and Shorts. `.\vp package` makes it, or you write it by hand (again without `script_hash`):

```json
{
  "titles": ["How FTX Lost $8 Billion in 9 Days", "...", "..."],
  "thumbnail_texts": ["$8B GONE", "9 DAYS", "..."],
  "thumbnail_prompt": "a lone stylized figure on a collapsing tower of gold coins, dark background, empty left third",
  "description": "Two or three short paragraphs. Chapters, sources and the disclaimer are added automatically.",
  "tags": ["ftx collapse", "sam bankman-fried", "..."],
  "hashtags": ["#ftx", "#crypto", "#truecrime"],
  "shorts": [{"title": "The 9 days that ended FTX", "hook": "$8 billion, gone", "beat_from": 1, "beat_to": 12,
              "why": "opens on the collapse, ends on the missing money"}],
  "pinned_comment": "One question for viewers."
}
```

- `shorts`: 3-5 ranges of consecutive beats (the same numbers as the shot list), each holding 70-140 words of narration. A range is stretched or trimmed to 60-150 words if needed, and `vp run` says when it does that.
- `thumbnail_prompt` gets the channel style added like `mj_prompt`. The rules for each field are in `vidpipe\prompts\package.md`.
- A hand-written package is kept when the script changes later. `vp run` then asks you to check the Shorts ranges and never replaces your titles. `.\vp package <slug> --force` has Claude write a new package.
