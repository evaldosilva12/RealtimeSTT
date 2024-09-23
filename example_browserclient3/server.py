import asyncio
import websockets
import threading
import numpy as np
from scipy.signal import resample
import json
from groq import Groq
# import ollama
from RealtimeSTT import AudioToTextRecorder  # Make sure this import is valid

client = Groq(api_key="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")

recorder = None
recorder_ready = threading.Event()
client_websocket = None

# History of messages sent to Groq
history = []
MAX_TOKENS = 8000  # Arbitrary token limit; adjust based on Groq model

# Recorder configuration
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
    'on_realtime_transcription_stabilized': lambda text: text_detected(text),
}

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

# Check if the sentence seems incomplete (based on simple heuristics)
def is_incomplete_sentence(sentence):
    return sentence.strip().endswith((',', 'and', 'but', 'so', '...'))

# Maintain history and ensure token limit is respected
def update_history(new_message):
    global history
    history.append(new_message)
    
    # Calculate the current token count
    total_tokens = sum(len(msg.split()) for msg in history)  # Simple word count
    
    # Trim oldest messages if over token limit
    while total_tokens > MAX_TOKENS:
        history.pop(0)
        total_tokens = sum(len(msg.split()) for msg in history)

async def handle_groq_request(sentence):
    # Only send meaningful sentences (skip incomplete ones)
    print(f"Handling Groq request: {sentence}")  # Log the sentence sent to Groq

    if is_incomplete_sentence(sentence):
        print(f"Skipped incomplete sentence: {sentence}")
        await send_to_client(
            json.dumps({
                'type': 'groqResponse',
                'response': "⚠️ Waiting for more context...",
                'useful': False
            })
        )
        return

    # Update the history and prepare the message for Groq
    update_history(sentence)
    
    # messages = [
    #     {"role": "system", "content": "You are a helpful assistant."},
    # ]

    messages = [
        {"role": "system", "content": "You are a helpful assistant helping me (Evaldo) to navigate a business meeting. I work for 908 AI, a company that provides bespoke software, automations, customizations, and integrations. In this meeting, me, Craig, Jeff and Carson will be discussing potential collaboration with WOW Lighting, a company that uses various tracking and estimation software programs but faces challenges with integration between them. The objective of the conversation is to explore ways 908 AI can help WOW Lighting integrate their existing systems without introducing new front-end software, respecting the company's current investments in Oasis and Pipedrive, and alleviating any concerns about major changes to their workflow. I want to demonstrate value without creating pressure, listening carefully to any objections, and asking thoughtful questions that help identify areas where integration and automation can offer improvements. Your output shoudl be always a setence like me saying."}
    ]

    # Add all messages from history to the chat context
    for msg in history:
        messages.append({"role": "user", "content": msg})

    # Log what is being sent to Groq
    # print("******************Sending the following to Groq:")
    for message in messages:
        print(f"Role: {message['role']}, Content: {message['content']}")        

    # print(f"Sending to Groq: {sentence}")
    chat_completion = client.chat.completions.create(
        messages=messages,
        model="llama-3.1-70b-versatile",
        temperature=0.5,
        max_tokens=1024,
        top_p=1,
        stop=None,
        stream=False,
    )

    groq_response = chat_completion.choices[0].message.content
    
    # Send response back to client (with different color coding for feedback)
    await send_to_client(
        json.dumps({
            'type': 'groqResponse',
            'response': groq_response,
            'useful': True
        })
    )

# async def handle_llama_request(sentence):
#     # Update the history and prepare the message for LLaMA
#     update_history(sentence)
    
#     # Build the list of messages to send to LLaMA (similar to Groq's structure)
#     messages = [
#         {"role": "system", "content": "You are a helpful assistant."},
#     ]

#     # Add all messages from history to the chat context
#     for msg in history:
#         messages.append({"role": "user", "content": msg})

#     # Log what is being sent to LLaMA
#     print("Sending the following to LLaMA:")
#     for message in messages:
#         print(f"Role: {message['role']}, Content: {message['content']}")

#     # Send the conversation to the LLaMA model locally using ollama
#     llama_response = ollama.chat(
#         model="llama3",  # Specify the local LLaMA model you want to use
#         messages=messages
#     )

#     llama_output = llama_response["message"]["content"]
    
#     # Send response back to client (with different color coding for feedback)
#     await send_to_client(
#         json.dumps({
#             'type': 'groqResponse',  # Keep 'groqResponse' for consistency, but it can be renamed
#             'response': llama_output,
#             'useful': True
#         })
#     )

# Recorder thread that sets the recorder_ready event when ready
def recorder_thread():
    global recorder
    print("Initializing RealtimeSTT...")
    recorder = AudioToTextRecorder(**recorder_config)
    print("RealtimeSTT initialized")
    recorder_ready.set()  # Set the event when the recorder is ready
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
    # Decode 16-bit PCM data to numpy array
    audio_np = np.frombuffer(audio_data, dtype=np.int16)

    # Calculate the number of samples after resampling
    num_original_samples = len(audio_np)
    num_target_samples = int(num_original_samples * target_sample_rate / original_sample_rate)

    # Resample the audio
    resampled_audio = resample(audio_np, num_target_samples)

    return resampled_audio.astype(np.int16).tobytes()

async def echo(websocket, path):
    global client_websocket
    client_websocket = websocket
    async for message in websocket:

        if not recorder_ready.is_set():
            print("Recorder not ready")
            continue

        # Check for different message types (metadata vs Groq interaction)
        try:
            message_data = json.loads(message)
            print(f"Received WebSocket message: {message_data}")  # Add this line to log incoming messages

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

if __name__ == '__main__':
    # Start WebSocket server
    start_server = websockets.serve(echo, "localhost", 8001)

    # Start the recorder thread
    recorder_thread = threading.Thread(target=recorder_thread)
    recorder_thread.start()

    # Wait until the recorder is ready
    recorder_ready.wait()

    print("Server started. Press Ctrl+C to stop the server.")
    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()

