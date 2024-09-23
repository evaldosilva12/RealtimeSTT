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
                if message_data.get('type') == 'groq':
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

    # Handle Groq API request from client
    async def handle_groq_request(user_message):
        print(f"Sending to Groq: {user_message}")

        # Example of additional useful information
        additional_info = (
            "EVALDO SILVA, SOLUTIONS ARCHITECT\n"
            "CONTACT: esilva12@gmail.com, (403) 926-8567, Red Deer, AB\n"
            "\n"
            "SUMMARY: Experienced IT leader with over 18 years of driving technology strategy and digital transformation, "
            "backed by a solid foundation in software development and project management. Proficient in overseeing IT operations, "
            "including infrastructure, security, and data management, with a strong emphasis on innovation and compliance. "
            "Experienced in delivering specialized solutions to the public sector, ensuring they meet the unique needs and challenges. "
            "Passionate about engaging with customers and delivering cutting-edge solutions, leveraging deep technical expertise "
            "to meet and exceed expectations.\n"
            "\n"
            "SKILLS:\n"
            "• AWS Technologies\n"
            "• Cloud & Hosting Solutions\n"
            "• IT Infrastructure & Security\n"
            "• Stakeholder Engagement\n"
            "• Technical Consulting\n"
            "• Strategic Planning & Execution\n"
            "• Team Leadership\n"
            "• Project Management\n"
            "• Software / Web Development\n"
            "• AI / Machine Learning\n"
            "\n"
            "EDUCATION:\n"
            "• Software Development Diploma, Red Deer Polytechnic, 2022–2023\n"
            "• Project Management Diploma, Positive University, 2017–2020\n"
            "\n"
            "ADDITIONAL:\n"
            "• TEDx Organizer https://ted.com/tedx/events/17501\n"
            "• Startup Weekend Co-organizer\n"
            "• Speaker https://youtu.be/zfR1ZvBqufY\n"
            "\n"
            "EXPERIENCE:\n"
            "• CTO, 908 Engineering Inc., Mar 2023–Aug 2024\n"
            "  - Led the development and implementation of the company AI and automation strategies, enhancing operational efficiency by 25%.\n"
            "  - Managed IT operations, including infrastructure, cybersecurity, and application management, to maintain seamless business continuity.\n"
            "  - Engaged directly with clients to understand their technical challenges and customized solutions that met their specific needs, enhancing client satisfaction and long-term partnerships.\n"
            "\n"
            "• Head of Web Development, Chroma Garden, July 2021–Dec 2022\n"
            "  - Directed the Web Development Team, increasing productivity by 17% through regular stand-ups and coaching sessions.\n"
            "  - Introduced a Kanban Board system, improving workflow efficiency by 23%.\n"
            "  - Acted as the main point of contact for clients, ensuring effective translation of client needs to the technical team and clear communication of solutions back to the client.\n"
            "\n"
            "• Project Manager, e-Solutions, Mar 2008–Apr 2021\n"
            "  - Managed the development of enterprise-level web applications, aligning projects with long-term business strategies.\n"
            "  - Drove innovation by identifying market trends and customer needs, leading to the launch of several new products and services.\n"
            "  - Maintained strong communication with stakeholders, ensuring project alignment with business goals and successful delivery.\n"
            "  - Facilitated regular meetings with clients and stakeholders.\n"
        )

        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are my assistant copilot, actively listening to a meeting and using the information you know about me to help me respond. Your goal is to create responses as if I were speaking. Please keep the following points in mind: 1. Speak as if you are me: Answer each conversation as if it is me talking, using a style that reflects my personality and communication style. 2. Keep responses simple and clear: Since I'm Brazilian and my English is not perfect, create responses that are easy for me to speak and understand. 3. Provide natural and effective responses: Ensure that the responses sound natural for conversation, even if the grammar isn't 100% perfect. The goal is to make me sound confident but also relatable. 4. Keep a friendly and professional tone: Maintain a balance between being polite, confident, and approachable in your responses, depending on the context of the conversation."},
                {"role": "user", "content": "This is the information about me that you need to know:" + additional_info},
                {"role": "user", "content": user_message},
            ],
            model="llama3-8b-8192",
            temperature=0.5,
            max_tokens=2048,
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
