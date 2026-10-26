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

**Continuous delivery** (code changes ship safely):

| Stage | What runs |
|---|---|
| Every commit | `tools/hooks/pre-commit`: secret scan, ruff lint, all tests (`git config core.hooksPath tools/hooks`) |
| Every push | GitHub Actions (`.github/workflows/ci.yml`): lint, tests with a coverage floor, gitleaks secret scan |
| Every deploy | `deploy.ps1`: lint + tests, committed code only, restart, `voice/health.py` (Zephyr stable + TV, Corelynx, Claude, ElevenLabs, YouTube, Spotify, SmartThings), version tag `deployed-*`, automatic rollback to the last good tag if unhealthy |
| Always | Zephyr runs as the `Zephyr` scheduled task: starts at login, restarts itself after a crash |

Dependencies are pinned in `requirements.txt` (yt-dlp deliberately kept current). Tests use fakes for
Claude, the TV, Spotify, YouTube and the voice model, and never write to the real log.

**Continuous improvement** (Zephyr gets better from real use):

| Loop | How |
|---|---|
| Corrections | "No, I said fifteen" → `learn_lesson` → `Zephyr/Lessons.md` in the Obsidian vault, read before every reply |
| Nightly review (4 am) | `voice/review.py` measures the day, compares it with the 7-day average (regressions flagged at the top of the report), and proposes lessons |
| Probe before adopting | `voice/probe.py` (Propose–Probe–Compose, after Google Research's Harness2): proposed lessons are tested on recent real requests against stand-in devices and adopted only if Zephyr answers at least as well |
| Memory | Long-term facts (`Memory.md`), lessons, and the last 20 conversations |
| Code fixes | The report's "ideas for code changes" feed the next development session; each fix ships with a regression test |

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
