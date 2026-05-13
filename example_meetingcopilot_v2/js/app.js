import { handleActionEvent, requestAction, requestLastN } from "./actions.js";
import {
    listAudioInputs,
    startMyHiddenContext,
    startScreenAudio,
    startSelectedAudioInput,
    stopMyHiddenContext,
    stopOthersAudio,
} from "./audio.js";
import { connectSocket } from "./socket.js";
import { state } from "./state.js";
import { appendHiddenContext, initSettings, renderSetup } from "./settings.js";
import { renderFinal, renderPartial, setUtteranceClickHandler } from "./transcript.js";

const socketStatus = document.getElementById("socketStatus");
const sttStatus = document.getElementById("sttStatus");
const llmStatus = document.getElementById("llmStatus");
const audioLevelBar = document.getElementById("audioLevelBar");
const audioLevelText = document.getElementById("audioLevelText");
const debugInput = document.getElementById("debugInput");
const debugPcm = document.getElementById("debugPcm");
const debugRawPeak = document.getElementById("debugRawPeak");
const debugResampledPeak = document.getElementById("debugResampledPeak");
const debugSocket = document.getElementById("debugSocket");

function setPill(element, text, mode = "") {
    element.textContent = text;
    element.className = `status-pill ${mode}`.trim();
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
        debugSocket.textContent = event.status;
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
        if (event.setup) {
            renderSetup(event.setup);
        }
        if (event.error || event.warning) {
            console.warn(event.error || event.warning);
        }
        return;
    }

    if (event.type === "setup.current") {
        renderSetup(event.setup);
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

    if (event.type === "audio.debug") {
        debugRawPeak.textContent = String(event.raw_peak || 0);
        debugResampledPeak.textContent = String(event.resampled_peak || 0);
        return;
    }

    if (event.type.startsWith("action.")) {
        handleActionEvent(event);
        return;
    }

    if (event.type === "context.my_note.saved") {
        appendHiddenContext(event.text);
    }
}

function initActions() {
    setUtteranceClickHandler((utterance, actionType = "answer_as_me") => {
        requestAction(actionType, {
            text: utterance.text,
            utterance_id: utterance.utterance_id,
        });
    });

    document.querySelectorAll(".last-n").forEach((button) => {
        button.addEventListener("click", () => requestLastN(Number(button.dataset.count)));
    });
    document.getElementById("answerRecent").addEventListener("click", () => requestLastN(4, "answer_last_n"));
    document.getElementById("clarifyRecent").addEventListener("click", () => requestLastN(4, "clarify"));
    document.getElementById("askRecent").addEventListener("click", () => requestLastN(4, "ask_question"));
    document.getElementById("pushBackRecent").addEventListener("click", () => requestLastN(4, "push_back"));
    document.getElementById("summarizeContext").addEventListener("click", () => requestLastN(8, "summarize_context"));
    document.getElementById("nextStepAction").addEventListener("click", () => requestLastN(6, "next_step"));
    document.getElementById("objectionResponse").addEventListener("click", () => requestLastN(5, "objection_response"));
    document.getElementById("screenshotAction").addEventListener("click", () => requestAction("screenshot_code_help"));
}

function initAudioControls() {
    const audioInputSelect = document.getElementById("audioInputSelect");
    const refreshInputs = document.getElementById("refreshAudioInputs");
    const startOthers = document.getElementById("startOthersAudio");
    const startScreen = document.getElementById("startScreenAudio");
    const stopOthers = document.getElementById("stopOthersAudio");
    const startMine = document.getElementById("startMyContext");
    const stopMine = document.getElementById("stopMyContext");

    async function refreshAudioInputs() {
        const devices = await listAudioInputs((status) => setPill(sttStatus, status, "status-warn"));
        audioInputSelect.innerHTML = "";
        for (const device of devices) {
            const option = document.createElement("option");
            option.value = device.deviceId;
            option.textContent = device.label || `Audio input ${audioInputSelect.length + 1}`;
            audioInputSelect.appendChild(option);
        }

        const preferred = [...audioInputSelect.options].find((option) =>
            /voicemeeter out b2/i.test(option.textContent),
        ) || [...audioInputSelect.options].find((option) =>
            /voicemeeter/i.test(option.textContent),
        );
        if (preferred) {
            audioInputSelect.value = preferred.value;
            debugInput.textContent = preferred.textContent;
        }
    }

    refreshInputs.addEventListener("click", async () => {
        try {
            await refreshAudioInputs();
            setPill(sttStatus, "Inputs refreshed", "status-ok");
        } catch (error) {
            setPill(sttStatus, error.message, "status-error");
        }
    });

    function renderAudioLevel(rms, peak, pcmPeak) {
        const percent = Math.min(100, Math.round(peak * 100));
        audioLevelBar.style.width = `${percent}%`;
        audioLevelText.textContent = peak > 0.01 ? `${percent}%` : "low signal";
        debugPcm.textContent = String(pcmPeak || 0);
    }

    audioInputSelect.addEventListener("change", () => {
        const label = audioInputSelect.options[audioInputSelect.selectedIndex]?.textContent || "input";
        debugInput.textContent = label;
        setPill(sttStatus, `Selected ${label}`, "status-warn");
    });

    startOthers.addEventListener("click", async () => {
        try {
            await startSelectedAudioInput(
                audioInputSelect.value,
                (status) => setPill(sttStatus, status, "status-ok"),
                renderAudioLevel,
            );
        } catch (error) {
            setPill(sttStatus, error.message, "status-error");
        }
    });
    startScreen.addEventListener("click", async () => {
        try {
            await startScreenAudio((status) => setPill(sttStatus, status, "status-ok"), renderAudioLevel);
        } catch (error) {
            setPill(sttStatus, error.message, "status-error");
        }
    });
    stopOthers.addEventListener("click", () => stopOthersAudio((status) => setPill(sttStatus, status, "status-warn")));
    startMine.addEventListener("click", () => startMyHiddenContext((status) => setPill(llmStatus, status, "status-ok")));
    stopMine.addEventListener("click", () => stopMyHiddenContext((status) => setPill(llmStatus, status, "status-warn")));
    refreshAudioInputs().catch(() => {});
}

initSettings();
initActions();
initAudioControls();
connectSocket(handleEvent);
