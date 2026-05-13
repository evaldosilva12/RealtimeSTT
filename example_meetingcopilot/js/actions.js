import { newestUtterances, state } from "./state.js";
import { sendJson } from "./socket.js";

const responses = document.getElementById("responses");

export function requestAction(actionType, options = {}) {
    const actionId = crypto.randomUUID();
    const payload = {
        type: "action.request",
        action_id: actionId,
        action_type: actionType,
        ...options,
    };
    sendJson(payload);
}

export function requestLastN(count, actionType = "answer_last_n") {
    const text = newestUtterances(count).map((item) => item.text).join(" ");
    requestAction(actionType, { count, text });
}

export function handleActionEvent(event) {
    if (event.type === "action.requested") {
        const card = document.createElement("article");
        card.className = "response-card running";
        card.id = `action-${event.action_id}`;
        card.innerHTML = `
            <div class="response-meta">
                <span>${event.label || event.action_type || "Action"}</span>
                <button class="copy-response" type="button">Copy</button>
            </div>
            <div class="source-text"></div>
            <div class="response-body">Thinking...</div>
        `;
        card.querySelector(".source-text").textContent = event.source_text || "";
        card.querySelector(".copy-response").addEventListener("click", async () => {
            const text = card.querySelector(".response-body").textContent;
            await navigator.clipboard.writeText(text);
        });
        responses.prepend(card);
        state.responses.set(event.action_id, { text: "" });
        return;
    }

    if (event.type === "action.delta") {
        const card = document.getElementById(`action-${event.action_id}`);
        if (!card) {
            return;
        }
        const current = state.responses.get(event.action_id) || { text: "" };
        current.text += event.delta;
        state.responses.set(event.action_id, current);
        card.querySelector(".response-body").textContent = current.text;
        return;
    }

    if (event.type === "action.completed") {
        const card = document.getElementById(`action-${event.action_id}`);
        if (card) {
            card.classList.remove("running");
            card.querySelector(".response-body").textContent = event.response || card.querySelector(".response-body").textContent;
        }
        return;
    }

    if (event.type === "action.error") {
        const card = document.getElementById(`action-${event.action_id}`) || document.createElement("article");
        if (!card.id) {
            card.id = `action-${event.action_id || crypto.randomUUID()}`;
            responses.prepend(card);
        }
        card.className = "response-card error";
        card.innerHTML = `
            <div class="response-meta"><span>Error</span></div>
            <div class="response-body"></div>
        `;
        card.querySelector(".response-body").textContent = event.message || "Action failed.";
    }
}
