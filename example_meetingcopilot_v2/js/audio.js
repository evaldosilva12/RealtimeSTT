import { sendAudioChunk, sendJson } from "./socket.js";

let othersStream = null;
let othersContext = null;
let othersNode = null;
let mutedGain = null;
let speechRecognition = null;

export async function listAudioInputs(onStatus) {
    try {
        const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        for (const track of permissionStream.getTracks()) {
            track.stop();
        }
    } catch {
        onStatus("Audio permission needed to show device names");
    }

    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((device) => device.kind === "audioinput");
}

export async function startSelectedAudioInput(deviceId, onStatus, onLevel = () => {}) {
    if (othersStream) {
        return;
    }

    if (!deviceId) {
        throw new Error("Choose an audio input first.");
    }

    onStatus("Opening selected input");
    othersStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            deviceId: { exact: deviceId },
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
        },
    });
    await startStreamCapture(othersStream, onStatus, "Capturing selected input", onLevel);
}

export async function startScreenAudio(onStatus, onLevel = () => {}) {
    if (othersStream) {
        return;
    }

    onStatus("Requesting shared audio");
    othersStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
    });
    for (const track of othersStream.getVideoTracks()) {
        track.stop();
    }
    await startStreamCapture(othersStream, onStatus, "Capturing shared screen audio", onLevel);
}

async function startStreamCapture(stream, onStatus, readyLabel, onLevel) {
    othersContext = new AudioContext();
    await othersContext.resume();
    await othersContext.audioWorklet.addModule("js/pcm-worklet.js");
    const source = othersContext.createMediaStreamSource(stream);
    othersNode = new AudioWorkletNode(othersContext, "pcm-worklet");
    othersNode.port.onmessage = (event) => {
        if (event.data && event.data.type === "level") {
            onLevel(event.data.rms, event.data.peak, event.data.pcmPeak || 0);
            return;
        }
        sendAudioChunk("others_audio", othersContext.sampleRate, event.data);
    };
    mutedGain = othersContext.createGain();
    mutedGain.gain.value = 0;
    source.connect(othersNode);
    othersNode.connect(mutedGain);
    mutedGain.connect(othersContext.destination);
    onStatus(readyLabel);
}

export async function stopOthersAudio(onStatus) {
    if (othersNode) {
        othersNode.disconnect();
        othersNode = null;
    }
    if (mutedGain) {
        mutedGain.disconnect();
        mutedGain = null;
    }
    if (othersContext) {
        await othersContext.close();
        othersContext = null;
    }
    if (othersStream) {
        for (const track of othersStream.getTracks()) {
            track.stop();
        }
        othersStream = null;
    }
    onStatus("Stopped");
}

export function startMyHiddenContext(onStatus) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        onStatus("Hidden context speech recognition unavailable");
        return;
    }
    if (speechRecognition) {
        return;
    }

    speechRecognition = new SpeechRecognition();
    speechRecognition.continuous = true;
    speechRecognition.interimResults = false;
    speechRecognition.lang = "en-US";
    speechRecognition.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
            if (!event.results[i].isFinal) {
                continue;
            }
            const text = event.results[i][0].transcript.trim();
            if (text) {
                sendJson({ type: "context.my_note", text });
            }
        }
    };
    speechRecognition.onend = () => {
        if (speechRecognition) {
            try {
                speechRecognition.start();
            } catch {
                speechRecognition = null;
            }
        }
    };
    speechRecognition.start();
    onStatus("Capturing my hidden context");
}

export function stopMyHiddenContext(onStatus) {
    if (speechRecognition) {
        const current = speechRecognition;
        speechRecognition = null;
        current.onend = null;
        current.stop();
    }
    onStatus("My hidden context stopped");
}
