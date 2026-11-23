# Install on Windows

You need Windows 10 or 11 and an internet connection for the first run. Everything else is set up for you.

## 1. Run the setup

Double-click **`setup.cmd`** in this folder. If Windows asks whether to run the file, choose Run.

It takes 2 to 5 minutes and does this, skipping anything that is already there:

1. Finds Python 3.11 or newer. If there is none, it installs Python 3.14 for your user with winget.
2. Makes a private Python environment in `.venv` and installs the packages from `requirements.txt`.
3. Checks ffmpeg in `tools\ffmpeg\bin`. If it is missing, it downloads the gyan.dev "essentials" build and checks its checksum.
4. Checks the fonts in `library\fonts` (they come with the folder; missing ones are downloaded from Google Fonts).
5. Creates `.env` from `.env.example`. An existing `.env` is never touched.
6. Runs `vp doctor`, the unit tests and `vp selftest`, which renders a 40-second test video (draft, full cut, music, captions, DaVinci timeline, a Short, thumbnails) and checks 32 things about it.

It ends with **Setup finished. Everything works.** If it lists problems instead, fix them and run `setup.cmd` again.

## 2. Add your keys

Open `.env` with Notepad:

- `ELEVENLABS_API_KEY=` your ElevenLabs key (ElevenLabs: Profile, then API keys). Without it the free robotic Windows voice is used, which is only good for checking timing.
- `ANTHROPIC_API_KEY=` is optional. Without it, the Claude steps run through the Claude app that is logged in on this computer.

## 3. Make a video

Open PowerShell in this folder (click the folder's address bar, type `powershell`, press Enter), then:

```powershell
.\vp new ftx "How FTX lost 8 billion dollars of customer money"
.\vp run ftx
```

README.md explains every step, the scene board for Midjourney and Kling, and the upload checklist.

## If something goes wrong

- `.\vp selftest` renders the test video again and says which step fails. When a check fails, the test files stay in `videos\_selftest`.
- `.\vp doctor` checks Python, ffmpeg (with the parts the pipeline needs), the fonts and the keys.
- In Command Prompt, type `vp` instead of `.\vp`.

## Updating to a newer version

Copy the new files over the old ones. Keep your `.env`, `config.yaml`, `videos` and `library\music`. Then run `setup.cmd` again.
