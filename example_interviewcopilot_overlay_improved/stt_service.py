from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
from scipy.signal import resample

from config import settings
from storage import Storage


Emit = Callable[[dict[str, Any]], Awaitable[None]]


class STTService:
    def __init__(self, session_id: str, storage: Storage, emit: Emit) -> None:
        self.session_id = session_id
        self.storage = storage
        self.emit = emit
        self.loop: asyncio.AbstractEventLoop | None = None
        self.recorder = None
        self.ready = threading.Event()
        self.thread: threading.Thread | None = None
        self.current_utterance_id: str | None = None
        self.lock = threading.Lock()
        self.started = False
        self.raw_audio_buffer = bytearray()
        self.resampled_audio_buffer = bytearray()
        self.raw_sample_rate: int | None = None
        self.last_resampled_peak = 0
        self.last_audio_log = 0.0

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self.started:
            return
        self.started = True
        self.loop = loop
        self.thread = threading.Thread(target=self._recorder_thread, daemon=True)
        self.thread.start()

    def feed_audio_message(self, message: bytes) -> None:
        if not self.ready.is_set() or self.recorder is None:
            return

        metadata_length = int.from_bytes(message[:4], byteorder="little")
        metadata_json = message[4 : 4 + metadata_length].decode("utf-8")
        metadata = json.loads(metadata_json)
        source = metadata.get("source", "others_audio")
        if source != "others_audio":
            return

        sample_rate = int(metadata["sampleRate"])
        chunk = message[4 + metadata_length :]
        raw_np = np.frombuffer(chunk, dtype=np.int16)
        raw_peak = int(np.max(np.abs(raw_np))) if raw_np.size else 0

        if self.raw_sample_rate != sample_rate:
            self.raw_sample_rate = sample_rate
            self.raw_audio_buffer.clear()

        self.raw_audio_buffer.extend(chunk)
        raw_frame_bytes = sample_rate * 2 // 10
        resampled = b""

        while len(self.raw_audio_buffer) >= raw_frame_bytes:
            raw_frame = bytes(self.raw_audio_buffer[:raw_frame_bytes])
            del self.raw_audio_buffer[:raw_frame_bytes]
            resampled = self._decode_and_resample(raw_frame, sample_rate, 16000)
            resampled_np = np.frombuffer(resampled, dtype=np.int16)
            self.last_resampled_peak = int(np.max(np.abs(resampled_np))) if resampled_np.size else 0
            self.resampled_audio_buffer.extend(resampled)

        stt_frame_bytes = 16000 * 2 // 10
        while len(self.resampled_audio_buffer) >= stt_frame_bytes:
            frame = bytes(self.resampled_audio_buffer[:stt_frame_bytes])
            del self.resampled_audio_buffer[:stt_frame_bytes]
            self.recorder.feed_audio(frame)

        now = time.time()
        if now - self.last_audio_log > 3:
            self.last_audio_log = now
            self._emit_threadsafe(
                {
                    "type": "audio.debug",
                    "source": source,
                    "sample_rate": sample_rate,
                    "bytes": len(chunk),
                    "raw_peak": raw_peak,
                    "resampled_peak": self.last_resampled_peak,
                }
            )
            print(
                f"browser audio received source={source} sample_rate={sample_rate} "
                f"bytes={len(chunk)} raw_peak={raw_peak} resampled_peak={self.last_resampled_peak}",
                flush=True,
            )

    def shutdown(self) -> None:
        if self.recorder:
            self.recorder.shutdown()

    def _recorder_thread(self) -> None:
        from RealtimeSTT import AudioToTextRecorder

        recorder_config = {
            "spinner": True,
            "use_microphone": False,
            "model": settings.stt_model,
            "language": settings.language,
            "silero_sensitivity": 0.2,
            "webrtc_sensitivity": 1,
            "post_speech_silence_duration": 0.45,
            "min_length_of_recording": 0.35,
            "min_gap_between_recordings": 0,
            "enable_realtime_transcription": True,
            "realtime_processing_pause": 0.2,
            "realtime_model_type": settings.realtime_model,
            "on_realtime_transcription_update": self._partial_detected,
            "on_realtime_transcription_stabilized": self._stable_detected,
        }

        self._emit_threadsafe({"type": "connection.status", "stt": "initializing"})
        self.recorder = AudioToTextRecorder(**recorder_config)
        self.ready.set()
        self._emit_threadsafe({"type": "connection.status", "stt": "ready"})

        while True:
            text = self.recorder.text()
            if not text:
                continue
            utterance_id = self._get_current_utterance_id()
            self.storage.save_utterance(utterance_id, self.session_id, "other", "final", text)
            self._emit_threadsafe(
                {
                    "type": "transcript.final",
                    "utterance_id": utterance_id,
                    "source": "other",
                    "status": "final",
                    "text": text,
                    "created_at": time.time(),
                }
            )
            with self.lock:
                self.current_utterance_id = None

    def _partial_detected(self, text: str) -> None:
        self._emit_transcript_preview(text, "partial")

    def _stable_detected(self, text: str) -> None:
        self._emit_transcript_preview(text, "stable")

    def _emit_transcript_preview(self, text: str, status: str) -> None:
        if not text.strip():
            return
        utterance_id = self._get_current_utterance_id()
        self._emit_threadsafe(
            {
                "type": "transcript.partial",
                "utterance_id": utterance_id,
                "source": "other",
                "status": status,
                "text": text,
                "created_at": time.time(),
            }
        )

    def _get_current_utterance_id(self) -> str:
        with self.lock:
            if not self.current_utterance_id:
                self.current_utterance_id = str(uuid.uuid4())
            return self.current_utterance_id

    def _emit_threadsafe(self, event: dict[str, Any]) -> None:
        if not self.loop:
            return
        asyncio.run_coroutine_threadsafe(self.emit(event), self.loop)

    @staticmethod
    def _decode_and_resample(audio_data: bytes, original_sample_rate: int, target_sample_rate: int) -> bytes:
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
        if original_sample_rate == target_sample_rate:
            return audio_np.tobytes()
        num_original_samples = len(audio_np)
        num_target_samples = int(num_original_samples * target_sample_rate / original_sample_rate)
        resampled = resample(audio_np, num_target_samples)
        return resampled.astype(np.int16).tobytes()
