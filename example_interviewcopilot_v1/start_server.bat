@echo off
cd /d C:\wamp64\www\RealtimeSTT
call venv\Scripts\activate
cd example_interviewcopilot_v1
start "Interview Copilot Server" python server.py
timeout /t 3 > nul
start "" http://localhost/RealtimeSTT/example_interviewcopilot_v1/
