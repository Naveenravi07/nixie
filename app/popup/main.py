#!/usr/bin/env python3
"""Small bottom-center Nixi popup for Wayland and X11."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from math import pi
from dataclasses import dataclass
from pathlib import Path

import gi

try:
    gi.require_foreign("cairo")
except ImportError as exc:
    raise SystemExit(
        "nixi-popup needs the system PyCairo bindings. "
        "Install the distro package, for example: sudo pacman -S python-cairo"
    ) from exc
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell  # type: ignore  # noqa: E402
except (ImportError, ValueError):
    GtkLayerShell = None


POPUP_WIDTH = 250
POPUP_HEIGHT = 96
POPUP_MARGIN_BOTTOM = 24
POPUP_RADIUS = 16
REPO_ROOT = Path(__file__).resolve().parents[2]
AVATAR_SVG = REPO_ROOT / "assets" / "Q19WSHi0PH.svg"
AVATAR_VIDEO = REPO_ROOT / "assets" / "110371-688648556_medium.mp4"


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
        self.video: LoopingVideo | None = None
        self.window = self._build_window()

    def open(self) -> None:
        self.window.show_all()
        # Layer-shell surfaces are not XDG toplevels and must not be presented.
        if not self.runtime.use_layer_shell:
            self.window.present()
            self._position_fallback_window()

    def close(self) -> None:
        if self.video is not None:
            self.video.stop()
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
        return self._build_video()

    def _build_video(self) -> Gtk.Widget:
        if AVATAR_VIDEO.exists():
            try:
                self.video = LoopingVideo(
                    AVATAR_VIDEO,
                    POPUP_WIDTH,
                    POPUP_HEIGHT,
                )
                return self.video
            except (FileNotFoundError, OSError):
                self.video = None

        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(AVATAR_SVG),
            POPUP_WIDTH,
            POPUP_HEIGHT,
            False,
        )
        fallback = Gtk.Image.new_from_pixbuf(pixbuf)
        fallback.set_size_request(POPUP_WIDTH, POPUP_HEIGHT)
        return fallback

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


class LoopingVideo(Gtk.DrawingArea):
    """Render a silent, rounded, looping MP4 using FFmpeg."""

    def __init__(self, path: Path, width: int, height: int) -> None:
        super().__init__()
        self.width = width
        self.height = height
        self.frame_bytes = width * height * 3
        self.stopped = threading.Event()
        self.frame_lock = threading.Lock()
        self.latest_frame: bytes | None = None
        self.update_pending = False
        self.pixbuf: GdkPixbuf.Pixbuf | None = None
        self.set_size_request(width, height)
        self.connect("draw", self._on_draw)

        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-stream_loop",
            "-1",
            "-i",
            str(path),
            "-an",
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            ),
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.thread = threading.Thread(
            target=self._read_frames,
            name="nixi-popup-video",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self.process.poll() is None:
            self.process.terminate()
        self.thread.join(timeout=1)
        if self.process.poll() is None:
            self.process.kill()
        with self.frame_lock:
            self.latest_frame = None

    def _read_frames(self) -> None:
        assert self.process.stdout is not None
        pending = bytearray()
        while not self.stopped.is_set():
            chunk = self.process.stdout.read(self.frame_bytes - len(pending))
            if not chunk:
                return
            pending.extend(chunk)
            if len(pending) != self.frame_bytes:
                continue
            frame = bytes(pending)
            pending.clear()
            with self.frame_lock:
                self.latest_frame = frame
                if self.update_pending:
                    continue
                self.update_pending = True
            GLib.idle_add(self._display_latest_frame)

    def _display_latest_frame(self) -> bool:
        with self.frame_lock:
            frame = self.latest_frame
            self.latest_frame = None
            self.update_pending = False
        if self.stopped.is_set() or frame is None:
            return GLib.SOURCE_REMOVE
        pixels = GLib.Bytes.new(frame)
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            pixels,
            GdkPixbuf.Colorspace.RGB,
            False,
            8,
            self.width,
            self.height,
            self.width * 3,
        )
        self.pixbuf = pixbuf
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _on_draw(self, _widget: Gtk.Widget, context: object) -> bool:
        if self.pixbuf is None:
            return False

        radius = min(POPUP_RADIUS, self.width / 2, self.height / 2)
        self._rounded_rectangle(context, 0.5, 0.5, self.width - 1, self.height - 1, radius)
        context.save()
        context.clip()
        Gdk.cairo_set_source_pixbuf(context, self.pixbuf, 0, 0)
        context.paint()
        context.restore()

        self._rounded_rectangle(context, 0.5, 0.5, self.width - 1, self.height - 1, radius)
        context.set_source_rgba(1, 1, 1, 0.22)
        context.set_line_width(1)
        context.stroke()
        return False

    @staticmethod
    def _rounded_rectangle(
        context: object,
        x: float,
        y: float,
        width: float,
        height: float,
        radius: float,
    ) -> None:
        context.new_sub_path()
        context.arc(x + width - radius, y + radius, radius, -pi / 2, 0)
        context.arc(x + width - radius, y + height - radius, radius, 0, pi / 2)
        context.arc(x + radius, y + height - radius, radius, pi / 2, pi)
        context.arc(x + radius, y + radius, radius, pi, 3 * pi / 2)
        context.close_path()


def load_css() -> None:
    css = b"""
    window { color: #f5f2eb; font: 11pt Sans; }
    #nixi-popup { background: transparent; }
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
    signal.signal(
        signal.SIGTERM,
        lambda _signum, _frame: GLib.idle_add(popup.close),
    )
    popup.open()
    try:
        Gtk.main()
    finally:
        if popup.video is not None:
            popup.video.stop()


if __name__ == "__main__":
    main()
