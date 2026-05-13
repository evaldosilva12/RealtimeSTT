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
from prompt_store import PromptStore
from storage import Storage
from stt_service import STTService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s meeting_copilot %(message)s",
)

session_id = str(uuid.uuid4())
clients: set[Any] = set()
storage = Storage(settings.db_path)
storage.create_session(session_id)
prompt_store = PromptStore(settings.setup_path)
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

    if message_type == "setup.get":
        await emit({"type": "setup.current", "setup": prompt_store.load()})
        return

    if message_type == "setup.save":
        setup = prompt_store.save(message.get("setup") or {})
        await emit({"type": "setup.current", "setup": setup})
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


async def websocket_handler(websocket, path=None) -> None:
    clients.add(websocket)
    await websocket.send(
        json.dumps(
            {
                "type": "connection.status",
                "session_id": session_id,
                "stt": "ready" if stt_service and stt_service.ready.is_set() else "starting",
                "llm": "configured" if llm_service.enabled else "missing_api_key",
                "setup": prompt_store.load(),
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
    action_service = ActionService(storage, prompt_store, llm_service, emit)

    logging.info("Meeting Copilot session_id=%s", session_id)
    logging.info("WebSocket listening on ws://%s:%s", settings.host, settings.ws_port)
    if not llm_service.enabled:
        logging.warning("GROQ_API_KEY missing. LLM actions will report a setup error.")

    async with websockets.serve(websocket_handler, settings.host, settings.ws_port):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        if stt_service:
            stt_service.shutdown()
