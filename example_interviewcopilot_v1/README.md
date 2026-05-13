# Interview Copilot V1

Prototype copied from `example_meetingcopilot_v2` for job interview assistance.
The original `example_meetingcopilot_v2` folder is intentionally left untouched.

## Setup

1. Copy `.env.example` to `.env` if needed.
2. Add `GROQ_API_KEY` for real-time answers.
3. Optionally add `OPENAI_API_KEY` and `OPENAI_SETUP_MODEL` for internal candidate profile generation.
4. Start `start_server.bat`, or run:

```powershell
cd C:\wamp64\www\RealtimeSTT
venv\Scripts\activate
cd example_interviewcopilot_v1
python server.py
```

5. Open:

```text
http://localhost/RealtimeSTT/example_interviewcopilot_v1/
```

## Interview Profiles

- Create one saved profile per interview target, such as `CTO - Company A` or `Developer - Company B`.
- Paste text directly into the setup fields. There is no upload or document processing step.
- Use `Generate Profile` to create the internal candidate profile from the pasted text.
- Select the active profile before using live answer actions.

## Defaults

- WebSocket: `ws://localhost:8013`
- Fast answer model: `openai/gpt-oss-20b`
- Improve/quality model: `llama-3.3-70b-versatile`
- Optional setup model: `gpt-4.1-mini`
