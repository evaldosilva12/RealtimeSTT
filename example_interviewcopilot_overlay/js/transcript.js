import { state } from "./state.js";
import { sendJson } from "./socket.js";

const timeline = document.getElementById("timeline");
const partial = document.getElementById("partialTranscript");

let onUtteranceClick = () => {};
let livePreview = null;

export function setUtteranceClickHandler(handler) {
    onUtteranceClick = handler;
}

export function renderPartial(event) {
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

export function renderFinal(event) {
    livePreview = null;
    partial.hidden = true;
    if (!event.text || !event.text.trim()) {
        return;
    }
    const chunks = splitFinalText(event.text);
    chunks.forEach((text, index) => {
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

export function renderTimeline() {
    const finals = sortedFinalUtterances();
    const items = livePreview ? [livePreview, ...finals] : finals;
    timeline.innerHTML = "";

    for (const item of items) {
        if (item.source !== "other") {
            continue;
        }
        const card = document.createElement("article");
        card.className = item.isLive ? "utterance live" : "utterance";
        card.tabIndex = 0;
        card.role = "button";
        card.dataset.utteranceId = item.utterance_id;
        card.dataset.status = item.status;

        const time = document.createElement("time");
        time.textContent = new Date(item.created_at * 1000).toLocaleTimeString();
        const text = document.createElement("div");
        text.className = "utterance-text";
        text.textContent = item.text;
        const header = document.createElement("div");
        header.className = "utterance-header";
        if (!item.isLive) {
            header.appendChild(time);
        }

        if (!item.isLive) {
            const actions = document.createElement("div");
            actions.className = "utterance-actions";
            [
                ["Clarify", "clarify"],
                ["Ask", "ask_question"],
                ["Example", "give_example"],
                ["Recover", "recover_answer"],
                ["Push back", "push_back"],
            ].forEach(([label, actionType]) => {
                const actionButton = document.createElement("button");
                actionButton.type = "button";
                actionButton.textContent = label;
                actionButton.addEventListener("click", (event) => {
                    event.stopPropagation();
                    onUtteranceClick(item, actionType);
                });
                actions.appendChild(actionButton);
            });
            const previousItem = previousFinalUtterance(item.utterance_id);
            if (previousItem) {
                const mergeButton = document.createElement("button");
                mergeButton.type = "button";
                mergeButton.textContent = "Merge prev";
                mergeButton.title = "Merge this transcript with the previous one";
                mergeButton.addEventListener("click", (event) => {
                    event.stopPropagation();
                    requestMergeWithPrevious(item, previousItem);
                });
                actions.appendChild(mergeButton);
            }
            const deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.className = "delete-utterance";
            deleteButton.textContent = "X";
            deleteButton.title = "Delete this transcript";
            deleteButton.setAttribute("aria-label", "Delete this transcript");
            deleteButton.addEventListener("click", (event) => {
                event.stopPropagation();
                requestTranscriptDelete(item);
            });
            actions.appendChild(deleteButton);
            header.appendChild(actions);
        }
        if (item.isLive) {
            card.appendChild(text);
        } else {
            card.append(header, text);
        }
        card.addEventListener("click", () => onUtteranceClick(item, "answer_as_me"));
        card.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onUtteranceClick(item, "answer_as_me");
            }
        });
        timeline.appendChild(card);
    }
}

export function applyTranscriptMerge(event) {
    const current = state.utterances.get(event.utterance_id);
    const previous = state.utterances.get(event.previous_utterance_id);
    if (!current || !previous) {
        return;
    }
    state.utterances.set(event.utterance_id, {
        ...current,
        text: event.text,
    });
    state.utterances.delete(event.previous_utterance_id);
    renderTimeline();
}

export function applyTranscriptDelete(event) {
    if (!state.utterances.has(event.utterance_id)) {
        return;
    }
    state.utterances.delete(event.utterance_id);
    renderTimeline();
}

function requestMergeWithPrevious(item, previousItem) {
    const mergedText = `${previousItem.text} ${item.text}`.replace(/\s+/g, " ").trim();
    sendJson({
        type: "transcript.merge_previous",
        utterance_id: item.utterance_id,
        previous_utterance_id: previousItem.utterance_id,
        merged_text: mergedText,
    });
}

export function requestTranscriptDelete(item) {
    const replacementText = siblingUtterances(item.utterance_id)
        .filter((sibling) => sibling.utterance_id !== item.utterance_id)
        .sort((a, b) => a.created_at - b.created_at)
        .map((sibling) => sibling.text)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    sendJson({
        type: "transcript.delete",
        utterance_id: item.utterance_id,
        replacement_text: replacementText,
    });
}

function siblingUtterances(utteranceId) {
    const baseId = storageUtteranceId(utteranceId);
    return [...state.utterances.values()].filter((item) => storageUtteranceId(item.utterance_id) === baseId);
}

function storageUtteranceId(utteranceId) {
    return utteranceId.replace(/-\d+$/, "");
}

function sortedFinalUtterances() {
    return [...state.utterances.values()]
        .filter((item) => item.status === "final" && item.source === "other")
        .sort((a, b) => b.created_at - a.created_at);
}

function previousFinalUtterance(utteranceId) {
    const finals = sortedFinalUtterances();
    const index = finals.findIndex((item) => item.utterance_id === utteranceId);
    return index >= 0 ? finals[index + 1] : null;
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
    if (normalized.length <= 360) {
        return normalized;
    }
    return `...${normalized.slice(-360)}`;
}
