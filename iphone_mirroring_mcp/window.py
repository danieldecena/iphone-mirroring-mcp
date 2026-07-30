"""Locating and capturing the iPhone Mirroring window.

The iPhone Mirroring app (bundle id ``com.apple.ScreenContinuity``) renders the
phone in a single window. We find that window via the CoreGraphics window list,
which works no matter which Space or monitor it is on, and capture just that
window with ``screencapture -l <windowid>`` so surrounding apps never leak in.

Coordinate model
----------------
Everything an agent sees is the captured PNG. Its pixels do not line up 1:1 with
on-screen points (a Retina display captures at 2x). So after each capture we
record the window's global bounds (in points, top-left origin — the same system
``cliclick`` uses) alongside the captured image's pixel size. Mapping an image
pixel back to a global click point is then just::

    gx = bounds.x + img_px_x / scale_x
    gy = bounds.y + img_px_y / scale_y

where ``scale = image_pixels / bounds_points``. We compute the scale from the
*actual* captured image, so it is correct on Retina and non-Retina displays
alike without hardcoding 2x.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import Quartz

# The iPhone Mirroring process/app is exposed under a few names depending on
# macOS version; match generously but only accept a real, on-screen window.
_OWNER_HINTS = ("iPhone Mirroring", "ScreenContinuity")
APP_PROCESS_NAME = "iPhone Mirroring"

# Where we stash the geometry of the most recent capture so that a later
# tap/swipe call (a separate tool invocation) can map image pixels to the
# screen. The server process is long-lived so an in-memory copy is the fast
# path; the file is a durable fallback.
_STATE_PATH = Path(tempfile.gettempdir()) / "iphone_mirroring_mcp_state.json"
_STATE_MEM: "Optional[CaptureState]" = None


class WindowNotFoundError(RuntimeError):
    """Raised when no iPhone Mirroring window is currently on screen."""


@dataclass
class WindowInfo:
    window_id: int
    x: float
    y: float
    width: float
    height: float


@dataclass
class CaptureState:
    """Geometry recorded at capture time, used to map image px -> screen pt."""

    window_id: int
    bounds_x: float
    bounds_y: float
    bounds_w: float
    bounds_h: float
    image_w: int
    image_h: int
    captured_at: float

    @property
    def scale_x(self) -> float:
        return self.image_w / self.bounds_w if self.bounds_w else 1.0

    @property
    def scale_y(self) -> float:
        return self.image_h / self.bounds_h if self.bounds_h else 1.0

    def image_px_to_screen_pt(self, px: float, py: float) -> tuple[float, float]:
        gx = self.bounds_x + px / self.scale_x
        gy = self.bounds_y + py / self.scale_y
        return gx, gy


def find_window() -> WindowInfo:
    """Return the current iPhone Mirroring window, or raise WindowNotFoundError.

    We scan on-screen windows, skip the tiny helper/menu windows, and pick the
    largest matching window (the phone canvas).
    """
    options = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements
    )
    windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []

    best: Optional[WindowInfo] = None
    best_area = 0.0
    for w in windows:
        owner = w.get("kCGWindowOwnerName", "") or ""
        if not any(hint in owner for hint in _OWNER_HINTS):
            continue
        # Layer 0 is a normal application window; menus/tooltips sit above it.
        if int(w.get("kCGWindowLayer", 0)) != 0:
            continue
        bounds = w.get("kCGWindowBounds") or {}
        width = float(bounds.get("Width", 0))
        height = float(bounds.get("Height", 0))
        # The phone canvas is tall and reasonably large; ignore stray chrome.
        if width < 120 or height < 240:
            continue
        area = width * height
        if area > best_area:
            best_area = area
            best = WindowInfo(
                window_id=int(w.get("kCGWindowNumber")),
                x=float(bounds.get("X", 0)),
                y=float(bounds.get("Y", 0)),
                width=width,
                height=height,
            )

    if best is None:
        raise WindowNotFoundError(
            "No iPhone Mirroring window found. Open the iPhone Mirroring app and "
            "make sure it has connected to your iPhone (it must be showing the "
            "phone, not the 'Connect'/'Timed Out' splash on a hidden Space)."
        )
    return best


def bring_to_front() -> None:
    """Focus the iPhone Mirroring app so input events land on it.

    Best-effort: if the app is not running this is a no-op and the caller's next
    step will surface a clear WindowNotFoundError.
    """
    script = (
        'tell application "System Events" to '
        f'set frontmost of (first process whose name is "{APP_PROCESS_NAME}") to true'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    # Small settle so the window is actually key before we click/type.
    time.sleep(0.15)


def frontmost_process() -> Optional[str]:
    """Name of the app that currently owns focus, or None if it can't be read."""
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get name of '
            "first process whose frontmost is true",
        ],
        capture_output=True,
        text=True,
    )
    name = result.stdout.strip()
    return name or None


def restore_frontmost(name: Optional[str]) -> None:
    """Hand focus back to `name`, unless it was already iPhone Mirroring.

    Best-effort by design: if the app quit or was renamed mid-action there is
    nothing sensible to fall back to, and failing here would mask the caller's
    actual result.
    """
    if not name or name == APP_PROCESS_NAME:
        return
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to set frontmost of '
            f'(first process whose name is "{name}") to true',
        ],
        capture_output=True,
        text=True,
    )


def capture(front: bool = True) -> tuple[bytes, CaptureState]:
    """Capture the iPhone Mirroring window as PNG bytes and record its geometry.

    Args:
        front: bring the window to the front before capturing. Required in
            practice, not merely recommended: VERIFIED 2026-07-30 that while
            iPhone Mirroring is backgrounded the process keeps running but its
            window leaves the Quartz window list entirely, so find_window()
            raises and there is nothing to image. Focus is handed back below.

    Returns:
        (png_bytes, CaptureState)
    """
    prior_app = frontmost_process() if front else None
    if front:
        bring_to_front()

    # A window that is mid-transition between Spaces/monitors can briefly refuse
    # to image ("could not create image from window"). Re-find and retry a few
    # times before surfacing a hard error, so a window on another Space (e.g. a
    # second display) captures reliably once it settles.
    last_err = ""
    data: Optional[bytes] = None
    info = find_window()
    for attempt in range(4):
        info = find_window()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            out_path = tmp.name
        try:
            # -x: silent, -o: omit window shadow so bounds match the image,
            # -l <id>: capture exactly this window.
            result = subprocess.run(
                ["screencapture", "-x", "-o", "-l", str(info.window_id), out_path],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and Path(out_path).stat().st_size > 0:
                data = Path(out_path).read_bytes()
                break
            last_err = result.stderr.strip() or "empty capture"
        finally:
            Path(out_path).unlink(missing_ok=True)
        if front:
            bring_to_front()
        time.sleep(0.4)

    # Hand focus back before returning OR raising — the pixels are already in
    # memory, so nothing below needs the window to still be front. Placing this
    # ahead of the error check covers both exits with one call.
    restore_frontmost(prior_app)

    if data is None:
        raise RuntimeError(
            "screencapture failed after retries. If this persists, the host app "
            "running this MCP needs Screen Recording permission (System Settings "
            f"> Privacy & Security > Screen Recording). Detail: {last_err}"
        )

    image_w, image_h = _png_size(data)
    state = CaptureState(
        window_id=info.window_id,
        bounds_x=info.x,
        bounds_y=info.y,
        bounds_w=info.width,
        bounds_h=info.height,
        image_w=image_w,
        image_h=image_h,
        captured_at=time.time(),
    )
    _save_state(state)
    return data, state


def get_state(refresh_if_missing: bool = True) -> CaptureState:
    """Return the geometry of the most recent capture.

    If nothing has been captured yet (e.g. the agent taps before it screenshots)
    we transparently take a capture so coordinate mapping still works.
    """
    global _STATE_MEM
    if _STATE_MEM is not None:
        return _STATE_MEM
    if _STATE_PATH.exists():
        try:
            _STATE_MEM = CaptureState(**json.loads(_STATE_PATH.read_text()))
            return _STATE_MEM
        except Exception:
            pass
    if refresh_if_missing:
        _, state = capture(front=True)
        return state
    raise WindowNotFoundError("No capture state yet — call iphone_screenshot first.")


def _save_state(state: CaptureState) -> None:
    global _STATE_MEM
    _STATE_MEM = state
    try:
        _STATE_PATH.write_text(json.dumps(asdict(state)))
    except Exception:
        pass


def _png_size(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG header without pulling in Pillow."""
    # PNG: 8-byte signature, then IHDR chunk (length, 'IHDR', width, height...).
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Capture did not produce a valid PNG.")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height
