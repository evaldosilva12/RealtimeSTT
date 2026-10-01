const { app, BrowserWindow, desktopCapturer, globalShortcut, ipcMain, session } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const APP_DIR = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(APP_DIR, "..");
const WS_PORT = "8015";

let controlWindow = null;
let overlayWindow = null;
let passControlWindow = null;
let backendProcess = null;
let passThrough = false;
let overlayLocked = false;
let overlayOpacity = 1;

function resolvePython() {
  const venvPython = path.join(REPO_ROOT, "venv", "Scripts", "python.exe");
  return fs.existsSync(venvPython) ? venvPython : "python";
}

function broadcast(channel, payload) {
  for (const window of [controlWindow, overlayWindow]) {
    if (window && !window.isDestroyed()) {
      window.webContents.send(channel, payload);
    }
  }
}

function startBackend() {
  if (backendProcess) {
    return;
  }

  const python = resolvePython();
  backendProcess = spawn(python, ["server.py"], {
    cwd: APP_DIR,
    env: {
      ...process.env,
      INTERVIEW_COPILOT_WS_PORT: WS_PORT,
      MEETING_COPILOT_HOST: "localhost",
    },
    windowsHide: false,
    stdio: ["ignore", "pipe", "pipe"],
  });

  broadcast("backend:status", { status: "starting", pid: backendProcess.pid });

  backendProcess.stdout.on("data", (data) => {
    broadcast("backend:status", { status: "running", stream: "stdout", message: data.toString() });
  });

  backendProcess.stderr.on("data", (data) => {
    broadcast("backend:status", { status: "running", stream: "stderr", message: data.toString() });
  });

  backendProcess.on("exit", (code) => {
    broadcast("backend:status", { status: "stopped", code });
    backendProcess = null;
  });

  backendProcess.on("error", (error) => {
    broadcast("backend:status", { status: "error", message: error.message });
    backendProcess = null;
  });
}

function stopBackend() {
  if (!backendProcess) {
    return;
  }
  backendProcess.kill();
  backendProcess = null;
}

function createControlWindow() {
  controlWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    title: "Interview Copilot Control",
    backgroundColor: "#0d1014",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  controlWindow.loadFile(path.join(APP_DIR, "index.html"));
  controlWindow.on("closed", () => {
    controlWindow = null;
  });
}

function createOverlayWindow() {
  overlayWindow = new BrowserWindow({
    width: 760,
    height: 460,
    minWidth: 420,
    minHeight: 240,
    x: 80,
    y: 80,
    title: "Interview Copilot Overlay",
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable: true,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  overlayWindow.setAlwaysOnTop(true, "screen-saver");
  overlayWindow.setOpacity(overlayOpacity);
  overlayWindow.loadFile(path.join(APP_DIR, "overlay.html"));
  overlayWindow.on("move", positionPassControlWindow);
  overlayWindow.on("resize", positionPassControlWindow);
  overlayWindow.on("show", updatePassControlVisibility);
  overlayWindow.on("hide", updatePassControlVisibility);
  overlayWindow.on("closed", () => {
    overlayWindow = null;
    updatePassControlVisibility();
  });
}

function createPassControlWindow() {
  passControlWindow = new BrowserWindow({
    width: 312,
    height: 46,
    show: false,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: true,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  passControlWindow.setAlwaysOnTop(true, "screen-saver");
  passControlWindow.loadFile(path.join(APP_DIR, "pass_controls.html"));
  passControlWindow.on("closed", () => {
    passControlWindow = null;
  });
}

function positionPassControlWindow() {
  if (!overlayWindow || !passControlWindow || overlayWindow.isDestroyed() || passControlWindow.isDestroyed()) {
    return;
  }

  const overlayBounds = overlayWindow.getBounds();
  const controlBounds = passControlWindow.getBounds();
  passControlWindow.setBounds({
    x: overlayBounds.x + overlayBounds.width - controlBounds.width - 12,
    y: overlayBounds.y + 10,
    width: controlBounds.width,
    height: controlBounds.height,
  });
}

function updatePassControlVisibility() {
  if (!passControlWindow || passControlWindow.isDestroyed()) {
    return;
  }

  const shouldShow = Boolean(overlayWindow && !overlayWindow.isDestroyed() && overlayWindow.isVisible() && (passThrough || overlayLocked));
  if (shouldShow) {
    positionPassControlWindow();
    passControlWindow.showInactive();
    passControlWindow.setAlwaysOnTop(true, "screen-saver");
  } else {
    passControlWindow.hide();
  }
}

function emitOverlayState() {
  broadcast("overlay:state", {
    passThrough,
    locked: overlayLocked,
    opacity: overlayOpacity,
    visible: Boolean(overlayWindow?.isVisible()),
  });
  updatePassControlVisibility();
}

function setPassThrough(value) {
  passThrough = value;
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.setIgnoreMouseEvents(passThrough, { forward: true });
  }
  emitOverlayState();
  return { passThrough, locked: overlayLocked, opacity: overlayOpacity };
}

function setOverlayLocked(value) {
  overlayLocked = value;
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.setMovable(!overlayLocked);
    overlayWindow.setResizable(!overlayLocked);
  }
  emitOverlayState();
  return { passThrough, locked: overlayLocked, opacity: overlayOpacity };
}

function setOverlayOpacity(value) {
  const parsed = Number(value);
  overlayOpacity = Math.min(1, Math.max(0.35, Number.isFinite(parsed) ? parsed : 1));
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.setOpacity(overlayOpacity);
  }
  emitOverlayState();
  return { passThrough, locked: overlayLocked, opacity: overlayOpacity };
}

function toggleOverlay() {
  if (!overlayWindow || overlayWindow.isDestroyed()) {
    createOverlayWindow();
    return;
  }
  if (overlayWindow.isVisible()) {
    overlayWindow.hide();
  } else {
    overlayWindow.showInactive();
    overlayWindow.setAlwaysOnTop(true, "screen-saver");
  }
  emitOverlayState();
}

function registerShortcuts() {
  globalShortcut.register("CommandOrControl+Shift+O", toggleOverlay);
  globalShortcut.register("CommandOrControl+Shift+P", () => setPassThrough(!passThrough));
  globalShortcut.register("CommandOrControl+Shift+C", () => {
    if (!controlWindow || controlWindow.isDestroyed()) {
      createControlWindow();
    }
    controlWindow.show();
    controlWindow.focus();
  });
  globalShortcut.register("CommandOrControl+Shift+L", () => setOverlayLocked(!overlayLocked));
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(["media", "display-capture"].includes(permission));
  });

  if (session.defaultSession.setDisplayMediaRequestHandler) {
    session.defaultSession.setDisplayMediaRequestHandler(async (_request, callback) => {
      const sources = await desktopCapturer.getSources({ types: ["screen"] });
      callback({ video: sources[0], audio: "loopback" });
    });
  }

  startBackend();
  createControlWindow();
  createOverlayWindow();
  createPassControlWindow();
  registerShortcuts();
});

ipcMain.handle("control:show", () => {
  if (!controlWindow || controlWindow.isDestroyed()) {
    createControlWindow();
  }
  controlWindow.show();
  controlWindow.focus();
});

ipcMain.handle("overlay:toggle", () => toggleOverlay());
ipcMain.handle("overlay:toggle-pass-through", () => setPassThrough(!passThrough));
ipcMain.handle("overlay:toggle-lock", () => setOverlayLocked(!overlayLocked));
ipcMain.handle("overlay:set-opacity", (_event, opacity) => setOverlayOpacity(opacity));
ipcMain.handle("overlay:get-state", () => ({
  passThrough,
  locked: overlayLocked,
  opacity: overlayOpacity,
  visible: Boolean(overlayWindow?.isVisible()),
}));

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  stopBackend();
});
