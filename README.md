# zx-smart-room

Smart room projects around a Samsung **The Frame** TV (Tizen 6.0), run from a Windows laptop.

## What's here

| Path | What it is |
|---|---|
| `frame-launcher/` | **Home**: a custom TV home screen (Tizen web app). Live wallpaper, frosted-glass clock, bento app grid. |
| `deploy-home.ps1` | Builds, signs and installs Home on the TV, then opens it. |
| `home-keeper.ps1` | Background watcher that reopens Home at power-on and after leaving an app. |
| `voice/assistant.py` | **Zephyr** (name set by `WAKE_NAME`): say "Zephyr, turn on the TV". Name spotted locally (Vosk), Corelynx speech-to-text, TV on / off / Art Mode / apps. |
| `voice/pair_tv.py` | One-time pairing with the TV's remote-control channel (press Allow on the TV). |
| `voice/brain.py` | Claude Haiku 5.5: conversation, personality and all tools (TV, YouTube, Spotify, settings, memory, lessons). |
| `voice/memory.py` | Long-term memory, lessons and conversation notes as Markdown in an Obsidian vault. |
| `voice/review.py` | Nightly self-review: measures the day, writes lessons and a report to the vault. |
| `voice/voice_id.py`, `phone_mic.py`, `spotify.py`, `youtube_tv.py`, `keystore.py` | Voice ID, Android phone as the mic, Spotify, YouTube (TizenTube), keys in Windows Credential Manager. |
| `tests/` | Tests for commands, tools and parts; no hardware needed. |
| `deploy.ps1` | Ships a change: runs the tests, then restarts Zephyr (and with `-HomeScreen`, the TV's Home). |

## Continuous improvement and delivery

- **CI**: every push runs the tests on GitHub (`.github/workflows/ci.yml`).
- **CD**: `deploy.ps1` tests first and only restarts Zephyr if everything passes. Zephyr runs as the
  `Zephyr` scheduled task (starts at login, restarts itself if it crashes).
- **Learning from use**: corrections become lessons right away (`learn_lesson` tool), and the
  `Zephyr Nightly Review` task reviews each day at 4 am. Lessons live in the vault at
  `Zephyr/Lessons.md`, reports in `Zephyr/Reports/`. Both are plain Markdown: edit or delete freely.

## Not in the repo (and how to get it back)

| Missing | Why | How to restore |
|---|---|---|
| `voice/corelynx.env` | API key | Copy `voice/corelynx.env.example` to `voice/corelynx.env` and fill it in. |
| `voice/tv-token.txt` | TV pairing token | Run `python voice/pair_tv.py` and press **Allow** on the TV within 30 s. |
| `voice/models/vosk-model-small-en-us-0.15/` | 68 MB, downloadable | Download from https://alphacephei.com/vosk/models and unzip into `voice/models/`. |
| `frame-launcher/fonts/SF-Pro-*.woff2` | Apple licence | Subset SF Pro Display Bold/Semibold/Medium and SF Pro Text Medium to Latin WOFF2 with fontTools. |
| `frame-launcher/media/wallpaper.mp4` | 60 MB | Any 16:9 H.264 loop; `poster.jpg` and the frost images are made from its first frame. |

## Requirements

- Tizen Studio CLI with a Samsung certificate profile named `FrameHome` (Samsung account; TV DUID registered).
- TV: Developer Mode pointing at this laptop, **IP Remote** on, **Power On with Mobile** on.
- Python 3.12: `pip install sounddevice numpy openwakeword vosk websocket-client comtypes`
