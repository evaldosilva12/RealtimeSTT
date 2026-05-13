# Meeting Copilot V2

Optimized copy of `example_meetingcopilot`. The original folder is preserved as the working baseline.

## Setup

1. Copy `.env.example` to `.env`.
2. Add `GROQ_API_KEY`.
3. Start `start_server.bat`, or run:

```powershell
cd C:\wamp64\www\RealtimeSTT
venv\Scripts\activate
cd example_meetingcopilot_v2
python server.py
```

4. Open:

```text
http://localhost/RealtimeSTT/example_meetingcopilot_v2/
```

## Defaults

- WebSocket: `ws://localhost:8012`
- Main input: choose `Voicemeeter Out B2`
- Fast answer model: `openai/gpt-oss-20b`
- Improve/quality model: `llama-3.3-70b-versatile`
- Vision model: `meta-llama/llama-4-scout-17b-16e-instruct`
- Final STT model: `medium.en`
- Realtime STT preview model: `tiny.en`

## UX Changes From V1

- Audio signal details are hidden in `Debug audio`.
- Transcript shows one compact live card while listening.
- Final transcripts are split into readable cards.
- Each final card has quick actions: `Answer`, `Clarify`, `Ask`, `Push back`.
- Responses stream with the fast model by default.
- `Improve` regenerates a stronger draft with the quality model.

## Notes

- Do not store `.env`, SQLite files, logs, `tmp/`, or `__pycache__/`.
- This version does not upgrade the core `RealtimeSTT` package.
- Your own speech remains hidden context only when enabled in setup.
