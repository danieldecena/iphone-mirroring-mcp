# iphone-mirroring-mcp

An MCP server that lets an agent drive Apple's **iPhone Mirroring** window on a
Mac — see the phone, tap, swipe, type, jump around, open apps, and read the
screen with on-device OCR. It captures and controls *only* the iPhone Mirroring
window and brings it to the front before every action, so other apps stealing
focus (games, menu-bar utilities) can't derail a sequence.

> It never handles passwords, PINs, or Face ID — those stay with you. Treat the
> phone's banking/wallet apps with the same care you would in person.

## What you get

| Tool | Does |
|------|------|
| `iphone_screenshot` | PNG of the phone window. Coordinates for every other tool are pixels in this image. |
| `iphone_tap` | Tap at an (x, y) from the screenshot. |
| `iphone_swipe` | Swipe by explicit endpoints, or `direction: up/down/left/right` from center (scrolling). |
| `iphone_type` | Type into the focused field. |
| `iphone_press_key` | return, delete, space, escape, tab, arrows. |
| `iphone_home` | Home Screen (⌘1). |
| `iphone_app_switcher` | App Switcher (⌘2). |
| `iphone_open_app` | Spotlight (⌘3) → type name → open. |
| `iphone_read_screen` | OCR every line, each with a tap point — locate a control by its label. |
| `iphone_window_info` | Window id + bounds, for troubleshooting. |

## Requirements

- macOS with **iPhone Mirroring** set up and connected to your iPhone (it must be
  showing the phone, not the "Connect"/"Timed Out" splash).
- [`cliclick`](https://github.com/BlueM/cliclick): `brew install cliclick`
- [`uv`](https://docs.astral.sh/uv/): `brew install uv`
- The app that launches this server (Claude Desktop, Claude Code, …) needs, in
  **System Settings → Privacy & Security**:
  - **Screen Recording** (for the screenshots), and
  - **Accessibility** (for cliclick taps/typing and the ⌘1/2/3 shortcuts).

## Install

```bash
cd ~/Developer/iphone-mirroring-mcp
uv sync
```

## Run / register

Run directly:

```bash
uv run iphone-mirroring-mcp
```

Register with an MCP client (stdio). Example config entry:

```json
{
  "mcpServers": {
    "iphone-mirroring": {
      "command": "uv",
      "args": ["--directory", "/Users/home/Developer/iphone-mirroring-mcp", "run", "iphone-mirroring-mcp"]
    }
  }
}
```

For Claude Code:

```bash
claude mcp add iphone-mirroring -- uv --directory ~/Developer/iphone-mirroring-mcp run iphone-mirroring-mcp
```

## How coordinates work

`iphone_screenshot` returns a PNG. Read a location off that image in pixels and
pass those same pixels to `iphone_tap` / `iphone_swipe`. The server records the
window's on-screen bounds at capture time and scales pixel → screen point
automatically, so it's correct on Retina and external (1x) displays alike, and
regardless of which monitor or Space the window is on.

A typical loop:

1. `iphone_screenshot` — look at the phone.
2. `iphone_read_screen` (optional) — get labeled tap points via OCR.
3. `iphone_tap` / `iphone_type` / `iphone_swipe` — act.
4. `iphone_screenshot` — confirm the result.

## Notes & limits

- If a tap seems to miss, take a fresh screenshot first — the window may have
  moved. Coordinates always refer to the *latest* capture.
- OCR uses Apple's Vision framework via pyobjc — no tesseract needed.
- Displays positioned above/left of the main display can have negative global
  coordinates; `cliclick` is happiest with positive coordinates, so keep the
  iPhone Mirroring window on the main display or one to the right/below if you
  hit trouble.
