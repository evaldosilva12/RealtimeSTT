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
                <div class="response-tools">
                    <button class="context-response" type="button">Context used</button>
                    <button class="improve-response" type="button">Improve</button>
                    <button class="copy-response" type="button">Copy</button>
                    <button class="cancel-response" type="button">Cancel</button>
                </div>
            </div>
            <div class="source-kind"></div>
            <div class="source-text"></div>
            <details class="context-debug">
                <summary>Context used</summary>
                <div class="context-inspector"></div>
                <pre></pre>
            </details>
            <div class="response-body">Thinking...</div>
        `;
        card.querySelector(".source-kind").textContent = event.source_label
            ? `Source: ${event.source_label}`
            : "";
        card.querySelector(".source-text").textContent = event.source_text || "";
        const contextDebug = formatContextDebug(event.context_debug);
        const contextDetails = card.querySelector(".context-debug");
        card.querySelector(".context-inspector").innerHTML = renderContextInspector(event.context_inspector);
        contextDetails.querySelector("pre").textContent = contextDebug || "No context debug available.";
        card.querySelector(".context-response").addEventListener("click", () => {
            contextDetails.open = !contextDetails.open;
        });
        card.querySelector(".copy-response").addEventListener("click", async () => {
            const text = card.querySelector(".response-body").textContent;
            await navigator.clipboard.writeText(text);
        });
        card.querySelector(".improve-response").addEventListener("click", () => {
            const responseText = card.querySelector(".response-body").textContent;
            requestAction("improve_answer", {
                text: event.source_text || "",
                previous_response: responseText,
            });
        });
        card.querySelector(".cancel-response").addEventListener("click", () => {
            const current = state.responses.get(event.action_id) || {};
            current.cancelled = true;
            state.responses.set(event.action_id, current);
            sendJson({ type: "action.cancel", action_id: event.action_id });
            card.classList.remove("running");
            card.classList.add("cancelled");
            card.querySelector(".response-body").textContent = current.text || "Cancelled.";
        });
        responses.prepend(card);
        state.responses.set(event.action_id, {
            text: "",
            sourceText: event.source_text || "",
            sourceLabel: event.source_label || "",
            cancelled: false,
        });
        return;
    }

    if (event.type === "action.delta") {
        const card = document.getElementById(`action-${event.action_id}`);
        if (!card) {
            return;
        }
        const current = state.responses.get(event.action_id) || { text: "" };
        if (current.cancelled) {
            return;
        }
        current.text += event.delta;
        state.responses.set(event.action_id, current);
        card.querySelector(".response-body").textContent = current.text;
        return;
    }

    if (event.type === "action.completed") {
        const card = document.getElementById(`action-${event.action_id}`);
        if (card) {
            const current = state.responses.get(event.action_id);
            if (current?.cancelled) {
                return;
            }
            card.classList.remove("running");
            card.querySelector(".cancel-response")?.remove();
            card.querySelector(".response-body").textContent = event.response || card.querySelector(".response-body").textContent;
        }
        return;
    }

    if (event.type === "action.cancelled") {
        const card = document.getElementById(`action-${event.action_id}`);
        const current = state.responses.get(event.action_id) || { text: "" };
        current.cancelled = true;
        state.responses.set(event.action_id, current);
        if (card) {
            card.classList.remove("running");
            card.classList.add("cancelled");
            card.querySelector(".cancel-response")?.remove();
            card.querySelector(".response-body").textContent = current.text || event.message || "Cancelled.";
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

function formatContextDebug(contextDebug) {
    if (!contextDebug) {
        return "";
    }
    if (typeof contextDebug === "string") {
        return contextDebug;
    }
    return [
        "SYSTEM",
        contextDebug.system || "",
        "",
        "USER",
        contextDebug.user || "",
    ].join("\n");
}

function renderContextInspector(inspector) {
    if (!inspector) {
        return `<div class="inspector-empty">No inspector summary available.</div>`;
    }

    const detection = inspector.question_detection || {};
    const blocks = inspector.blocks || {};
    const promptChars = inspector.prompt_chars || {};
    const assessment = promptChars.assessment || {};
    const memory = inspector.memory || {};
    const priority = Array.isArray(inspector.context_priority) ? inspector.context_priority : [];
    const blockRows = Object.entries(blocks).map(([name, block]) => renderBlockRow(name, block)).join("");
    const priorityItems = priority.map((item) => `<li>${escapeHtml(item)}</li>`).join("");

    return `
        <div class="inspector-section">
            <div class="inspector-label">Detected target</div>
            <div class="inspector-target">${escapeHtml(detection.target || "(none detected)")}</div>
            <div class="inspector-meta">
                ${escapeHtml(detection.confidence || "unknown")} confidence - ${escapeHtml(detection.method || "unknown")}
            </div>
            ${detection.reason ? `<div class="inspector-meta">${escapeHtml(detection.reason)}</div>` : ""}
        </div>
        <div class="inspector-grid">
            <div>
                <span class="inspector-label">Source</span>
                <strong>${escapeHtml(inspector.source_label || "unknown")}</strong>
            </div>
            <div>
                <span class="inspector-label">Profile</span>
                <strong>${escapeHtml(inspector.profile_name || "Untitled Interview")}</strong>
            </div>
            <div>
                <span class="inspector-label">Internal profile</span>
                <strong>${inspector.has_internal_candidate_profile ? "present" : "missing"}</strong>
            </div>
            <div>
                <span class="inspector-label">Prompt size</span>
                <strong>${formatNumber(promptChars.total || 0)} chars</strong>
            </div>
            <div>
                <span class="inspector-label">Prompt health</span>
                <strong class="prompt-health-${escapeHtml(assessment.level || "unknown")}">${escapeHtml(assessment.level || "unknown")}</strong>
            </div>
            <div>
                <span class="inspector-label">Action memory</span>
                <strong>${formatNumber(memory.completed_actions_used || 0)}/${formatNumber(memory.limit || 0)} used</strong>
            </div>
        </div>
        ${assessment.message ? `<div class="inspector-meta">${escapeHtml(assessment.message)}</div>` : ""}
        ${priorityItems ? `<ol class="inspector-priority">${priorityItems}</ol>` : ""}
        ${blockRows ? `<div class="inspector-blocks">${blockRows}</div>` : ""}
    `;
}

function renderBlockRow(name, block = {}) {
    const label = name.replaceAll("_", " ");
    const present = block.present ? "used" : "empty";
    const truncated = block.truncated ? "truncated" : "full";
    const chars = formatNumber(block.chars || 0);
    const original = formatNumber(block.original_chars || 0);
    return `
        <div class="inspector-block-row">
            <span>${escapeHtml(label)}</span>
            <strong>${present}</strong>
            <span>${chars}/${original} chars</span>
            <span class="${block.truncated ? "is-truncated" : ""}">${truncated}</span>
        </div>
    `;
}

function formatNumber(value) {
    return new Intl.NumberFormat().format(Number(value) || 0);
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}
