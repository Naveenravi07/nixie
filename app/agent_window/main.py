#!/usr/bin/env python3
"""Lexi bottom popup prototype using GTK layer-shell when available."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

try:
    try:
        gi.require_version("WebKit2", "4.1")
    except ValueError:
        gi.require_version("WebKit2", "4.0")
    from gi.repository import WebKit2  # type: ignore  # noqa: E402
except (ImportError, ValueError):
    WebKit2 = None

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell  # type: ignore  # noqa: E402
except (ImportError, ValueError):
    GtkLayerShell = None


POPUP_WIDTH = 460
POPUP_HEIGHT = 118
POPUP_MARGIN_BOTTOM = 34
AVATAR_SIZE = 78
REPO_ROOT = Path(__file__).resolve().parents[2]
AVATAR_SVG = REPO_ROOT / "assets" / "Q19WSHi0PH.svg"


@dataclass(frozen=True)
class RuntimeInfo:
    is_wayland: bool
    has_layer_shell: bool

    @property
    def can_use_layer_shell(self) -> bool:
        return self.is_wayland and self.has_layer_shell


def get_runtime_info() -> RuntimeInfo:
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    return RuntimeInfo(
        is_wayland=session_type == "wayland" or bool(wayland_display),
        has_layer_shell=GtkLayerShell is not None,
    )


class LexiPopup:
    def __init__(self, runtime: RuntimeInfo, on_close: Callable[[], None] | None = None) -> None:
        self.runtime = runtime
        self.on_close = on_close
        self.window: Gtk.Window | None = None

    def open(self) -> None:
        if self.window is None:
            self.window = self._build_window()

        self.window.show_all()
        self.window.present()
        self._position_fallback_window()

    def close(self) -> None:
        if self.window is not None:
            self.window.hide()
        if self.on_close is not None:
            self.on_close()

    def _build_window(self) -> Gtk.Window:
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_name("lexi-popup")
        window.set_title("Lexi")
        window.set_decorated(False)
        window.set_resizable(False)
        window.set_keep_above(True)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.set_default_size(POPUP_WIDTH, POPUP_HEIGHT)
        window.connect("key-press-event", self._handle_popup_key)
        window.connect("delete-event", self._handle_delete)

        if self.runtime.can_use_layer_shell:
            GtkLayerShell.init_for_window(window)
            GtkLayerShell.set_namespace(window, "lexi")
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(window, GtkLayerShell.Edge.BOTTOM, POPUP_MARGIN_BOTTOM)
            GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)

        window.add(self._build_popup_content())
        return window

    def _build_popup_content(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        card.get_style_context().add_class("lexi-card")
        card.set_size_request(POPUP_WIDTH, POPUP_HEIGHT)
        card.set_margin_start(0)
        card.set_margin_end(0)
        card.set_margin_top(0)
        card.set_margin_bottom(0)

        avatar = self._build_avatar()
        card.pack_start(avatar, False, False, 14)

        card.pack_start(self._build_copy(), True, True, 0)
        card.pack_end(self._build_close_button(), False, False, 14)
        return card

    def _build_copy(self) -> Gtk.Widget:
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy.set_valign(Gtk.Align.CENTER)
        copy.set_hexpand(True)

        title = Gtk.Label(label="Lexi listening")
        title.get_style_context().add_class("lexi-title")
        title.set_xalign(0)
        copy.pack_start(title, False, False, 0)

        body = Gtk.Label(label='Opened from button / Ctrl+Space / "hey lexi" trigger.')
        body.get_style_context().add_class("lexi-body")
        body.set_xalign(0)
        body.set_line_wrap(True)
        body.set_max_width_chars(35)
        copy.pack_start(body, False, False, 0)

        mode = "Wayland layer-shell overlay" if self.runtime.can_use_layer_shell else "GTK fallback popup"
        detail = Gtk.Label(label=mode)
        detail.get_style_context().add_class("lexi-detail")
        detail.set_xalign(0)
        copy.pack_start(detail, False, False, 0)

        return copy

    def _build_close_button(self) -> Gtk.Widget:
        close = Gtk.Button(label="x")
        close.get_style_context().add_class("lexi-close")
        close.set_size_request(36, 36)
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda _button: self.close())
        return close

    def _build_avatar(self) -> Gtk.Widget:
        webkit_avatar = self._build_webkit_avatar()
        if webkit_avatar is not None:
            return webkit_avatar

        avatar = Gtk.Image.new_from_file(str(AVATAR_SVG))
        avatar.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        avatar.get_style_context().add_class("lexi-avatar")
        return avatar

    def _build_webkit_avatar(self) -> Gtk.Widget | None:
        if WebKit2 is None or not AVATAR_SVG.exists():
            return None

        svg_uri = escape(AVATAR_SVG.as_uri(), quote=True)
        html = f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <style>
              html,
              body {{
                width: 100%;
                height: 100%;
                margin: 0;
                overflow: hidden;
                background: #1e212a;
              }}

              img {{
                display: block;
                width: 100%;
                height: 100%;
                object-fit: contain;
              }}
            </style>
          </head>
          <body>
            <img src="{svg_uri}" alt="">
          </body>
        </html>
        """

        avatar = WebKit2.WebView()
        avatar.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        avatar.get_style_context().add_class("lexi-avatar")
        avatar.load_html(html, AVATAR_SVG.parent.as_uri())
        return avatar

    def _handle_popup_key(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _handle_delete(self, _window: Gtk.Window, _event: Gdk.Event) -> bool:
        self.close()
        return True

    def _position_fallback_window(self) -> None:
        if self.window is None or self.runtime.can_use_layer_shell:
            return

        screen = self.window.get_screen()
        monitor = screen.get_primary_monitor()
        geometry = screen.get_monitor_geometry(monitor)
        x = geometry.x + max(0, (geometry.width - POPUP_WIDTH) // 2)
        y = geometry.y + max(0, geometry.height - POPUP_HEIGHT - POPUP_MARGIN_BOTTOM)
        self.window.move(x, y)


class LexiController:
    def __init__(self, start_open: bool = False) -> None:
        self.runtime = get_runtime_info()
        self.popup = LexiPopup(self.runtime)

        self.window = Gtk.Window(title="Lexi Controller")
        self.window.set_name("lexi-controller")
        self.window.set_default_size(380, 210)
        self.window.set_resizable(False)
        self.window.connect("destroy", Gtk.main_quit)
        self.window.connect("key-press-event", self._handle_key)

        self.status = Gtk.Label()
        self.status.set_xalign(0)
        self.status.get_style_context().add_class("muted")

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text('Type "hey lexi" and press Enter')
        self.entry.connect("activate", self._handle_command)

        self._build_controller()

        if start_open:
            self.window.connect("show", lambda _window: self.popup.open())

    def _build_controller(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_border_width(18)
        self.window.add(root)

        title = Gtk.Label(label="Lexi popup controller")
        title.set_xalign(0)
        title.get_style_context().add_class("controller-title")
        root.pack_start(title, False, False, 0)

        hint = Gtk.Label(label='Click the button, press Ctrl+Space, or type "hey lexi".')
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("muted")
        root.pack_start(hint, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_button = Gtk.Button(label="Open Lexi")
        open_button.connect("clicked", lambda _button: self._open_popup())
        row.pack_start(open_button, False, False, 0)

        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda _button: self._close_popup())
        row.pack_start(close_button, False, False, 0)
        root.pack_start(row, False, False, 0)

        root.pack_start(self.entry, False, False, 0)
        root.pack_start(self.status, False, False, 0)

        backend = "layer-shell" if self.runtime.can_use_layer_shell else "GTK fallback"
        self.status.set_text(f"Idle. Backend: {backend}.")

    def _handle_command(self, entry: Gtk.Entry) -> None:
        phrase = entry.get_text().strip().lower()
        entry.set_text("")

        if phrase in {"hey lexi", "lexi", "open lexi"}:
            self._open_popup()
            return

        self.status.set_text('Try typing "hey lexi".')

    def _handle_key(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and event.keyval == Gdk.KEY_space:
            self._open_popup()
            return True

        if event.keyval == Gdk.KEY_Escape:
            self._close_popup()
            return True

        return False

    def _open_popup(self) -> None:
        self.popup.open()
        self.status.set_text("Lexi popup is open.")

    def _close_popup(self) -> None:
        self.popup.close()
        self.status.set_text("Idle.")

    def run(self) -> None:
        load_css()
        self.window.show_all()
        Gtk.main()


class LexiPopupOnly:
    def __init__(self) -> None:
        self.popup = LexiPopup(get_runtime_info(), on_close=Gtk.main_quit)

    def run(self) -> None:
        load_css()
        self.popup.open()
        Gtk.main()


def load_css() -> None:
    css = b"""
    window {
      background: #15171d;
      color: #f5f2eb;
      font: 11pt Sans;
    }

    #lexi-popup {
      background: transparent;
    }

    #lexi-controller {
      background: #15171d;
    }

    .controller-title {
      color: #f5f2eb;
      font: 700 14pt Sans;
    }

    .muted {
      color: #b8b5ad;
    }

    .lexi-card {
      background: rgba(30, 33, 42, 0.96);
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 8px;
      box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5);
    }

    .lexi-avatar {
      border-radius: 8px;
    }

    .lexi-title {
      color: #f5f2eb;
      font: 700 14pt Sans;
    }

    .lexi-body {
      color: #b8b5ad;
      font: 11pt Sans;
    }

    .lexi-detail {
      color: #65d6c8;
      font: 9pt Sans;
    }

    .lexi-close {
      background: #2a2d36;
      color: #f5f2eb;
      border-radius: 8px;
    }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Lexi popup prototype.")
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the popup only; useful for window-manager keybinds",
    )
    parser.add_argument("--controller", action="store_true", help="show the test controller window")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.controller:
        LexiController(start_open=args.open).run()
        return

    LexiPopupOnly().run()


if __name__ == "__main__":
    main()
