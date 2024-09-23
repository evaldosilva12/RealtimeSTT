cd C:\wamp64\www\RealtimeSTT
call venv\Scripts\activate
cd example_browserclient3
start python server.py

rem Wait for 5 seconds to ensure the server is running before opening the URL
timeout /t 10

rem Open the URL in the default browser
start "" http://localhost/RealtimeSTT/example_browserclient4/
