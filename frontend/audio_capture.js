// Microphone capture that produces 16 kHz mono WAV, the format FirstVoice's
// models use. Recording WAV in the browser means the server never needs ffmpeg.
class MicCapture {
    constructor({ bufferSeconds = 30 } = {}) {
        this.bufferSeconds = bufferSeconds;
        this.context = null;
        this.stream = null;
        this.chunks = [];          // Float32Array blocks at the device sample rate
        this.totalSamples = 0;
        this.recording = null;     // samples collected by start/stopRecording
    }

    get sampleRate() { return this.context ? this.context.sampleRate : 48000; }
    get active() { return Boolean(this.context); }

    async start() {
        if (this.context) return;
        this.stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
        });
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        this.context = new AudioContextClass();
        const source = this.context.createMediaStreamSource(this.stream);
        this.processor = this.context.createScriptProcessor(4096, 1, 1);
        this.processor.onaudioprocess = (event) => {
            const block = new Float32Array(event.inputBuffer.getChannelData(0));
            this.chunks.push(block);
            this.totalSamples += block.length;
            if (this.recording) this.recording.push(block);
            const maxSamples = this.bufferSeconds * this.sampleRate;
            while (this.totalSamples - this.chunks[0].length > maxSamples) {
                this.totalSamples -= this.chunks.shift().length;
            }
        };
        source.connect(this.processor);
        // Route through a muted gain node so the processor runs without echoing the mic.
        const mute = this.context.createGain();
        mute.gain.value = 0;
        this.processor.connect(mute).connect(this.context.destination);
    }

    stop() {
        if (this.processor) this.processor.disconnect();
        if (this.stream) this.stream.getTracks().forEach(track => track.stop());
        if (this.context) this.context.close();
        this.context = null;
        this.stream = null;
        this.processor = null;
        this.chunks = [];
        this.totalSamples = 0;
        this.recording = null;
    }

    startRecording() { this.recording = []; }

    stopRecording() {
        const blocks = this.recording || [];
        this.recording = null;
        return MicCapture.toWav(MicCapture.concat(blocks), this.sampleRate);
    }

    // Last `seconds` of audio from the rolling buffer.
    latest(seconds) {
        const wanted = Math.floor(seconds * this.sampleRate);
        const out = new Float32Array(Math.min(wanted, this.totalSamples));
        let offset = out.length;
        for (let i = this.chunks.length - 1; i >= 0 && offset > 0; i--) {
            const block = this.chunks[i];
            const take = Math.min(block.length, offset);
            out.set(block.subarray(block.length - take), offset - take);
            offset -= take;
        }
        return out;
    }

    latestWav(seconds) { return MicCapture.toWav(this.latest(seconds), this.sampleRate); }

    // Loudness of the last `seconds` in dBFS.
    levelDb(seconds = 0.5) {
        const samples = this.latest(seconds);
        if (!samples.length) return -100;
        let sum = 0;
        for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
        return 10 * Math.log10(sum / samples.length + 1e-12);
    }

    static concat(blocks) {
        const length = blocks.reduce((n, b) => n + b.length, 0);
        const out = new Float32Array(length);
        let offset = 0;
        blocks.forEach(b => { out.set(b, offset); offset += b.length; });
        return out;
    }

    static resample(samples, fromRate, toRate = 16000) {
        if (fromRate === toRate) return samples;
        const ratio = fromRate / toRate;
        const out = new Float32Array(Math.floor(samples.length / ratio));
        for (let i = 0; i < out.length; i++) {
            // Average the source samples that fall in this output slot (simple anti-aliasing).
            const start = Math.floor(i * ratio), end = Math.min(samples.length, Math.floor((i + 1) * ratio));
            let sum = 0;
            for (let j = start; j < end; j++) sum += samples[j];
            out[i] = end > start ? sum / (end - start) : samples[start] || 0;
        }
        return out;
    }

    static toWav(samples, sampleRate, targetRate = 16000) {
        const data = MicCapture.resample(samples, sampleRate, targetRate);
        const buffer = new ArrayBuffer(44 + data.length * 2);
        const view = new DataView(buffer);
        const writeString = (offset, text) => { for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i)); };
        writeString(0, 'RIFF'); view.setUint32(4, 36 + data.length * 2, true); writeString(8, 'WAVE');
        writeString(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
        view.setUint32(24, targetRate, true); view.setUint32(28, targetRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
        writeString(36, 'data'); view.setUint32(40, data.length * 2, true);
        for (let i = 0; i < data.length; i++) {
            const s = Math.max(-1, Math.min(1, data[i]));
            view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        }
        return new Blob([view], { type: 'audio/wav' });
    }
}

// Shared result rendering helpers used by the dashboard and hands-free mode.
const FirstVoiceUI = {
    escape(text) {
        return String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    },

    drawSpectrogram(canvas, spectrogram) {
        if (!canvas || !spectrogram || !spectrogram.frames) return;
        const { bands, frames, values } = spectrogram;
        canvas.width = frames;
        canvas.height = bands;
        const ctx = canvas.getContext('2d');
        const image = ctx.createImageData(frames, bands);
        for (let b = 0; b < bands; b++) {
            for (let f = 0; f < frames; f++) {
                const v = values[b * frames + f] / 255;
                const y = bands - 1 - b;   // low frequencies at the bottom
                const i = (y * frames + f) * 4;
                image.data[i] = Math.round(255 * Math.min(1, v * 1.6));
                image.data[i + 1] = Math.round(255 * Math.max(0, v - 0.35) * 1.4);
                image.data[i + 2] = Math.round(120 * (1 - v) + 60 * v);
                image.data[i + 3] = 255;
            }
        }
        ctx.putImageData(image, 0, 0);
    },

    featureChips(features) {
        if (!features) return '';
        const chips = [
            features.pitch_hz ? ['Pitch', `${features.pitch_hz} Hz`] : null,
            features.pitch_peak_hz ? ['Peak pitch', `${features.pitch_peak_hz} Hz`] : null,
            features.energy_db !== undefined ? ['Energy', `${features.energy_db} dB`] : null,
            features.bursts_per_s !== undefined ? ['Rhythm', `${features.bursts_per_s}/s`] : null,
            features.duration_s ? ['Length', `${features.duration_s} s`] : null,
        ].filter(Boolean);
        return chips.map(([k, v]) => `<span class="feature-chip"><em>${k}</em><b>${v}</b></span>`).join('');
    },

    speak(text) {
        if (!('speechSynthesis' in window)) return;
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.95;
        window.speechSynthesis.speak(utterance);
    },

    spokenSummary(event, name) {
        const plan = (event.action_plan || [])[0] || event.recommendation || '';
        return `${name} is most likely ${event.prediction.toLowerCase()}. ${plan}`;
    },
};
