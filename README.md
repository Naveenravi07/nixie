# Nixie / Lexi Popup Prototype

This repo currently contains a small GTK popup prototype for Lexi. On Wayland,
it uses `gtk-layer-shell` to anchor the popup at the bottom of the screen. On
other sessions, it falls back to a regular GTK utility popup.

## Run

Open only the bottom popup:

```sh
python3 -m app.agent_window.main --open
```

`--open` is the command to use from a window-manager keybind. Closing the popup
also exits the process.

The test controller is still available while prototyping:

```sh
python3 -m app.agent_window.main --controller
```

In controller mode, use any of these triggers:

- Click `Open Lexi`
- Press `Ctrl+Space`
- Type `hey lexi` and press Enter

Run the local server:

```sh
python3 -m app.server.main
```

The server listens on `127.0.0.1:8765` by default and loads actions from
`config/lexi.toml`.

The old launchers still work:

```sh
python3 app/lexi_popup.py --controller
python3 app/lexi_server.py
```

Useful test requests:

```sh
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/actions
curl -X POST http://127.0.0.1:8765/message \
  -H 'Content-Type: application/json' \
  -d '{"message":"say hello"}'
```

## Action Config

Actions are local command bindings. Lexi asks for an action by name; your config
decides what command actually runs on this machine.

```toml
[actions.set_wallpaper]
description = "Set wallpaper with my personal script"
command = "/home/shastri/.local/bin/set-wallpaper {path:q}"
triggers = ["set wallpaper", "change wallpaper"]
```

Use `{name}` for raw placeholder values and `{name:q}` for shell-quoted values.
For custom scripts, prefer `{path:q}` or similar when paths can contain spaces.

## Keybind Examples

Hyprland:

```sh
bind = SUPER, L, exec, python3 -m app.agent_window.main --open
```

BSPWM:

```sh
super + l
    cd /home/shastri/Code/nixie && python3 -m app.agent_window.main --open
```

## Wake Word Note

Real voice activation needs a long-running background process. That process
keeps the microphone open, detects a wake phrase like `hey lexi`, and then
opens this popup.

For the package, the clean shape is:

- `lexi-popup`: draws the bottom popup
- `lexi-daemon`: optional background service for wake word and global triggers
- desktop/autostart or systemd user unit: starts `lexi-daemon` when the user opts in

The current prototype does not constantly listen to audio yet.
