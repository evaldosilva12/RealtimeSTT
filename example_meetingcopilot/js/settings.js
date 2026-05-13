import { state } from "./state.js";
import { sendJson } from "./socket.js";

const dialog = document.getElementById("settingsDialog");
const openSettings = document.getElementById("openSettings");
const systemRole = document.getElementById("systemRole");
const additionalInfo = document.getElementById("additionalInfo");
const includeHiddenContext = document.getElementById("includeHiddenContext");
const saveSetup = document.getElementById("saveSetup");
const manualHiddenContext = document.getElementById("manualHiddenContext");
const saveManualContext = document.getElementById("saveManualContext");
const hiddenContextLog = document.getElementById("hiddenContextLog");

export function initSettings() {
    openSettings.addEventListener("click", () => dialog.showModal());
    saveSetup.addEventListener("click", () => {
        sendJson({
            type: "setup.save",
            setup: {
                system_role: systemRole.value,
                additional_info: additionalInfo.value,
                include_hidden_context: includeHiddenContext.checked,
            },
        });
    });
    saveManualContext.addEventListener("click", () => {
        const text = manualHiddenContext.value.trim();
        if (!text) {
            return;
        }
        sendJson({ type: "context.my_note", text });
        manualHiddenContext.value = "";
    });
}

export function renderSetup(setup) {
    state.setup = setup;
    systemRole.value = setup.system_role || "";
    additionalInfo.value = setup.additional_info || "";
    includeHiddenContext.checked = Boolean(setup.include_hidden_context);
}

export function appendHiddenContext(text) {
    state.hiddenContext.push(text);
    const item = document.createElement("div");
    item.textContent = text;
    hiddenContextLog.appendChild(item);
}
