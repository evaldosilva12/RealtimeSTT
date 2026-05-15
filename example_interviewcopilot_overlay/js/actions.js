import { newestUtterances, state } from "./state.js";
import { sendJson } from "./socket.js";

const responses = document.getElementById("responses");
const pendingHistoryExports = new Map();

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

export function requestLastN(count, actionType = "answer_last_n", options = {}) {
    const text = newestUtterances(count).map((item) => item.text).join(" ");
    requestAction(actionType, { count, text, ...options });
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
                    <button class="delete-response" type="button" title="Delete this answer" aria-label="Delete this answer">X</button>
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
            <div class="response-status" hidden></div>
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
        card.querySelector(".copy-response").addEventListener("click", async (clickEvent) => {
            const button = clickEvent.currentTarget;
            const text = card.querySelector(".response-body").textContent.trim();
            if (!text || text === "Thinking...") {
                flashButtonLabel(button, "No answer yet");
                return;
            }
            try {
                await copyText(text);
                flashButtonLabel(button, "Copied");
            } catch (error) {
                console.warn("Copy failed", error);
                flashButtonLabel(button, "Copy failed");
            }
        });
        card.querySelector(".improve-response").addEventListener("click", () => {
            const responseText = card.querySelector(".response-body").textContent;
            requestAction("improve_answer", {
                text: event.source_text || "",
                previous_response: responseText,
            });
        });
        card.querySelector(".delete-response").addEventListener("click", () => {
            deleteResponseCard(event.action_id, card);
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
            responseMode: event.response_mode || "normal",
            createdAt: Date.now(),
            cancelled: false,
        });
        return;
    }

    if (event.type === "action.status") {
        const card = document.getElementById(`action-${event.action_id}`);
        if (!card) {
            return;
        }
        const status = card.querySelector(".response-status");
        status.hidden = false;
        status.textContent = event.message || "";
        return;
    }

    if (event.type === "action.reset") {
        const card = document.getElementById(`action-${event.action_id}`);
        if (!card) {
            return;
        }
        const current = state.responses.get(event.action_id) || {};
        if (current.cancelled) {
            return;
        }
        current.text = "";
        state.responses.set(event.action_id, current);
        card.querySelector(".response-body").textContent = "Retrying with fallback...";
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
            const current = state.responses.get(event.action_id) || {};
            if (current?.cancelled) {
                return;
            }
            current.text = (event.response || current.text || "").trim();
            current.modelInfo = event.model_info || null;
            state.responses.set(event.action_id, current);
            card.classList.remove("running");
            card.querySelector(".cancel-response")?.remove();
            card.querySelector(".response-body").textContent = current.text || "Completed, but no answer text was returned.";
            if (event.model_info?.provider && event.model_info?.model) {
                const status = card.querySelector(".response-status");
                status.hidden = false;
                status.textContent = `Answered by ${formatProviderLabel(event.model_info)} ${event.model_info.model}`;
            }
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
            <div class="response-meta">
                <span>Error</span>
                <div class="response-tools">
                    <button class="delete-response" type="button" title="Delete this answer" aria-label="Delete this answer">X</button>
                </div>
            </div>
            <div class="response-body"></div>
        `;
        card.querySelector(".delete-response").addEventListener("click", () => {
            deleteResponseCard(event.action_id, card);
        });
        card.querySelector(".response-body").textContent = event.message || "Action failed.";
    }
}

export async function copyInterviewHistory(button) {
    const text = await requestInterviewHistory();
    if (!text) {
        flashButtonLabel(button, "Nothing yet");
        return;
    }
    try {
        await copyText(text);
        flashButtonLabel(button, "Copied");
    } catch (error) {
        console.warn("History copy failed", error);
        flashButtonLabel(button, "Copy failed");
    }
}

export function handleSessionExportEvent(event) {
    const pending = pendingHistoryExports.get(event.request_id);
    if (!pending) {
        return;
    }
    pendingHistoryExports.delete(event.request_id);
    pending.resolve(event.text || "");
}

function requestInterviewHistory() {
    const requestId = crypto.randomUUID();
    return new Promise((resolve) => {
        const timeout = window.setTimeout(() => {
            pendingHistoryExports.delete(requestId);
            resolve(formatInterviewHistory());
        }, 1500);
        pendingHistoryExports.set(requestId, {
            resolve: (text) => {
                window.clearTimeout(timeout);
                resolve(text || formatInterviewHistory());
            },
        });
        if (!sendJson({ type: "session.export_history", request_id: requestId })) {
            window.clearTimeout(timeout);
            pendingHistoryExports.delete(requestId);
            resolve(formatInterviewHistory());
        }
    });
}

function deleteResponseCard(actionId, card) {
    if (actionId) {
        sendJson({ type: "action.delete", action_id: actionId });
        state.responses.delete(actionId);
    }
    card.remove();
}

function formatInterviewHistory() {
    const transcriptLines = [...state.utterances.values()]
        .filter((item) => item.status === "final" && item.source === "other")
        .sort((a, b) => a.created_at - b.created_at)
        .map((item) => `Q: ${normalizeForExport(item.text)}`)
        .filter((line) => line !== "Q: ");

    const answerLines = [...state.responses.values()]
        .filter((item) => item.text && item.text.trim() && !item.cancelled)
        .sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
        .flatMap((item) => [
            `Q: ${normalizeForExport(item.sourceText || "(no source captured)")}`,
            `A: ${normalizeForExport(item.text)}`,
        ]);

    const sections = [];
    if (transcriptLines.length) {
        sections.push(["INTERVIEWER TRANSCRIPT", ...transcriptLines].join("\n"));
    }
    if (answerLines.length) {
        sections.push(["GENERATED ANSWERS", ...answerLines].join("\n"));
    }
    return sections.join("\n\n");
}

function normalizeForExport(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
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

async function copyText(text) {
    if (window.interviewCopilot?.copyText) {
        window.interviewCopilot.copyText(text);
        return;
    }

    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return;
        } catch (error) {
            console.warn("navigator.clipboard failed, trying fallback", error);
        }
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) {
        throw new Error("Clipboard fallback failed.");
    }
}

function flashButtonLabel(button, label) {
    const original = button.dataset.originalLabel || button.textContent;
    button.dataset.originalLabel = original;
    button.textContent = label;
    window.setTimeout(() => {
        button.textContent = original;
    }, 1200);
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
    const models = inspector.models || {};
    const plannedChain = Array.isArray(models.planned_chain) ? models.planned_chain : [];
    const priority = Array.isArray(inspector.context_priority) ? inspector.context_priority : [];
    const blockRows = Object.entries(blocks).map(([name, block]) => renderBlockRow(name, block)).join("");
    const priorityItems = priority.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    const plannedModels = plannedChain
        .map((attempt) => `${formatProviderLabel(attempt)} ${attempt.model || "model"}`)
        .join(" -> ");

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
                <span class="inspector-label">Estimated tokens</span>
                <strong>${formatNumber(promptChars.estimated_tokens || 0)} in / ${formatNumber(promptChars.max_output_tokens || 0)} out</strong>
            </div>
            <div>
                <span class="inspector-label">Prompt health</span>
                <strong class="prompt-health-${escapeHtml(assessment.level || "unknown")}">${escapeHtml(assessment.level || "unknown")}</strong>
            </div>
            <div>
                <span class="inspector-label">Mode</span>
                <strong>${escapeHtml(inspector.response_mode || "normal")}</strong>
            </div>
            <div>
                <span class="inspector-label">Action memory</span>
                <strong>${formatNumber(memory.completed_actions_used || 0)}/${formatNumber(memory.limit || 0)} used</strong>
            </div>
        </div>
        <div class="inspector-meta">
            Model: ${escapeHtml(models.primary || "unknown")}
            ${plannedModels ? ` | Planned: ${escapeHtml(plannedModels)}` : ""}
            ${models.groq_secondary ? ` | Groq secondary: ${escapeHtml(models.groq_secondary)}` : ""}
            ${models.openai_fallback ? ` | OpenAI fallback: ${escapeHtml(models.openai_fallback)}` : ""}
        </div>
        ${assessment.message ? `<div class="inspector-meta">${escapeHtml(assessment.message)}</div>` : ""}
        ${priorityItems ? `<ol class="inspector-priority">${priorityItems}</ol>` : ""}
        ${blockRows ? `<div class="inspector-blocks">${blockRows}</div>` : ""}
    `;
}

function formatProviderLabel(providerOrInfo) {
    if (providerOrInfo?.provider_label) {
        return providerOrInfo.provider_label;
    }
    return String(providerOrInfo?.provider || providerOrInfo || "provider").replaceAll("_", " ");
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
