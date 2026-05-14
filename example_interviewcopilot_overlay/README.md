# Interview Copilot Overlay

Desktop overlay version copied from `example_interviewcopilot_v2`.

This version keeps the Python STT/LLM backend and adds an Electron shell with:

- a normal control window for setup, profiles, audio and full responses;
- an always-on-top overlay window for meeting/interview use;
- global shortcuts for showing the overlay, pass-through mode and lock mode;
- automatic backend startup on WebSocket port `8015`.

## Setup

1. Install Node dependencies once:

```powershell
cd C:\wamp64\www\RealtimeSTT\example_interviewcopilot_overlay
npm install
```

2. Start the desktop app:

```powershell
npm start
```

Electron starts `python server.py` automatically. It first tries:

```text
C:\wamp64\www\RealtimeSTT\venv\Scripts\python.exe
```

If that file is missing, it falls back to `python` from PATH.

## Windows Shortcuts

- `Ctrl+Shift+O`: show/hide overlay.
- `Ctrl+Shift+P`: toggle click pass-through.
- `Ctrl+Shift+C`: focus/open control window.
- `Ctrl+Shift+L`: lock/unlock overlay movement and resize.

When pass-through or lock mode is enabled, a small always-clickable control
strip appears near the overlay so those modes can be turned off with the mouse.
The main setup dialog also includes an overlay transparency slider.

## Manual Backend Debug

For backend-only debugging:

```powershell
cd C:\wamp64\www\RealtimeSTT
venv\Scripts\activate
cd example_interviewcopilot_overlay
$env:INTERVIEW_COPILOT_WS_PORT = "8015"
python server.py
```

Then open either:

```text
http://localhost/RealtimeSTT/example_interviewcopilot_overlay/
```

or the Electron app with `npm start`.

## Defaults

- WebSocket: `ws://localhost:8015`
- Fast answer model: `openai/gpt-oss-20b`
- Improve/quality model: `llama-3.3-70b-versatile`
- Optional setup model: `gpt-4.1-mini`

## Notes

This folder was intentionally copied with local runtime files such as `.env`,
SQLite data, logs and caches. The `.gitignore` keeps those files out of Git
unless explicitly changed later.
