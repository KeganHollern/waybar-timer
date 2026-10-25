#!/usr/bin/env python3
"""A small Wayland confirmation below the timer's click position."""

import ctypes.util
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

from settings import popup_settings
from waybar_timer import Store, cancel_if_current, clock_text, module_data, remaining

ROOT = Path(__file__).resolve().parent
CARD_WIDTH = 244


def timer_anchor(bar, state, settings):
    """Measure a centered-clock layout only when the user supplies its settings."""
    import gi

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    font = Pango.FontDescription.from_string(settings["font"])
    layout = Pango.Layout.new(PangoCairo.FontMap.get_default().create_context())
    layout.set_font_description(font)
    clock = datetime.datetime.now().strftime(settings["clock_format"])

    def width(value):
        layout.set_text(value, -1)
        return layout.get_pixel_size().width

    clock_width = width(clock) + settings["clock_extra_width"]
    timer_width = width(module_data(state)["text"]) + settings["timer_extra_width"]
    return bar["x"] + bar["w"] / 2 - clock_width / 2 - timer_width / 2


def hyprland_position(state=None):
    """Use logical monitor coordinates and the actual Waybar layer bounds."""
    try:

        def query(name):
            result = subprocess.run(
                ["hyprctl", "-j", name], check=True, capture_output=True, text=True, timeout=2
            )
            return json.loads(result.stdout)

        cursor = query("cursorpos")
        monitors = query("monitors")
        monitor = next(
            item
            for item in monitors
            if item["x"] <= cursor["x"] < item["x"] + item["width"] / item["scale"]
            and item["y"] <= cursor["y"] < item["y"] + item["height"] / item["scale"]
        )
        width = monitor["width"] / monitor["scale"]
        bars = [
            layer
            for level in query("layers").get(monitor["name"], {}).get("levels", {}).values()
            for layer in level
            if layer.get("namespace") == "waybar"
        ]
        bar = next(
            (
                layer
                for layer in bars
                if layer["x"] <= cursor["x"] < layer["x"] + layer["w"]
                and layer["y"] <= cursor["y"] < layer["y"] + layer["h"]
            ),
            None,
        )
        if bar is None and bars:
            bar = min(bars, key=lambda layer: abs(cursor["y"] - layer["y"]))
        anchor = cursor["x"]
        settings = popup_settings()
        if state is not None and bar is not None and settings["anchor"] == "centered-clock":
            try:
                anchor = timer_anchor(bar, state, settings)
            except (OSError, ValueError, KeyError, AttributeError, ImportError):
                pass
        left = round(max(8, min(width - CARD_WIDTH - 8, anchor - monitor["x"] - CARD_WIDTH / 2)))
        top = round(bar["y"] + bar["h"] - monitor["y"] + 8) if bar else 45
        return {"monitor": monitor["name"], "left": left, "top": top}
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
        return None


def preload_layer_shell():
    # GTK must see layer-shell before libwayland-client.
    if os.environ.get("WAYBAR_TIMER_LAYER_SHELL") == "1":
        return
    library = ctypes.util.find_library("gtk4-layer-shell")
    if not library:
        raise RuntimeError("The timer popup needs gtk4-layer-shell.")
    environment = os.environ.copy()
    previous = environment.get("LD_PRELOAD")
    environment["LD_PRELOAD"] = library + (":" + previous if previous else "")
    environment["WAYBAR_TIMER_LAYER_SHELL"] = "1"
    os.execve(
        sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], environment
    )


def run_popup(timer_id, position=None):
    preload_layer_shell()
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell, Pango

    class Confirmation(Gtk.Application):
        def __init__(self):
            super().__init__(
                application_id="io.github.waybar_timer.confirm",
                flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
            )
            self.window = None
            self.timer_id = None
            self.store = Store()
            self.tick = None

        def do_startup(self):
            Gtk.Application.do_startup(self)
            dismiss = Gio.SimpleAction.new("dismiss", None)
            dismiss.connect("activate", lambda *_: self.close_popup())
            self.add_action(dismiss)
            end = Gio.SimpleAction.new("end-timer", None)
            end.connect("activate", self.confirm)
            self.add_action(end)
            provider = Gtk.CssProvider()
            provider.load_from_path(str(ROOT / "assets/popup.css"))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        def do_command_line(self, command):
            arguments = command.get_arguments()
            requested = arguments[1]
            if isinstance(requested, bytes):
                requested = requested.decode()
            anchor = arguments[2] if len(arguments) > 2 else "null"
            if isinstance(anchor, bytes):
                anchor = anchor.decode()
            if self.window is not None:
                same_timer = requested == self.timer_id
                self.close_popup()
                if same_timer:
                    return 0
            with self.store.lock():
                state = self.store.read()
            if (
                state.get("id") != requested
                or state["status"] != "running"
                or remaining(state) == 0
            ):
                return 0
            self.timer_id = requested
            self.build(state, json.loads(anchor))
            self.window.present()
            GLib.idle_add(self.focus_once)
            self.tick = GLib.timeout_add(250, self.update_timer)
            return 0

        def focus_once(self):
            if self.window is not None:
                self.focus_target.grab_focus()
            return GLib.SOURCE_REMOVE

        def close_popup(self):
            if self.tick is not None:
                GLib.source_remove(self.tick)
                self.tick = None
            if self.window is not None:
                window, self.window = self.window, None
                window.destroy()

        def update_timer(self):
            try:
                with self.store.lock():
                    state = self.store.read()
            except (OSError, ValueError):
                self.tick = None
                self.close_popup()
                return GLib.SOURCE_REMOVE
            if (
                state.get("id") != self.timer_id
                or state["status"] != "running"
                or remaining(state) == 0
            ):
                self.tick = None
                self.close_popup()
                return GLib.SOURCE_REMOVE
            self.detail.set_text(
                f"{state['label'] or 'Timer'} · {clock_text(remaining(state))} left"
            )
            return GLib.SOURCE_CONTINUE

        def confirm(self, *_args):
            if self.window is not None:
                cancel_if_current(self.store, self.timer_id)
            self.close_popup()

        def key_pressed(self, _controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                self.close_popup()
                return True
            return False

        def build(self, state, position):
            window = Gtk.ApplicationWindow(application=self)
            self.window = window
            window.set_name("timer-confirm-overlay")
            window.set_title("End timer?")
            Gtk4LayerShell.init_for_window(window)
            Gtk4LayerShell.set_namespace(window, "waybar-timer-confirm")
            Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
            # Cover the full monitor instead of adding Waybar's reserved height twice.
            Gtk4LayerShell.set_exclusive_zone(window, -1)
            Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
            for edge in (
                Gtk4LayerShell.Edge.TOP,
                Gtk4LayerShell.Edge.RIGHT,
                Gtk4LayerShell.Edge.BOTTOM,
                Gtk4LayerShell.Edge.LEFT,
            ):
                Gtk4LayerShell.set_anchor(window, edge, True)

            monitors = Gdk.Display.get_default().get_monitors()
            if position:
                for index in range(monitors.get_n_items()):
                    monitor = monitors.get_item(index)
                    if monitor.get_connector() == position["monitor"]:
                        Gtk4LayerShell.set_monitor(window, monitor)
                        break

            overlay = Gtk.Overlay()
            backdrop = Gtk.Box()
            backdrop.set_hexpand(True)
            backdrop.set_vexpand(True)
            gesture = Gtk.GestureClick()
            gesture.set_button(0)
            gesture.connect("pressed", lambda *_: self.close_popup())
            backdrop.add_controller(gesture)
            overlay.set_child(backdrop)

            frame = Gtk.Box()
            frame.set_name("timer-confirm-frame")
            frame.set_size_request(CARD_WIDTH, -1)
            frame.set_halign(Gtk.Align.START if position else Gtk.Align.CENTER)
            frame.set_valign(Gtk.Align.START)
            frame.set_margin_start(position["left"] if position else 0)
            frame.set_margin_top(position["top"] if position else 45)
            frame.set_focusable(True)
            self.focus_target = frame
            overlay.add_overlay(frame)
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.set_name("timer-confirm-card")
            frame.append(card)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title = Gtk.Label(label="End timer?", xalign=0)
            title.add_css_class("timer-confirm-title")
            title.set_hexpand(True)
            header.append(title)
            card.append(header)
            self.detail = Gtk.Label(xalign=0)
            self.detail.set_max_width_chars(26)
            self.detail.set_ellipsize(Pango.EllipsizeMode.END)
            self.detail.add_css_class("timer-confirm-detail")
            self.detail.set_text(
                f"{state['label'] or 'Timer'} · {clock_text(remaining(state))} left"
            )
            card.append(self.detail)
            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            buttons.set_halign(Gtk.Align.END)
            buttons.add_css_class("timer-confirm-buttons")
            cancel = Gtk.Button(label="End timer")
            cancel.add_css_class("timer-cancel")
            cancel.set_action_name("app.end-timer")
            buttons.append(cancel)
            card.append(buttons)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self.key_pressed)
            window.add_controller(keys)
            window.set_child(overlay)

    return Confirmation().run([str(Path(__file__).resolve()), timer_id, json.dumps(position)])


if __name__ == "__main__":
    try:
        if len(sys.argv) not in (2, 3):
            raise ValueError("Use waybar-timer click to open the confirmation.")
        position = json.loads(sys.argv[2]) if len(sys.argv) == 3 else hyprland_position()
        sys.exit(run_popup(sys.argv[1], position))
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"waybar-timer popup: {error}", file=sys.stderr)
        sys.exit(1)
