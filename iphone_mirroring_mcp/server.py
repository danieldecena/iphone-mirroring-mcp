#!/usr/bin/env python3
"""iphone_mirroring_mcp — drive the macOS iPhone Mirroring window from an agent.

Exposes the phone shown in Apple's *iPhone Mirroring* app as a set of MCP tools:
see the screen, tap/swipe/type at screenshot coordinates, jump to the Home
screen / App Switcher / Spotlight, open an app by name, and read on-screen text
via on-device OCR. The window is always brought to the front first, so other
apps stealing focus can't derail a sequence of actions.

Requirements on the host machine:
  * macOS with iPhone Mirroring set up and connected to an iPhone.
  * `cliclick` (`brew install cliclick`).
  * The app that launches this server (Claude Desktop, Claude Code, etc.) must
    have **Accessibility** and **Screen Recording** permissions.
"""

from __future__ import annotations

import json
import tempfile
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP, Image

from . import input as phone_input
from . import ocr as phone_ocr
from . import window as phone_window

mcp = FastMCP("iphone_mirroring_mcp")


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _error(exc: Exception) -> str:
    """Uniform, actionable error text for tools that return strings."""
    if isinstance(exc, phone_window.WindowNotFoundError):
        return f"Error: {exc}"
    if isinstance(exc, phone_input.InputError):
        return f"Error: {exc}"
    return f"Error: {type(exc).__name__}: {exc}"


def _bounds_note(state: phone_window.CaptureState) -> str:
    return (
        f"screenshot is {state.image_w}x{state.image_h}px; give tap/swipe "
        f"coordinates in that pixel space (origin top-left)."
    )


# --------------------------------------------------------------------------- #
# Screenshot
# --------------------------------------------------------------------------- #
class ScreenshotInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    bring_to_front: bool = Field(
        default=True,
        description="Focus the iPhone Mirroring window before capturing so it "
        "isn't hidden behind another app or Space. Almost always leave True.",
    )


@mcp.tool(
    name="iphone_screenshot",
    annotations={
        "title": "Screenshot the iPhone Mirroring window",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def iphone_screenshot(params: ScreenshotInput) -> Image:
    """Capture the current iPhone Mirroring window and return it as a PNG image.

    Use this to see the phone before deciding where to tap. Coordinates in every
    other tool refer to the pixels of the image returned here (top-left origin).
    The capture is of the phone window only — other windows are never included —
    and it works regardless of which monitor or macOS Space the window is on.

    Returns:
        Image: PNG screenshot of the iPhone Mirroring window.
    """
    data, _state = phone_window.capture(front=params.bring_to_front)
    return Image(data=data, format="png")


# --------------------------------------------------------------------------- #
# Tap
# --------------------------------------------------------------------------- #
class TapInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    x: float = Field(..., description="X in screenshot pixels (from the left).", ge=0)
    y: float = Field(..., description="Y in screenshot pixels (from the top).", ge=0)


@mcp.tool(
    name="iphone_tap",
    annotations={
        "title": "Tap the phone screen",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def iphone_tap(params: TapInput) -> str:
    """Tap the phone at a point taken from the most recent screenshot.

    Coordinates are pixels in the image returned by ``iphone_screenshot`` (or any
    tool that returns a fresh capture). The window is focused first so the tap
    always lands on the phone. If you have not captured yet, one is taken
    automatically to establish the coordinate mapping.

    Args:
        params (TapInput): x and y in screenshot pixels.

    Returns:
        str: Confirmation including the resolved on-screen point.
    """
    try:
        gx, gy = phone_input.tap(params.x, params.y)
        return f"Tapped ({params.x:.0f}, {params.y:.0f}) → screen point ({gx}, {gy})."
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)


# --------------------------------------------------------------------------- #
# Swipe
# --------------------------------------------------------------------------- #
class SwipeDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class SwipeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    # Either give an explicit from/to, or a direction to swipe from the center.
    direction: Optional[SwipeDirection] = Field(
        default=None,
        description="Convenience: swipe up/down/left/right across the middle of "
        "the screen (good for scrolling). Ignored if x1/y1/x2/y2 are given.",
    )
    x1: Optional[float] = Field(default=None, description="Start X (screenshot px).", ge=0)
    y1: Optional[float] = Field(default=None, description="Start Y (screenshot px).", ge=0)
    x2: Optional[float] = Field(default=None, description="End X (screenshot px).", ge=0)
    y2: Optional[float] = Field(default=None, description="End Y (screenshot px).", ge=0)


@mcp.tool(
    name="iphone_swipe",
    annotations={
        "title": "Swipe / scroll the phone screen",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def iphone_swipe(params: SwipeInput) -> str:
    """Swipe on the phone, either by explicit endpoints or a simple direction.

    Provide either all of x1,y1,x2,y2 (screenshot pixels) for a precise gesture,
    or just ``direction`` to swipe across the center of the screen — handy for
    scrolling a list (swipe up to scroll down) or paging between screens.

    Args:
        params (SwipeInput): direction, or explicit x1,y1,x2,y2.

    Returns:
        str: Confirmation of the gesture performed.
    """
    try:
        state = phone_window.get_state()
        if None not in (params.x1, params.y1, params.x2, params.y2):
            (a, b) = phone_input.swipe(params.x1, params.y1, params.x2, params.y2)
            return f"Swiped {a} → {b} (screen points)."

        if params.direction is None:
            return (
                "Error: provide either a direction, or all of x1,y1,x2,y2 "
                "(screenshot pixels)."
            )

        # Center-based swipe. Move ~60% of the shorter axis so it registers.
        cx, cy = state.image_w / 2, state.image_h / 2
        dx = state.image_w * 0.3
        dy = state.image_h * 0.3
        vectors = {
            SwipeDirection.UP: (cx, cy + dy, cx, cy - dy),
            SwipeDirection.DOWN: (cx, cy - dy, cx, cy + dy),
            SwipeDirection.LEFT: (cx + dx, cy, cx - dx, cy),
            SwipeDirection.RIGHT: (cx - dx, cy, cx + dx, cy),
        }
        x1, y1, x2, y2 = vectors[params.direction]
        phone_input.swipe(x1, y1, x2, y2)
        return f"Swiped {params.direction.value} across the screen."
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# --------------------------------------------------------------------------- #
# Type + keys
# --------------------------------------------------------------------------- #
class TypeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., description="Text to type into the focused field.", min_length=1)


@mcp.tool(
    name="iphone_type",
    annotations={
        "title": "Type text on the phone",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def iphone_type(params: TypeInput) -> str:
    """Type a string into whatever text field is currently focused on the phone.

    Tap the field first, then call this. Does not press Return — use
    ``iphone_press_key`` with 'return' if you need to submit.

    Args:
        params (TypeInput): text to type.

    Returns:
        str: Confirmation.
    """
    try:
        phone_input.type_text(params.text)
        return f"Typed {len(params.text)} character(s)."
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


class KeyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    key: str = Field(
        ...,
        description="Key to press: return, delete, space, escape, tab, "
        "up, down, left, right.",
    )


@mcp.tool(
    name="iphone_press_key",
    annotations={
        "title": "Press a key on the phone",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def iphone_press_key(params: KeyInput) -> str:
    """Press a single key (return, delete, space, escape, tab, arrow keys).

    Args:
        params (KeyInput): key name.

    Returns:
        str: Confirmation.
    """
    try:
        phone_input.press_key(params.key)
        return f"Pressed '{params.key}'."
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# --------------------------------------------------------------------------- #
# Navigation shortcuts (Command+1/2/3)
# --------------------------------------------------------------------------- #
class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="iphone_home",
    annotations={
        "title": "Go to the phone Home Screen",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def iphone_home(params: EmptyInput) -> str:
    """Go to the iPhone Home Screen (Command+1 in iPhone Mirroring)."""
    try:
        phone_input.key_combo_command("1")
        return "Went to the Home Screen."
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(
    name="iphone_app_switcher",
    annotations={
        "title": "Open the phone App Switcher",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def iphone_app_switcher(params: EmptyInput) -> str:
    """Open the iPhone App Switcher (Command+2 in iPhone Mirroring)."""
    try:
        phone_input.key_combo_command("2")
        return "Opened the App Switcher."
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


class OpenAppInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(
        ...,
        description="App name as it appears on the phone (e.g. 'Amex', "
        "'Messages', 'Settings').",
        min_length=1,
        max_length=60,
    )


@mcp.tool(
    name="iphone_open_app",
    annotations={
        "title": "Open an app on the phone by name",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def iphone_open_app(params: OpenAppInput) -> str:
    """Open an app by name using Spotlight (Command+3, type, Return).

    This opens iPhone Spotlight, types the app name, and launches the top hit.
    After it returns, take a screenshot to confirm the app opened.

    Args:
        params (OpenAppInput): app name.

    Returns:
        str: Confirmation (verify with a screenshot).
    """
    try:
        phone_input.key_combo_command("3")  # Spotlight
        phone_input.type_text(params.name)
        phone_input.press_key("return")
        return f"Asked Spotlight to open '{params.name}'. Screenshot to confirm."
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# --------------------------------------------------------------------------- #
# OCR / read screen
# --------------------------------------------------------------------------- #
class ReadScreenInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    contains: Optional[str] = Field(
        default=None,
        description="Optional case-insensitive filter — only return lines whose "
        "text contains this substring (e.g. 'Rewards Checking').",
    )


@mcp.tool(
    name="iphone_read_screen",
    annotations={
        "title": "Read on-screen text (OCR) with tap points",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def iphone_read_screen(params: ReadScreenInput) -> str:
    """OCR the phone screen and return each text line with a tap point.

    Runs Apple's on-device text recognition on a fresh capture. Each result has
    the recognized text and the screenshot-pixel center (px, py) — feed those to
    ``iphone_tap`` to press a control you found by its label instead of guessing
    coordinates.

    Args:
        params (ReadScreenInput): optional ``contains`` substring filter.

    Returns:
        str: JSON with schema:
        {
          "screenshot": {"width": int, "height": int},
          "count": int,
          "lines": [
            {"text": str, "confidence": float, "px": int, "py": int}
          ]
        }
        (px, py) are screenshot pixels — pass them straight to iphone_tap.
    """
    try:
        data, state = phone_window.capture(front=True)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            items = phone_ocr.recognize(tmp_path, state)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        if params.contains:
            needle = params.contains.lower()
            items = [it for it in items if needle in it.text.lower()]

        payload = {
            "screenshot": {"width": state.image_w, "height": state.image_h},
            "count": len(items),
            "lines": [
                {"text": it.text, "confidence": it.confidence, "px": it.px, "py": it.py}
                for it in items
            ],
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# --------------------------------------------------------------------------- #
# Status / debug
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="iphone_window_info",
    annotations={
        "title": "Report the iPhone Mirroring window geometry",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def iphone_window_info(params: EmptyInput) -> str:
    """Return the current window's id and on-screen bounds (for troubleshooting).

    Useful to confirm the server can see the iPhone Mirroring window before
    trying to tap. Returns an error string if the window isn't found.
    """
    try:
        info = phone_window.find_window()
        return json.dumps(
            {
                "window_id": info.window_id,
                "x": info.x,
                "y": info.y,
                "width": info.width,
                "height": info.height,
            },
            indent=2,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
