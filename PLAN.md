# Video pipeline plan

Status 2026-11-23: design done, code not started. Waiting on the user's OK to install ffmpeg and download 3 Google fonts (Anton, Inter, IBM Plex Mono).

## Layout

```
vp.cmd                  python -m vidpipe %*
config.yaml             models, voice, style suffix, fps, caps (max images, max Kling clips)
.env                    ANTHROPIC_API_KEY, ELEVENLABS_API_KEY (user fills from .env.example)
vidpipe/                cli, config, llm, research, script, factcheck, shotlist, package,
                        voice, align, board, assets, cards, render, cut, shorts, thumbnail, costs
vidpipe/prompts/*.md    editable prompt templates + style_guide.md
library/music, sfx, fonts
videos/<slug>/          video.yaml, 01_research.md, sources.json, 02_script.md, 03_factcheck.md,
                        04_shotlist.json, 05_package.md, audio/, assets/{inbox,images,clips,cards}, edit/, logs/
```

## Stages

1. `new` - creates videos/<slug>/video.yaml (topic, angle, target words, notes).
2. `research` - Claude with web search -> dossier (timeline, people, numbers, quotes, legal outcomes) + numbered sources; link-check every URL.
3. `script` - 7 parts (cold open, promise, rise, first cracks, unravelling, aftermath, lesson), ~3,500 words, written for speech.
4. `factcheck` - claim table (supported / unsupported / disputed), "alleged" for anyone not convicted; `--fix` applies edits with a backup.
5. `shotlist` - script pre-split into beats (8-16 words); one visual bible call (recurring figures, places), then one call per part in parallel. Scene types: still (Midjourney + Ken Burns), motion (Midjourney -> Kling), card (rendered locally), reuse. Caps on unique images and Kling clips from config. Never real names in Midjourney prompts; stylized figures only.
6. `package` - 3 titles, thumbnail texts, description, chapters (timestamps filled after voice), tags, 3-5 Shorts moments as beat ranges.
7. `voice` - ElevenLabs with-timestamps per chunk (~2,500 chars, previous/next text for continuity, cached by hash); free Windows SAPI draft voice with word events; or user mp3 + ElevenLabs forced alignment.
8. `board` - local web page: per scene, copy buttons for Midjourney/Kling prompts and a drop/paste zone that saves S012.png / S012.mp4. `sort` matches inbox files by Midjourney filename text (RapidFuzz).
9. `cut` - scene times from word timings, per-scene segments cached, animated cards (stat count-up, quote, date, document, title), placeholders for missing assets, music bed with ducking, generated whoosh/hit SFX, SRT, FCPXML of the segments for DaVinci, roughcut.mp4.
10. `shorts` - 9:16 re-render of each moment, word-by-word captions, hook text, <= 60 s.
11. `thumb`, `run` (all automatic steps, stops at the board), `status`, `cost`.

LLM engines: `api` (Anthropic SDK, claude-opus-5-5, structured outputs, web_search_20260209, fallbacks via extra_body) or `claude-cli` (`claude -p --output-format json --json-schema ... --tools WebSearch,WebFetch`, scrubbed CLAUDE_* env). Auto-pick: API key if set, else CLI.

Style: bg #0B0B0C, gold #E8B54A, red #E5484D for losses, text #F2EFE8, muted #8f8b84, paper #ece7dc; Anton / Inter / IBM Plex Mono, fallback Bahnschrift / Segoe UI / Consolas.

Test: ~60-90 s sample through every stage (SAPI voice, placeholder images), then ElevenLabs once a key exists. When done, message the launch-plan thread with the shot-list format.
