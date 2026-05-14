const controlState = document.getElementById("controlState");
const passButton = document.getElementById("togglePassThrough");
const lockButton = document.getElementById("toggleLock");
const controlButton = document.getElementById("showControl");

function renderState(state) {
    if (!state) {
        return;
    }

    passButton.classList.toggle("is-active", state.passThrough);
    lockButton.classList.toggle("is-active", state.locked);
    passButton.textContent = state.passThrough ? "Pass off" : "Pass on";
    lockButton.textContent = state.locked ? "Unlock" : "Lock";
    controlState.textContent = state.passThrough ? "Passing" : state.locked ? "Locked" : "Overlay";
}

passButton.addEventListener("click", async () => {
    renderState(await window.interviewCopilot?.togglePassThrough());
});

lockButton.addEventListener("click", async () => {
    renderState(await window.interviewCopilot?.toggleLock());
});

controlButton.addEventListener("click", () => {
    window.interviewCopilot?.showControl();
});

window.interviewCopilot?.onOverlayState(renderState);
window.interviewCopilot?.getOverlayState().then(renderState).catch(() => {});
