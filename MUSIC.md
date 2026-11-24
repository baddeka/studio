# Music by mood

The music now follows the story. Songs live in mood folders, each part of the script gets a mood, and where
the mood changes the music crossfades (4 seconds). The voice still ducks the music, and every song is evened
out to the same loudness first, so a loud song and a quiet song sit the same under the narration.

## The folders

```
library\music\calm         backstory, explaining, neutral
library\music\rise         money pouring in, success, momentum (with a hint of unease)
library\music\tense        something is wrong, suspense, the investigation
library\music\heavy        the collapse, the arrest, the verdict
library\music\reflective   aftermath, lessons, the ending
```

Put 2-3 songs in each. `.\vp get-music` downloads a ready-made starter set of 15 free songs (3 per mood) by
Scott Buckley straight into these folders. They are free under CC BY 4.0, which allows monetized videos as long
as the description credits each song; the pipeline adds those credit lines to the description in
`05_package.md` for you, so copy the whole description when you upload.

Loose songs straight in `library\music` (no folder) still work: a mood whose folder is empty borrows from a
related mood first (heavy borrows tense, and so on), then from the loose songs. With only loose songs and no
folders, the whole video gets one music bed, as before. A video can also have its own `videos\<slug>\music\<mood>`
folders, which win over the library.

Your own songs: drop them into a mood folder. If a song needs a credit, add a line to
`library\music\CREDITS.txt` like `my song.mp3 | "My Song" by Someone, CC BY 4.0`.

## Which mood plays where

In `videos\<slug>\video.yaml`:

```yaml
music:
  1: tense          # part 1
  2: calm
  3: rise
  S3-28: tense      # from scene S3-28 on (scene ids are in the shot list and on vp board)
  6: heavy
  7: reflective
  S7-40: none       # no music from here (none = silence)
```

A part number sets the mood for the whole part; a scene id changes it from that scene on. If a part has no
entry, the script's voice moods choose (`{tense}` -> tense, `{grave}` -> heavy, `{warm}` -> calm ..., the most
used one in the part; the table is `music: from_voice` in config.yaml). A hand-written shot list can also put
`"music": "heavy"` on a scene. A mood stretch shorter than 12 seconds keeps the music before it.

## Check it

```
.\vp music ftx
```

lists the song folders, then the plan (which mood, which songs, from when to when), and writes
`videos\ftx\edit\music_bed.flac`: the music alone, so you can listen through it in a couple of minutes by
skipping around. Change `video.yaml`, run it again, and only then render. The cut report (`edit\report.md`)
shows the same plan.

Settings in config.yaml, under `music:` (all optional): `crossfade` (seconds, 4), `min_section` (12),
`level_lufs` (-16, raise it to make all music louder, or change `video: music_db`, -21).
