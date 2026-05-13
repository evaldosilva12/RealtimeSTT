import { state } from "./state.js";

let retryDelay = 800;
let handlers = {};

export function connectSocket(onEvent) {
    handlers.onEvent = onEvent;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.hostname || "localhost"}:8011`);
    state.socket = socket;

    socket.onopen = () => {
        retryDelay = 800;
        state.connected = true;
        onEvent({ type: "client.socket", status: "connected" });
        sendJson({ type: "setup.get" });
    };

    socket.onmessage = (event) => {
        try {
            onEvent(JSON.parse(event.data));
        } catch (error) {
            onEvent({ type: "client.error", message: error.message });
        }
    };

    socket.onclose = () => {
        state.connected = false;
        onEvent({ type: "client.socket", status: "disconnected" });
        window.setTimeout(() => connectSocket(onEvent), retryDelay);
        retryDelay = Math.min(retryDelay * 1.6, 8000);
    };

    socket.onerror = () => {
        onEvent({ type: "client.socket", status: "error" });
    };
}

export function sendJson(payload) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
        return false;
    }
    state.socket.send(JSON.stringify(payload));
    return true;
}

export function sendAudioChunk(source, sampleRate, int16Buffer) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
        return;
    }

    const metadata = new TextEncoder().encode(JSON.stringify({ source, sampleRate }));
    const audioBytes = int16Buffer instanceof Int16Array
        ? new Uint8Array(int16Buffer.buffer.slice(0))
        : new Uint8Array(int16Buffer.slice(0));
    const packet = new ArrayBuffer(4 + metadata.byteLength + audioBytes.byteLength);
    const packetView = new Uint8Array(packet);
    new DataView(packet).setInt32(0, metadata.byteLength, true);
    packetView.set(metadata, 4);
    packetView.set(audioBytes, 4 + metadata.byteLength);
    state.socket.send(packet);
}
