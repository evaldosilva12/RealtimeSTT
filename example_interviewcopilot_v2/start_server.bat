@echo off
cd /d C:\wamp64\www\RealtimeSTT
call venv\Scripts\activate
cd example_interviewcopilot_v2
start "Interview Copilot V2 Server" python server.py
timeout /t 3 > nul
start "" http://localhost/RealtimeSTT/example_interviewcopilot_v2/
