"""Mouse/keyboard input into the iPhone Mirroring window.

Input is delivered with ``cliclick`` (https://github.com/BlueM/cliclick), a tiny
CLI that posts real HID events — reliable for click, drag and typing. We always
bring the window to the front first, because macOS routes events to the frontmost
app; that also side-steps the focus-stealing problems you hit doing this by hand.

All screen coordinates handed to cliclick are global points with a top-left
origin, which is exactly the coordinate space ``window.CaptureState`` maps into.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from typing import Iterable

from . import window

_CLICLICK = shutil.which("cliclick") or "/opt/homebrew/bin/cliclick"


class InputError(RuntimeError):
    pass


@contextlib.contextmanager
def _focused():
    """Front the phone window for the duration of an input action, then undo it.

    Taps cannot avoid taking focus — cliclick posts real HID events and macOS
    routes those to the frontmost app — but they can give it straight back. We
    also park the pointer where the user left it, since a real cursor move is
    the other half of what makes this disruptive on a second display.
    """
    prior_app = window.frontmost_process()
    prior_cursor = _cursor_position()
    window.bring_to_front()
    try:
        yield
    finally:
        if prior_cursor is not None:
            _run_cliclick([f"m:{prior_cursor[0]},{prior_cursor[1]}"])
        window.restore_frontmost(prior_app)


def _cursor_position() -> tuple[int, int] | None:
    """Current pointer location, or None if cliclick can't report it."""
    result = subprocess.run([_CLICLICK, "p"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    # cliclick prints "123,456".
    parts = result.stdout.strip().split(",")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _run_cliclick(args: Iterable[str]) -> None:
    if not shutil.which(_CLICLICK) and not _CLICLICK.startswith("/"):
        raise InputError(
            "cliclick not found. Install it with `brew install cliclick` and "
            "grant the host app Accessibility permission (System Settings > "
            "Privacy & Security > Accessibility)."
        )
    result = subprocess.run([_CLICLICK, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise InputError(
            "cliclick failed — the host app running this MCP likely needs "
            "Accessibility permission (System Settings > Privacy & Security > "
            f"Accessibility). Detail: {result.stderr.strip() or result.stdout.strip()}"
        )


def _screen_point(img_x: float, img_y: float) -> tuple[int, int]:
    """Map a screenshot-pixel target to a live on-screen point.

    The iPhone Mirroring window can move or change backing scale between a
    capture and the follow-up tap (e.g. when it hops between a Retina built-in
    display and an external 1x monitor). Mapping through absolute capture bounds
    would then miss. Instead we treat the target as a *fraction* of the captured
    image and re-project it onto the window's CURRENT bounds, read fresh here.
    The on-screen layout is identical across scales, so the fraction is stable.
    """
    state = window.get_state()
    fx = img_x / state.image_w if state.image_w else 0.0
    fy = img_y / state.image_h if state.image_h else 0.0
    fx = min(max(fx, 0.0), 1.0)
    fy = min(max(fy, 0.0), 1.0)
    info = window.find_window()  # current bounds, after bring_to_front
    gx = info.x + fx * info.width
    gy = info.y + fy * info.height
    return round(gx), round(gy)


def tap(img_x: float, img_y: float) -> tuple[int, int]:
    """Tap at a point given in *screenshot pixel* coordinates."""
    with _focused():
        gx, gy = _screen_point(img_x, img_y)
        _run_cliclick([f"c:{gx},{gy}"])
    return gx, gy


def swipe(
    img_x1: float,
    img_y1: float,
    img_x2: float,
    img_y2: float,
    steps: int = 8,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Drag/swipe from one screenshot-pixel point to another.

    A swipe is a press, a few intermediate moves (so iOS reads it as a gesture
    rather than a flick), then a release.
    """
    with _focused():
        x1, y1 = _screen_point(img_x1, img_y1)
        x2, y2 = _screen_point(img_x2, img_y2)

        args = [f"m:{x1},{y1}", f"dd:{x1},{y1}"]
        steps = max(2, steps)
        for i in range(1, steps + 1):
            t = i / steps
            mx = round(x1 + (x2 - x1) * t)
            my = round(y1 + (y2 - y1) * t)
            args.append(f"m:{mx},{my}")
        args.append(f"du:{x2},{y2}")
        _run_cliclick(args)
    return (x1, y1), (x2, y2)


def type_text(text: str) -> None:
    """Type a string into whatever field is focused on the phone."""
    with _focused():
        # cliclick's t: types the literal string (handles spaces/punctuation).
        _run_cliclick([f"t:{text}"])


# cliclick key names for the presses we expose. Kept small and explicit so the
# tool schema can enumerate valid values.
_KEY_MAP = {
    "return": "return",
    "enter": "return",
    "delete": "delete",
    "backspace": "delete",
    "space": "space",
    "escape": "esc",
    "esc": "esc",
    "tab": "tab",
    "up": "arrow-up",
    "down": "arrow-down",
    "left": "arrow-left",
    "right": "arrow-right",
}


def press_key(key: str) -> None:
    name = _KEY_MAP.get(key.lower().strip())
    if name is None:
        raise InputError(
            f"Unknown key '{key}'. Supported: {', '.join(sorted(_KEY_MAP))}."
        )
    with _focused():
        _run_cliclick([f"kp:{name}"])


def key_combo_command(digit: str) -> None:
    """Press Command+<digit> via System Events (iPhone Mirroring nav shortcuts).

    Command+1 = Home Screen, Command+2 = App Switcher, Command+3 = Spotlight.
    We use osascript keystroke rather than cliclick because modifier chords are
    cleaner to express there.
    """
    with _focused():
        script = f'tell application "System Events" to keystroke "{digit}" using command down'
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise InputError(
                "osascript keystroke failed — grant the host app Accessibility "
                f"permission. Detail: {result.stderr.strip()}"
            )
        time.sleep(0.4)
