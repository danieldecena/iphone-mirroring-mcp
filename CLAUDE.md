# iphone-mirroring-mcp

MCP server that captures and drives the macOS iPhone Mirroring window only (screenshot, tap, swipe, type, OCR). Never handles passwords, PINs, or Face ID.

## Stack
Python, uv, `cliclick`. Needs Screen Recording + Accessibility for the launching app.

## Do not
- Click banking/wallet UI unattended.
- Drive any window other than iPhone Mirroring.
