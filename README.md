# Nixie / Lexi Popup Prototype

This repo currently contains a small GTK popup prototype for Lexi. On Wayland,
it uses `gtk-layer-shell` to anchor the popup at the bottom of the screen. On
other sessions, it falls back to a regular GTK utility popup.

## Run

Open only the bottom popup:

```sh
python3 app/lexi_popup.py --open
```

`--open` is the command to use from a window-manager keybind. Closing the popup
also exits the process.

The test controller is still available while prototyping:

```sh
python3 app/lexi_popup.py --controller
```

In controller mode, use any of these triggers:

- Click `Open Lexi`
- Press `Ctrl+Space`
- Type `hey lexi` and press Enter

## Keybind Examples

Hyprland:

```sh
bind = SUPER, L, exec, python3 /home/shastri/Code/nixie/app/lexi_popup.py --open
```

BSPWM:

```sh
super + l
    python3 /home/shastri/Code/nixie/app/lexi_popup.py --open
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
