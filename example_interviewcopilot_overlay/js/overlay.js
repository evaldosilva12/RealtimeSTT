import { connectSocket, sendJson } from "./socket.js";
import { newestUtterances, state } from "./state.js";

const socketStatus = document.getElementById("socketStatus");
const sttStatus = document.getElementById("sttStatus");
const llmStatus = document.getElementById("llmStatus");
const modeStatus = document.getElementById("modeStatus");
const timeline = document.getElementById("timeline");
const partial = document.getElementById("partialTranscript");
const activeResponse = document.getElementById("activeResponse");
const responseBody = document.getElementById("responseBody");

let livePreview = null;
let activeActionId = null;
let activeResponseText = "";

function setPill(element, text, mode = "") {
    element.textContent = text;
    element.className = `status-pill ${mode}`.trim();
}

function requestAction(actionType, options = {}) {
    const actionId = crypto.randomUUID();
    sendJson({
        type: "action.request",
        action_id: actionId,
        action_type: actionType,
        ...options,
    });
}

function requestLastN(count, actionType = "answer_last_n") {
    const text = newestUtterances(count).map((item) => item.text).join(" ");
    requestAction(actionType, { count, text });
}

function handleEvent(event) {
    if (event.session_id) {
        state.sessionId = event.session_id;
    }

    if (event.type === "client.socket") {
        setPill(
            socketStatus,
            event.status === "connected" ? "WS connected" : "WS disconnected",
            event.status === "connected" ? "status-ok" : "status-warn",
        );
        return;
    }

    if (event.type === "connection.status") {
        if (event.stt) {
            setPill(sttStatus, `STT ${event.stt}`, event.stt === "ready" ? "status-ok" : "status-warn");
        }
        if (event.llm) {
            setPill(
                llmStatus,
                `LLM ${event.llm}`,
                event.llm === "configured" ? "status-ok" : "status-error",
            );
        }
        return;
    }

    if (event.type === "transcript.partial") {
        renderPartial(event);
        return;
    }

    if (event.type === "transcript.final") {
        renderFinal(event);
        return;
    }

    if (event.type.startsWith("action.")) {
        handleActionEvent(event);
    }
}

function renderPartial(event) {
    const text = event.text.replace(/\s+/g, " ").trim();
    livePreview = text ? {
        utterance_id: `${event.utterance_id}-live`,
        source: "other",
        status: "live",
        text: compactLiveText(text),
        created_at: event.created_at,
        isLive: true,
    } : null;
    partial.hidden = true;
    renderTimeline();
}

function renderFinal(event) {
    livePreview = null;
    partial.hidden = true;
    if (!event.text || !event.text.trim()) {
        return;
    }

    splitFinalText(event.text).forEach((text, index) => {
        state.utterances.set(`${event.utterance_id}-${index}`, {
            ...event,
            utterance_id: `${event.utterance_id}-${index}`,
            status: "final",
            text,
            created_at: event.created_at + index * 0.001,
        });
    });
    renderTimeline();
}

function renderTimeline() {
    const finals = [...state.utterances.values()]
        .filter((item) => item.status === "final" && item.source === "other")
        .sort((a, b) => b.created_at - a.created_at)
        .slice(0, 5);
    const items = livePreview ? [livePreview, ...finals] : finals;
    timeline.innerHTML = "";

    for (const item of items) {
        const card = document.createElement("article");
        card.className = item.isLive ? "utterance live" : "utterance";

        const header = document.createElement("div");
        header.className = "utterance-header";
        header.textContent = item.isLive ? "Live" : new Date(item.created_at * 1000).toLocaleTimeString();

        const text = document.createElement("div");
        text.className = "utterance-text";
        text.textContent = item.text;

        card.append(header, text);
        card.addEventListener("click", () => requestAction("answer_as_me", {
            text: item.text,
            utterance_id: item.utterance_id,
        }));
        timeline.appendChild(card);
    }
}

function handleActionEvent(event) {
    if (event.type === "action.requested") {
        activeActionId = event.action_id;
        activeResponseText = "";
        activeResponse.querySelector(".response-meta").textContent = event.label || event.action_type || "Action";
        responseBody.textContent = "Thinking...";
        activeResponse.className = "active-response running";
        return;
    }

    if (event.type === "action.delta" && event.action_id === activeActionId) {
        activeResponseText += event.delta;
        responseBody.textContent = activeResponseText;
        return;
    }

    if (event.type === "action.completed" && event.action_id === activeActionId) {
        activeResponseText = event.response || activeResponseText;
        responseBody.textContent = activeResponseText || "Completed.";
        activeResponse.className = "active-response";
        return;
    }

    if (event.type === "action.cancelled" && event.action_id === activeActionId) {
        responseBody.textContent = activeResponseText || event.message || "Cancelled.";
        activeResponse.className = "active-response";
        return;
    }

    if (event.type === "action.error") {
        activeResponse.querySelector(".response-meta").textContent = "Error";
        responseBody.textContent = event.message || "Action failed.";
        activeResponse.className = "active-response error";
    }
}

function splitFinalText(text) {
    const normalized = text.replace(/\s+/g, " ").trim();
    if (!normalized) {
        return [];
    }

    const sentenceParts = normalized
        .split(/(?<=[.!?])\s+/)
        .map((part) => part.trim())
        .filter(Boolean);

    const chunks = [];
    let current = "";
    for (const part of sentenceParts) {
        if (!current) {
            current = part;
        } else if ((current + " " + part).length <= 220) {
            current += " " + part;
        } else {
            chunks.push(current);
            current = part;
        }
    }
    if (current) {
        chunks.push(current);
    }

    if (chunks.length <= 1 && normalized.length > 260) {
        return normalized.match(/.{1,220}(\s|$)/g).map((part) => part.trim()).filter(Boolean);
    }
    return chunks;
}

function compactLiveText(text) {
    const normalized = text.replace(/\s+/g, " ").trim();
    return normalized.length <= 360 ? normalized : `...${normalized.slice(-360)}`;
}

function renderOverlayState(stateUpdate) {
    if (!stateUpdate) {
        return;
    }
    const mode = stateUpdate.passThrough ? "Pass-through" : "Interactive";
    const locked = stateUpdate.locked ? " locked" : "";
    setPill(modeStatus, `${mode}${locked}`, stateUpdate.passThrough ? "status-warn" : "status-ok");
}

document.getElementById("answerRecent").addEventListener("click", () => requestLastN(4, "answer_last_n"));
document.getElementById("clarifyRecent").addEventListener("click", () => requestLastN(4, "clarify"));
document.getElementById("recoverRecent").addEventListener("click", () => requestLastN(6, "recover_answer"));
document.getElementById("copyResponse").addEventListener("click", async () => {
    await navigator.clipboard.writeText(activeResponseText || responseBody.textContent || "");
});

document.getElementById("showControl").addEventListener("click", () => window.interviewCopilot?.showControl());
document.getElementById("toggleLock").addEventListener("click", async () => {
    renderOverlayState(await window.interviewCopilot?.toggleLock());
});
document.getElementById("togglePassThrough").addEventListener("click", async () => {
    renderOverlayState(await window.interviewCopilot?.togglePassThrough());
});
document.getElementById("hideOverlay").addEventListener("click", () => window.interviewCopilot?.toggleOverlay());

window.interviewCopilot?.onOverlayState(renderOverlayState);
window.interviewCopilot?.onBackendStatus((backend) => {
    if (backend.status === "starting") {
        setPill(socketStatus, "Backend starting", "status-warn");
    }
    if (backend.status === "stopped") {
        setPill(socketStatus, "Backend stopped", "status-error");
    }
    if (backend.status === "error") {
        setPill(socketStatus, "Backend error", "status-error");
    }
});
window.interviewCopilot?.getOverlayState().then(renderOverlayState).catch(() => {});

connectSocket(handleEvent);
