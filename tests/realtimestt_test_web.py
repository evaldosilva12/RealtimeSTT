from RealtimeSTT import AudioToTextRecorder
import asyncio
import websockets
import threading
import numpy as np
from scipy.signal import resample
import json
import http.server
import socketserver
import webbrowser

recorder = None
recorder_ready = threading.Event()
client_websocket = None

async def send_to_client(message):
    if client_websocket:
        await client_websocket.send(message)

def text_detected(text):
    asyncio.new_event_loop().run_until_complete(
        send_to_client(
            json.dumps({
                'type': 'realtime',
                'text': text
            })
        )
    )
    print(f"\r{text}", flush=True, end='')

recorder_config = {
    'spinner': False,
    'use_microphone': False,
    'model': 'large-v2',
    'language': 'en',
    'silero_sensitivity': 0.4,
    'webrtc_sensitivity': 2,
    'post_speech_silence_duration': 0.7,
    'min_length_of_recording': 0,
    'min_gap_between_recordings': 0,
    'enable_realtime_transcription': True,
    'realtime_processing_pause': 0,
    'realtime_model_type': 'tiny.en',
    'on_realtime_transcription_stabilized': text_detected,
}

def recorder_thread():
    global recorder
    print("Initializing RealtimeSTT...")
    recorder = AudioToTextRecorder(**recorder_config)
    print("RealtimeSTT initialized")
    recorder_ready.set()
    while True:
        full_sentence = recorder.text()
        asyncio.new_event_loop().run_until_complete(
            send_to_client(
                json.dumps({
                    'type': 'fullSentence',
                    'text': full_sentence
                })
            )
        )
        print(f"\rSentence: {full_sentence}")

def decode_and_resample(audio_data, original_sample_rate, target_sample_rate):
    audio_np = np.frombuffer(audio_data, dtype=np.int16)
    num_original_samples = len(audio_np)
    num_target_samples = int(num_original_samples * target_sample_rate / original_sample_rate)
    resampled_audio = resample(audio_np, num_target_samples)
    return resampled_audio.astype(np.int16).tobytes()

async def echo(websocket, path):
    print("Client connected")
    global client_websocket
    client_websocket = websocket
    async for message in websocket:
        if not recorder_ready.is_set():
            print("Recorder not ready")
            continue

        metadata_length = int.from_bytes(message[:4], byteorder='little')
        metadata_json = message[4:4+metadata_length].decode('utf-8')
        metadata = json.loads(metadata_json)
        sample_rate = metadata['sampleRate']
        chunk = message[4+metadata_length:]
        resampled_chunk = decode_and_resample(chunk, sample_rate, 16000)
        recorder.feed_audio(resampled_chunk)

def start_http_server(port=8000):
    handler = http.server.SimpleHTTPRequestHandler
    httpd = socketserver.TCPServer(("", port), handler)
    print(f"Serving HTTP on http://localhost:{port}")
    webbrowser.open(f"http://127.0.0.1:{port}/index.html")
    httpd.serve_forever()

if __name__ == '__main__':
    start_server = websockets.serve(echo, "localhost", 8001)

    recorder_thread = threading.Thread(target=recorder_thread)
    recorder_thread.start()
    recorder_ready.wait()

    # Start the WebSocket server
    print("Starting WebSocket server on ws://localhost:8001")
    asyncio.get_event_loop().run_until_complete(start_server)

    # Start the HTTP server in a separate thread
    http_thread = threading.Thread(target=start_http_server, args=(8000,))
    http_thread.start()

    # Keep the WebSocket server running
    asyncio.get_event_loop().run_forever()
