if __name__ == '__main__':
    print("Starting server, please wait...")

    from RealtimeSTT import AudioToTextRecorder
    import asyncio
    import websockets
    import threading
    import numpy as np
    from scipy.signal import resample
    import json
    from groq import Groq
    from PIL import ImageGrab
    import base64

    recorder = None
    recorder_ready = threading.Event()
    client_websocket = None
    client = Groq(api_key="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")

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
        'spinner': True,
        'use_microphone': True,
        'model': 'large-v2',
        'language': 'en',
        'silero_sensitivity': 0.4,
        'webrtc_sensitivity': 2,
        'post_speech_silence_duration': 0.1,
        'min_length_of_recording': 0,
        'min_gap_between_recordings': 0,
        'enable_realtime_transcription': False,
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

            # Check for different message types (metadata vs Groq interaction)
            try:
                message_data = json.loads(message)
                if message_data.get('type') == 'screenshot':
                    await handle_screenshot_request()
                elif message_data.get('type') == 'groq':
                    await handle_groq_request(message_data.get('text'))
                continue
            except (ValueError, json.JSONDecodeError):
                # Not a JSON message, likely audio data
                pass

            # Handle audio data
            metadata_length = int.from_bytes(message[:4], byteorder='little')
            metadata_json = message[4:4+metadata_length].decode('utf-8')
            metadata = json.loads(metadata_json)
            sample_rate = metadata['sampleRate']
            chunk = message[4+metadata_length:]
            resampled_chunk = decode_and_resample(chunk, sample_rate, 16000)
            recorder.feed_audio(resampled_chunk)

    def load_setup_data():
        try:
            with open("setup_data.json", "r") as f:
                setup_data = json.load(f)
                return setup_data.get('system_role'), setup_data.get('additional_info')
        except FileNotFoundError:
            print("Setup file not found. Please save the setup first.")
            return None, None

    def take_screenshot():
        path = 'screenshot.jpg'
        
        # Define the bounding box for the region you want to capture
        bbox = (0, 140, 2200, 1820)  # Adjust these coordinates as needed
        
        screenshot = ImageGrab.grab(bbox=bbox)
        rgb_screenshot = screenshot.convert('RGB')
        rgb_screenshot.save(path, quality=15)
        
        # Return the path for further processing
        return path

    # Handle Groq API request for screenshots
    async def handle_screenshot_request():
        screenshot_path = take_screenshot()
        
        with open(screenshot_path, "rb") as image_file:
            image_data = image_file.read()

        # Convert image to base64 to send to Groq
        image_base64 = base64.b64encode(image_data).decode("utf-8")

        # Load the system role and additional info from the setup file (if needed)
        system_role, additional_info = load_setup_data()

        if system_role is None or additional_info is None:
            await send_to_client(
                json.dumps({
                    'type': 'error',
                    'message': 'Please set up the system role and additional information first.'
                })
            )
            return

        # Example prompt to check if the screenshot contains code
        prompt = (
            f"Analyze the image provided and determine if it contains code. If code is present, interpret it and provide a clear, step-by-step explanation of its functionality and purpose. Identify any incomplete or erroneous code and either fix or complete it to ensure that it works correctly. After making any necessary changes, rewrite the code to improve its clarity, readability, and performance. Provide an explanation of any modifications, explaining the reasoning behind each improvement."
        )

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}",
                            },
                        },
                    ],
                }
            ],
            model="llava-v1.5-7b-4096-preview",
            temperature=0.6,
            max_tokens=1024,
            top_p=1,
            stop=None,
            stream=False,
        )

        groq_response = chat_completion.choices[0].message.content
        await send_to_client(
            json.dumps({
                'type': 'groqResponse',
                'response': groq_response
            })
        )

    # Handle Groq API request from client
    async def handle_groq_request(user_message):
        print(f"Sending to Groq: {user_message}")

        # Load the system role and additional info from file
        system_role, additional_info = load_setup_data()
        if system_role is None or additional_info is None:
            await send_to_client(
                json.dumps({
                    'type': 'error',
                    'message': 'Please set up the system role and additional information first.'
                })
            )
            return

        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_role},
                {"role": "user", "content": "This is the crucial information that you need to know:" + additional_info},
                {"role": "user", "content": "I need you to answer this as me:" + user_message},
            ],
            model="llama3-8b-8192",
            temperature=0.6,
            max_tokens=1024,
            top_p=1,
            stop=None,
            stream=False,
        )
        groq_response = chat_completion.choices[0].message.content
        await send_to_client(
            json.dumps({
                'type': 'groqResponse',
                'response': groq_response
            })
        )

    # Start WebSocket server
    start_server = websockets.serve(echo, "localhost", 8001)

    recorder_thread = threading.Thread(target=recorder_thread)
    recorder_thread.start()
    recorder_ready.wait()

    print("Server started. Press Ctrl+C to stop the server.")
    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()
