# Nixi

Nixi is a local voice-command assistant for Linux. It listens through
PipeWire, transcribes speech with a local Whisper model, displays a small GTK
popup, and dispatches matched commands to user-configured actions.

## Components

| Component | Command | Purpose |
| --- | --- | --- |
| Voice | `uv run nixi-voice` | Local wake phrase, Sarvam command STT, and Sarvam TTS |
| Server | `uv run nixi-server` | Message matching and configured action execution |
| Popup | `uv run nixi-popup` | Bottom-center GTK layer-shell window |

The voice process starts and closes the popup automatically. The popup can also
be run directly for visual testing.

## Setup

Install [uv](https://docs.astral.sh/uv/) and synchronize the environment:

```sh
uv sync
```

uv selects Python 3.13 from `.python-version`, creates `.venv`, installs the
declared dependencies, and writes `uv.lock`. The first voice launch downloads
the configured Whisper model.

The popup uses the distribution-provided GTK 3, PyGObject, librsvg, and
gtk-layer-shell bindings. On Arch Linux these are provided by `python-gobject`,
`gtk3`, `librsvg`, and `gtk-layer-shell`. The `nixi-popup` uv command launches
that native GUI through the system Python interpreter.

Copy the environment template and add your API keys:

```sh
cp .env.example .env
```

```dotenv
GOOGLE_API_KEY1=your-first-google-key
GOOGLE_API_KEY2=your-second-google-key
# Continue through GOOGLE_API_KEY10 when needed.
SARVAM_API_KEY=your-sarvam-key
```

`.env` is ignored by Git. Gemini 3.5 Flash requests run through LiteLLM and
rotate across all configured Google keys. Any failed key is skipped for the
configured cooldown, including rate-limit, authentication, and model-access
errors. Sarvam uses only `SARVAM_API_KEY`.

## Run

Start the server:

```sh
uv run nixi-server
```

Start the voice daemon in another terminal and remain quiet during its one-second
microphone calibration:

```sh
uv run nixi-voice
```

Say `Hey Nixi`, wait for the popup, and speak a command. A single utterance such
as `Hey Nixi, take a screenshot` also works. Idle microphone and wake-word audio
stay local. After Nixi wakes, command audio is streamed to Sarvam for
transcription; it remains in memory and is not saved by Nixi.

## Configuration

Edit [config/nixi.toml](config/nixi.toml) to change wake-phrase variants, audio
thresholds, Saaras realtime STT settings, model settings, and actions. For example:

```toml
[actions.set_wallpaper]
description = "Set the wallpaper"
command = "/home/me/.local/bin/set-wallpaper {path:q}"
triggers = ["set wallpaper", "change wallpaper"]
```

Use `{name}` for a raw action argument and `{name:q}` for a shell-quoted value.
If room noise triggers recording, increase `speech_threshold`. If quiet speech
is missed, decrease it. Set `microphone_target` to a node shown by
`wpctl status` when the default input is wrong.

The server exposes `GET /health`, `GET /actions`, `POST /message`, and
`POST /actions/<name>` on `127.0.0.1:8765` by default.

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
