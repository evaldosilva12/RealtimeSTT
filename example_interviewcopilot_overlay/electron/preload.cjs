const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("interviewCopilot", {
  webSocketUrl: "ws://localhost:8015",
  showControl: () => ipcRenderer.invoke("control:show"),
  toggleOverlay: () => ipcRenderer.invoke("overlay:toggle"),
  togglePassThrough: () => ipcRenderer.invoke("overlay:toggle-pass-through"),
  toggleLock: () => ipcRenderer.invoke("overlay:toggle-lock"),
  setOpacity: (opacity) => ipcRenderer.invoke("overlay:set-opacity", opacity),
  getOverlayState: () => ipcRenderer.invoke("overlay:get-state"),
  onBackendStatus: (callback) => {
    ipcRenderer.on("backend:status", (_event, status) => callback(status));
  },
  onOverlayState: (callback) => {
    ipcRenderer.on("overlay:state", (_event, state) => callback(state));
  },
});
