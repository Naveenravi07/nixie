# Nixi

A desktop voice assistant for Linux. Trigger Nixi, ask a question or give a
command, and it handles it — change the volume, set a wallpaper, take a
screenshot, control media, or answer general queries using Gemini.

## What It Does

- **System control** — volume, brightness, mute, media playback, screenshots,
  wallpaper changes, app launcher.
- **General queries** — ask anything. Nixi uses Gemini and can search the web
  for live information like weather, news, and prices.
- **Vision** — say "open your eyes", "where should I click", or "why is this
  red" and Nixi asks permission to screenshot your screen, then describes what
  it sees. Approval is via desktop notification with a configurable timeout.
- **Voice interaction** — manual trigger-to-record mechanism, speech-to-text,
  and text-to-speech. No constant background listening/wake-word overhead.

## Installation & Setup

### Option 1: Global Installation (Recommended)

Install Nixi globally on your system using [pipx](https://github.com/pypa/pipx):

```sh
pipx install git+https://github.com/your-username/nixi.git
```

This will automatically expose the commands globally. Run any command once to automatically initialize your configuration directory:

```sh
nixi-server
```

Now, configure your API keys by editing `~/.config/nixi/.env`:

```dotenv
GOOGLE_CLOUD_API_KEY=your-vertex-express-mode-key
SARVAM_API_KEY=your-sarvam-key
```

### Option 2: Local Development Setup

If you'd like to run or develop Nixi locally, install [uv](https://docs.astral.sh/uv/) and sync dependencies:

```sh
uv sync
```

Run any command once (e.g. `uv run nixi-server`) to auto-generate your config, then add your API keys to `~/.config/nixi/.env`.

## Run

> **Note:** If you are using the local development setup, prefix all of the following commands with `uv run ` (e.g., `uv run nixi-server`).

First, discover your desktop environment (keybinds, scripts, wallpapers):

```sh
nixi-server --discover
```

Then start the server:

```sh
nixi-server
```

Next, start the voice daemon (which runs as a background process listening for manual trigger events):

```sh
nixi-voice
```

To interact with Nixi, trigger the recording. You can bind keys or desktop shortcuts to run these commands:

- **Trigger Recording (Toggle)**: Start or stop recording your query.
  ```sh
  nixi-voice trigger
  ```
- **New Session**: Start a fresh conversation session and trigger recording.
  ```sh
  nixi-voice new
  ```

Run the command once to start recording, speak your request, and run it again to stop and process the audio!

## Configuration

Nixi resolves its config in this order:

1. `--config <path>` CLI flag
2. `$NIXI_CONFIG` environment variable
3. `~/.config/nixi/nixi.toml` (user config)
4. `example_config/nixi.toml` (repo default)
5. Code defaults (no file needed)

The system prompt lives in code. Set `agent_name` in your TOML to change the
persona — the prompt template uses it automatically.

Edit your TOML to configure audio thresholds, STT, TTS, model
settings, vision, and local actions.
