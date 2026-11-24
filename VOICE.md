# Directing the narrator

The voice reads exactly what is in `02_script.md`. To make it sound like a storyteller instead of a newsreader, you write small directions into the script. They are never read out, never shown in captions and never change the beat numbers, so the shot list and package stay valid.

## The four marks

| You write | What it does | Example |
|---|---|---|
| `{tense}` at the start of a paragraph | Sets the mood for that paragraph and the next ones, until another mood or the next part. `{normal}` goes back to the plain voice. | `{grave} Three days later, FTX filed for bankruptcy.` |
| `[pause]` inside a sentence | A beat before something lands. Also `[short pause]`, `[long pause]`. | `he had told the world: [pause] "FTX is fine."` |
| `*word*` | Leans on that word. | `It had been gone for *months*.` |
| `[silence 1.5s]` on its own line | That much silence after the paragraph above. Silence costs nothing. | (a line between two paragraphs) |

With the newer models (eleven_v4, eleven_v3) you can also put acting tags in the middle of a sentence: `[quietly]`, `[slowly]`, `[wry]`, `[whispers]`, `[sighs]`. Use them rarely. A tag every few paragraphs sounds directed; a tag in every sentence sounds like a cartoon.

## The moods

They live in `config.yaml` under `voice: moods:`, and you can change them or add your own there.

| Mood | Use it for |
|---|---|
| `{calm}` | explaining, lessons, the closing |
| `{warm}` | backstory, the human side |
| `{curious}` | questions to the viewer, mysteries |
| `{tense}` | something is wrong but nobody knows yet |
| `{urgent}` | the day-by-day collapse, fast events |
| `{grave}` | bankruptcy, verdicts, the big losses |
| `{somber}` | victims, people who lost savings |
| `{wry}` | quoting a lie back ("FTX is fine"), irony |
| `{reveal}` | the number or fact the whole part built up to |

Each mood has two halves. The `tag` is the acting direction that eleven_v4 and eleven_v3 hear before the paragraph, such as `[tense, low voice]`; you can write any short description there. The numbers are what the older eleven_multilingual_v2 model uses instead, because it cannot take directions:

- **stability**: lower means more emotion and more variety, higher means steadier. 0.3 to 0.6 is the useful range.
- **style**: higher means more dramatic. Above 0.5 it can start to sound overacted.
- **speed**: 0.7 to 1.2, where 1.0 is normal. 0.92 feels weighty; 1.07 feels urgent.

## Which model

- **eleven_multilingual_v2** is what made your first take. It is steady and clean, but it cannot act. The moods only nudge its settings, and acting tags are left out so it doesn't read them aloud. `[pause]` still works.
- **eleven_v4** is ElevenLabs' newest and most expressive model. It follows the mood tags, the `[pause]` and acting tags, and the `*stressed*` words. This is the one that fixes "no intonation". Try it with your voice before you switch, because some voices act better than others.

The model goes in `config.yaml` under `voice: model:`.

## How to work without wasting credits

ElevenLabs charges per character. Your Starter plan has 30,000 a month, and a full FTX voice-over is about 20,000. So try things on a few paragraphs first:

1. **Put your voice in.** Run `.\vp voices`, copy your voice's id, and paste it into `config.yaml` as `voice_id: "..."`.
2. **See the paragraphs (free).** `.\vp paras ftx` lists every paragraph with a number, its mood and its length.
3. **Try a few.** `.\vp sample ftx 1 20 47` voices just paragraphs 1, 20 and 47 (about 1,000 credits) and opens the result. The files are in `videos\ftx\audio\samples`.
4. **Compare.** Put commas between choices, and you get one file for each combination:
   - `.\vp sample ftx 20 --model eleven_v4,eleven_multilingual_v2` compares the two models.
   - `.\vp sample ftx 47 --mood tense,grave,none` compares moods on the same paragraph.
   - `.\vp sample ftx 20 --voice ID1,ID2` compares two voices.
   - `.\vp sample ftx --text "{grave} Nine days. [pause] That is all it took."` tries any line you type.
5. **Keep what you like.** Change `config.yaml` or the marks in the script, and sample again.
6. **Voice the whole video.** `.\vp voice ftx` tells you how many credits it will use and how many you have left, then asks before it starts. Then `.\vp run ftx` makes the cut.

Money-saving rules:

- A paragraph is only paid for again if its words, marks, mood or settings changed. Changing the mood of five paragraphs re-voices five paragraphs, not the whole video.
- Anything you sampled is reused by the full voice-over, as long as the voice, model and settings are the same.
- Changing the voice, the model or the main settings re-voices everything, at about 20,000 credits.
- `.\vp credits` shows what is left this month.

## Tips from narration work

- Change the mood when the story's temperature changes, about every two to four paragraphs. The same mood for a whole part flattens it again.
- Put `[pause]` before the number, not after it: `came to just [pause] *$134 million*.`
- Use one stressed word per paragraph at most.
- Use `[silence 1s]` after the two or three biggest moments of the video, not after every paragraph.
- If a paragraph comes out strange, change one thing and sample just that paragraph again. It costs a few hundred credits.
- New scripts that Claude writes already come with these marks. The FTX script has them too, so you can see how they look in practice.
