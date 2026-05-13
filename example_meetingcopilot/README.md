# Meeting Copilot

This is the improved, isolated meeting-copilot version of `example_browserclient5`.

It keeps the original product idea:

- listen to the other participants;
- show their transcript in a focused timeline;
- click a transcript snippet or recent-context button;
- generate an answer as Evaldo using `system_role` and `additional_info`;
- keep Evaldo's own speech out of the main timeline, using it only as hidden context when enabled.

## Setup

1. Copy `.env.example` to `.env`.
2. Add `GROQ_API_KEY`.
3. Start `start_server.bat`, or run:

```powershell
cd C:\wamp64\www\RealtimeSTT
venv\Scripts\activate
cd example_meetingcopilot
python server.py
```

4. Open:

```text
http://localhost/RealtimeSTT/example_meetingcopilot/
```

## Runtime Model

- WebSocket: `ws://localhost:8011`
- Main visible transcript source: selected browser recording input, sent as `others_audio`.
- With Voicemeeter, choose `Voicemeeter Out A1` or the bus that contains the other participants' audio.
- `Share screen audio` is still available as a fallback, but Voicemeeter input selection is usually more reliable on Windows.
- Hidden personal context: browser speech recognition or manual notes, sent as `context.my_note`.
- STT: local `RealtimeSTT` through an isolated adapter.
- LLM: Groq provider through `llm_service.py`.
- Persistence:
  - prompt setup: `setup_data.json`
  - session data: `meeting_copilot.sqlite3`

## Event Protocol

Incoming client events:

- `setup.get`
- `setup.save`
- `action.request`
- `action.cancel`
- `context.my_note`
- binary PCM chunks with metadata `{ source, sampleRate }`

Outgoing server events:

- `connection.status`
- `setup.current`
- `transcript.partial`
- `transcript.final`
- `action.requested`
- `action.delta`
- `action.completed`
- `action.error`
- `context.my_note.saved`

## Notes

- `example_browserclient5` is intentionally untouched.
- The app does not upgrade the repository's RealtimeSTT package in place.
- Screenshot actions use the local desktop screenshot API and should be treated as explicit operator-triggered actions.
- If `GROQ_API_KEY` is missing, transcription can still run, but LLM actions will return a setup error.
