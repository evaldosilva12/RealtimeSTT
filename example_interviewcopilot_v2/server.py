from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import websockets

from actions import ActionService
from config import settings
from llm_service import LLMService
from storage import Storage
from stt_service import STTService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s interview_copilot_v2 %(message)s",
)

session_id = str(uuid.uuid4())
clients: set[Any] = set()
storage = Storage(settings.db_path)
storage.create_session(session_id)
storage.ensure_default_interview_profile()
llm_service = LLMService()
stt_service: STTService | None = None
action_service: ActionService | None = None


async def emit(event: dict[str, Any]) -> None:
    event.setdefault("session_id", session_id)
    if not clients:
        return
    payload = json.dumps(event, ensure_ascii=True)
    dead_clients = []
    for websocket in list(clients):
        try:
            await websocket.send(payload)
        except Exception:
            dead_clients.append(websocket)
    for websocket in dead_clients:
        clients.discard(websocket)


async def handle_json(message: dict[str, Any]) -> None:
    global action_service
    message_type = message.get("type")

    if message_type == "profiles.get":
        await emit_profiles()
        return

    if message_type == "profile.save":
        profile = storage.save_interview_profile(message.get("profile") or {})
        storage.set_active_interview_profile(profile["id"])
        await emit_profiles()
        if message.get("generate_internal"):
            if action_service is None:
                await emit({"type": "profile.error", "message": "Action service is not ready."})
                return
            await emit({"type": "profile.generation.started", "profile_id": profile["id"]})
            internal_profile = await action_service.generate_internal_profile(profile)
            storage.update_internal_candidate_profile(profile["id"], internal_profile)
            await emit_profiles()
            await emit({"type": "profile.generation.completed", "profile_id": profile["id"]})
        return

    if message_type == "profile.select":
        profile = storage.set_active_interview_profile(str(message.get("profile_id") or ""))
        if not profile:
            await emit({"type": "profile.error", "message": "Interview profile not found."})
            return
        await emit_profiles()
        return

    if message_type == "profile.duplicate":
        profile = storage.duplicate_interview_profile(str(message.get("profile_id") or ""))
        if not profile:
            await emit({"type": "profile.error", "message": "Interview profile not found."})
            return
        storage.set_active_interview_profile(profile["id"])
        await emit_profiles()
        return

    if message_type == "profile.archive":
        storage.archive_interview_profile(str(message.get("profile_id") or ""))
        await emit_profiles()
        return

    if message_type == "profile.generate_internal":
        if action_service is None:
            await emit({"type": "profile.error", "message": "Action service is not ready."})
            return
        profile = storage.get_interview_profile(str(message.get("profile_id") or ""))
        if not profile:
            profile = storage.get_active_interview_profile()
        if not profile:
            await emit({"type": "profile.error", "message": "Interview profile not found."})
            return
        await emit({"type": "profile.generation.started", "profile_id": profile["id"]})
        internal_profile = await action_service.generate_internal_profile(profile)
        storage.update_internal_candidate_profile(profile["id"], internal_profile)
        await emit_profiles()
        await emit({"type": "profile.generation.completed", "profile_id": profile["id"]})
        return

    if message_type == "action.request":
        if action_service is None:
            await emit({"type": "action.error", "message": "Action service is not ready."})
            return
        await action_service.request(session_id, message)
        return

    if message_type == "action.cancel":
        if action_service is not None:
            await action_service.cancel(str(message.get("action_id") or ""))
        return

    if message_type == "context.my_note":
        text = str(message.get("text") or "").strip()
        if text:
            context_id = str(uuid.uuid4())
            storage.save_hidden_context(context_id, session_id, text)
            await emit(
                {
                    "type": "context.my_note.saved",
                    "context_id": context_id,
                    "text": text,
                }
            )
        return

    await emit({"type": "connection.status", "warning": f"Unknown event: {message_type}"})


async def emit_profiles() -> None:
    active_profile = storage.get_active_interview_profile() or storage.ensure_default_interview_profile()
    await emit(
        {
            "type": "profiles.current",
            "profiles": storage.list_interview_profiles(),
            "active_profile": active_profile,
            "openai_setup": "configured" if llm_service.openai_enabled else "not_configured",
        }
    )


async def websocket_handler(websocket, path=None) -> None:
    clients.add(websocket)
    await websocket.send(
        json.dumps(
            {
                "type": "connection.status",
                "session_id": session_id,
                "stt": "ready" if stt_service and stt_service.ready.is_set() else "starting",
                "llm": "configured" if llm_service.enabled else "missing_api_key",
                "profiles": storage.list_interview_profiles(),
                "active_profile": storage.get_active_interview_profile(),
                "openai_setup": "configured" if llm_service.openai_enabled else "not_configured",
            },
            ensure_ascii=True,
        )
    )
    try:
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    if stt_service:
                        stt_service.feed_audio_message(message)
                    continue
                await handle_json(json.loads(message))
            except json.JSONDecodeError:
                await emit({"type": "connection.status", "warning": "Invalid JSON message ignored."})
            except Exception as exc:
                logging.exception("message handling failed")
                await emit({"type": "connection.status", "error": str(exc)})
    finally:
        clients.discard(websocket)


async def main() -> None:
    global stt_service, action_service
    loop = asyncio.get_running_loop()
    stt_service = STTService(session_id, storage, emit)
    stt_service.start(loop)
    action_service = ActionService(storage, llm_service, emit)

    logging.info("Interview Copilot session_id=%s", session_id)
    logging.info("WebSocket listening on ws://%s:%s", settings.host, settings.ws_port)
    if not llm_service.enabled:
        logging.warning("GROQ_API_KEY missing. LLM actions will report a setup error.")
    if llm_service.openai_enabled:
        logging.info("OpenAI setup profile generation is configured.")

    async with websockets.serve(websocket_handler, settings.host, settings.ws_port):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        if stt_service:
            stt_service.shutdown()
