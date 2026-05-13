class PcmWorklet extends AudioWorkletProcessor {
    constructor() {
        super();
        this.framesSinceLevel = 0;
    }

    process(inputs) {
        const input = inputs[0];
        if (!input || !input[0]) {
            return true;
        }

        const channel = input[0];
        const output = new Int16Array(channel.length);
        let sumSquares = 0;
        let peak = 0;
        let pcmPeak = 0;
        for (let i = 0; i < channel.length; i += 1) {
            const sample = Math.max(-1, Math.min(1, channel[i]));
            const abs = Math.abs(sample);
            sumSquares += sample * sample;
            if (abs > peak) {
                peak = abs;
            }
            output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            const pcmAbs = Math.abs(output[i]);
            if (pcmAbs > pcmPeak) {
                pcmPeak = pcmAbs;
            }
        }
        this.framesSinceLevel += channel.length;
        if (this.framesSinceLevel >= sampleRate / 10) {
            this.framesSinceLevel = 0;
            this.port.postMessage({
                type: "level",
                rms: Math.sqrt(sumSquares / channel.length),
                peak,
                pcmPeak,
            });
        }
        this.port.postMessage(output, [output.buffer]);
        return true;
    }
}

registerProcessor("pcm-worklet", PcmWorklet);
