@echo off
cd /d C:\wamp64\www\RealtimeSTT
call venv\Scripts\activate
set INTERVIEW_COPILOT_WS_PORT=8015
cd example_interviewcopilot_overlay
start "Interview Copilot Overlay Server" python server.py
timeout /t 3 > nul
start "" http://localhost/RealtimeSTT/example_interviewcopilot_overlay/
