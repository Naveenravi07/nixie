# Nixi

Nixi is a local-first Linux voice assistant for desktop workflows. It listens
for a wake phrase, shows a small desktop popup, transcribes the command, and
either runs a configured local action or replies through the configured LLM.

The project is meant to be a personal desktop assistant, not a cloud service.
Idle microphone audio and wake-word detection stay local. After wake-up, command
audio can be sent to the configured STT provider, and unmatched messages can be
sent to the configured LLM provider.

## What It Does

- Listens for wake phrases such as `Hey Nixi`.
- Shows a bottom-center GTK popup while it is listening.
- Converts command speech to text.
- Matches phrases to local actions from `config/nixi.toml`.
- Falls back to Gemini 3.5 Flash-Lite through Vertex AI Express Mode when no local action matches.
- Runs a live no-key web search for time-sensitive questions such as weather,
  news, closures, prices, and schedules, and uses the results to answer with
  up-to-date information.
- Speaks responses with Sarvam TTS when enabled.
- Exposes a small local HTTP API for messages and actions.

## Components

| Component | Command | Purpose |
| --- | --- | --- |
| Voice | `uv run nixi-voice` | Wake phrase detection, command recording, STT, TTS, and popup control |
| Server | `uv run nixi-server` | Message routing, LLM replies, and configured action execution |
| Popup | `uv run nixi-popup` | GTK desktop popup for Wayland and X11 |

Internally, shared settings are exposed through `app.config`. Voice capture and
segmentation, wake-word recognition, popup lifecycle, server transport, and the
conversation state machine live in separate modules so each can be tested or
changed independently.

The voice process starts and closes the popup automatically. You can also run
the popup directly when debugging the desktop UI.

## Dependency Model

Nixi uses two dependency layers:

1. Python app dependencies managed by `uv`.
2. Native Linux desktop bindings installed by the system package manager.

`uv sync` installs the Python packages declared in `pyproject.toml`, such as
Google Gen AI, aiohttp, faster-whisper, and numpy.

The popup is different. It uses GTK, PyGObject, PyCairo, librsvg, and optionally
gtk-layer-shell. Those bindings are tied to your desktop stack and are loaded
through `/usr/bin/python3` by [app/popup/launcher.py](app/popup/launcher.py).
Because of that, installing Cairo only inside the uv virtualenv will not fix the
popup. The system Python must be able to import Cairo.

On Arch Linux, install the native popup dependencies with:

```sh
sudo pacman -S python-gobject python-cairo gtk3 librsvg gtk-layer-shell
```

On Debian/Ubuntu, the equivalent packages are usually:

```sh
sudo apt install python3-gi python3-cairo gir1.2-gtk-3.0 librsvg2-common
```

`gtk-layer-shell` is only useful on Wayland compositors that support the layer
shell protocol. On Xorg, Nixi falls back to a normal undecorated GTK window.

## Setup

Install [uv](https://docs.astral.sh/uv/) and synchronize the Python environment:

```sh
uv sync
```

`uv` selects Python 3.13 from `.python-version`, creates `.venv`, installs the
declared Python dependencies, and writes `uv.lock`. The first voice launch can
also download the configured Whisper model.

Install the native desktop packages for your distro as described above. To check
the Cairo binding used by the popup launcher:

```sh
/usr/bin/python3 -c "import cairo; print('cairo ok')"
```

Copy the environment template and configure your Vertex AI Express Mode key and
other provider keys:

```sh
cp .env.example .env
```

```dotenv
GOOGLE_CLOUD_API_KEY=your-vertex-express-mode-key
# Optional project-scoped Vertex configuration:
# GOOGLE_CLOUD_PROJECT=your-project-id
# GOOGLE_CLOUD_LOCATION=global
SARVAM_API_KEY=your-sarvam-key
```

`.env` is ignored by Git. Gemini requests run through Vertex AI Express Mode
using `GOOGLE_CLOUD_API_KEY`; restrict that key to the Vertex AI API in Google
Cloud. To use the standard project-scoped endpoint instead of Express Mode, set
both `GOOGLE_CLOUD_PROJECT` (the project ID, not display name) and
`GOOGLE_CLOUD_LOCATION`. Sarvam uses only `SARVAM_API_KEY`.

## Run

Start the local server:

```sh
uv run nixi-server
```

Start the voice daemon in another terminal and stay quiet during its one-second
microphone calibration:

```sh
uv run nixi-voice
```

Say `Hey Nixi`, wait for the popup, and speak a command. A single utterance such
as `Hey Nixi, take a screenshot` also works.

For popup-only debugging:

```sh
uv run nixi-popup
```

## Configuration

Edit [config/nixi.toml](config/nixi.toml) to configure wake phrases, audio
thresholds, STT, TTS, model settings, and local actions.

`llm.google_search_enabled` controls web search for current-information questions.
Keep it enabled (the default); ordinary conversation does not invoke Search.
Time-sensitive prompts trigger a live DuckDuckGo search whose results are fed to
the model as grounded context.

Example action:

```toml
[actions.set_wallpaper]
description = "Set the wallpaper"
command = "/home/me/.local/bin/set-wallpaper {path:q}"
triggers = ["set wallpaper", "change wallpaper"]
```

Use `{name}` for a raw action argument and `{name:q}` for a shell-quoted value.
If room noise triggers recording, increase `speech_threshold`. If quiet speech
is missed, decrease it. Set `microphone_target` to a PipeWire node id or name
from `wpctl status` when the default input is wrong.

For interruptions while Nixi is speaking, tune `barge_in_threshold_multiplier`
and `barge_in_speech_start_ms`. Lower values make interruption easier; higher
values reject more speaker audio and room noise.

The server exposes these endpoints on `127.0.0.1:8765` by default:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Health check |
| `GET /actions` | List configured actions |
| `POST /message` | Route a user message to an action or LLM response |
| `POST /actions/<name>` | Run one configured action directly |

## Start at Login

The included systemd user services assume this repository is located at
`~/Personal/nixie`. Adjust their paths first if needed.

```sh
mkdir -p ~/.config/systemd/user
cp config/systemd/nixi-{server,voice}.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nixi-voice.service
```

View voice logs with:

```sh
journalctl --user -u nixi-voice.service -f
```

## Troubleshooting

If the popup prints this error:

```text
TypeError: Couldn't find foreign struct converter for 'cairo.Context'
```

install the system PyCairo package. On Arch Linux:

```sh
sudo pacman -S python-cairo
```

If `/usr/bin/python3 -c "import cairo"` fails, `uv sync` will not fix the popup,
because the popup intentionally uses the system Python that owns the GTK/GI
bindings.
