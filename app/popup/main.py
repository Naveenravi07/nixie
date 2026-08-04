#!/usr/bin/env python3
"""Small bottom-center Nixi popup for Wayland and X11."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell  # type: ignore  # noqa: E402
except (ImportError, ValueError):
    GtkLayerShell = None


POPUP_WIDTH = 460
POPUP_HEIGHT = 96
POPUP_MARGIN_BOTTOM = 24
AVATAR_SIZE = 72
REPO_ROOT = Path(__file__).resolve().parents[2]
AVATAR_SVG = REPO_ROOT / "assets" / "Q19WSHi0PH.svg"


@dataclass(frozen=True)
class RuntimeInfo:
    is_wayland: bool
    has_layer_shell: bool

    @property
    def use_layer_shell(self) -> bool:
        return self.is_wayland and self.has_layer_shell


def get_runtime_info() -> RuntimeInfo:
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    is_wayland = session_type == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    has_layer_shell = GtkLayerShell is not None
    if has_layer_shell and hasattr(GtkLayerShell, "is_supported"):
        has_layer_shell = bool(GtkLayerShell.is_supported())
    return RuntimeInfo(is_wayland=is_wayland, has_layer_shell=has_layer_shell)


class NixiPopup:
    def __init__(self) -> None:
        self.runtime = get_runtime_info()
        self.window = self._build_window()

    def open(self) -> None:
        self.window.show_all()
        # Layer-shell surfaces are not XDG toplevels and must not be presented.
        if not self.runtime.use_layer_shell:
            self.window.present()
            self._position_fallback_window()

    def close(self) -> None:
        self.window.hide()
        Gtk.main_quit()

    def _build_window(self) -> Gtk.Window:
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_name("nixi-popup")
        window.set_title("Nixi")
        window.set_decorated(False)
        window.set_resizable(False)
        window.set_keep_above(True)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.set_default_size(POPUP_WIDTH, POPUP_HEIGHT)
        window.connect("key-press-event", self._on_key_press)
        window.connect("delete-event", self._on_delete)

        if self.runtime.use_layer_shell:
            GtkLayerShell.init_for_window(window)
            GtkLayerShell.set_namespace(window, "nixi")
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(window, GtkLayerShell.Edge.BOTTOM, POPUP_MARGIN_BOTTOM)
            GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)

        window.add(self._build_content())
        return window

    def _build_content(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        card.get_style_context().add_class("nixi-card")
        card.set_size_request(POPUP_WIDTH, POPUP_HEIGHT)
        card.pack_start(self._build_avatar(), False, False, 14)
        card.pack_start(self._build_copy(), True, True, 0)
        card.pack_end(self._build_close_button(), False, False, 14)
        return card

    def _build_copy(self) -> Gtk.Widget:
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy.set_valign(Gtk.Align.CENTER)
        copy.set_hexpand(True)

        title = Gtk.Label(label="Nixi listening")
        title.get_style_context().add_class("nixi-title")
        title.set_xalign(0)
        copy.pack_start(title, False, False, 0)

        body = Gtk.Label(label='Opened by the "Hey Nixi" voice trigger.')
        body.get_style_context().add_class("nixi-body")
        body.set_xalign(0)
        body.set_line_wrap(True)
        copy.pack_start(body, False, False, 0)

        backend = "Wayland layer-shell" if self.runtime.use_layer_shell else "GTK fallback"
        detail = Gtk.Label(label=backend)
        detail.get_style_context().add_class("nixi-detail")
        detail.set_xalign(0)
        copy.pack_start(detail, False, False, 0)
        return copy

    def _build_close_button(self) -> Gtk.Widget:
        button = Gtk.Button(label="×")
        button.get_style_context().add_class("nixi-close")
        button.set_size_request(36, 36)
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _button: self.close())
        return button

    def _build_avatar(self) -> Gtk.Widget:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(AVATAR_SVG),
            AVATAR_SIZE,
            AVATAR_SIZE,
            True,
        )
        avatar = Gtk.Image.new_from_pixbuf(pixbuf)
        avatar.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        avatar.get_style_context().add_class("nixi-avatar")
        return avatar

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_delete(self, _window: Gtk.Window, _event: Gdk.Event) -> bool:
        self.close()
        return True

    def _position_fallback_window(self) -> None:
        display = self.window.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        x = geometry.x + max(0, (geometry.width - POPUP_WIDTH) // 2)
        y = geometry.y + max(0, geometry.height - POPUP_HEIGHT - POPUP_MARGIN_BOTTOM)
        self.window.move(x, y)


def load_css() -> None:
    css = b"""
    window { color: #f5f2eb; font: 11pt Sans; }
    #nixi-popup { background: transparent; }
    .nixi-card {
      background: rgba(30, 33, 42, 0.96);
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 8px;
      box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5);
    }
    .nixi-avatar { border-radius: 8px; }
    .nixi-title { color: #f5f2eb; font: 700 14pt Sans; }
    .nixi-body { color: #b8b5ad; font: 11pt Sans; }
    .nixi-detail { color: #65d6c8; font: 9pt Sans; }
    .nixi-close {
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


def main() -> None:
    load_css()
    popup = NixiPopup()
    popup.open()
    Gtk.main()


if __name__ == "__main__":
    main()
