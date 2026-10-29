// Hands-free mode: energy gate -> YAMNet cry detection -> full analysis -> spoken result.
const $ = (id) => document.getElementById(id);
const TICK_MS = 250;
const CHECK_INTERVAL_MS = 900;
const PRE_ROLL_S = 2.5;          // keep the start of the cry
const MIN_CAPTURE_S = 4;
const MAX_CAPTURE_S = 10;

let mic = null;
let state = 'off';               // off | listening | capturing | analyzing | cooldown
let timer = null;
let noiseFloor = -60;
let lastCheck = 0;
let checking = false;
let cryHits = 0;
let quietChecks = 0;
let captureStart = 0;
let cooldownUntil = 0;
let wakeLock = null;
let babyName = 'your baby';
let lastEvent = null;
const log = [];

document.addEventListener('DOMContentLoaded', async () => {
    try {
        const response = await fetch('/api/firstvoice/dashboard');
        if (response.status === 401) { window.location.href = '/login'; return; }
        const data = await response.json();
        babyName = data.profile.name || babyName;
        $('hf-name').textContent = babyName;
    } catch (error) { console.error(error); }
});

function setState(next, detail) {
    state = next;
    const labels = { off: 'Off', listening: 'Listening', capturing: 'Cry detected, recording', analyzing: 'Analyzing', cooldown: 'Paused' };
    const icons = { off: 'microphone-slash', listening: 'ear-listen', capturing: 'baby', analyzing: 'spinner fa-spin', cooldown: 'pause' };
    $('hf-state').textContent = labels[next];
    $('hf-detail').textContent = detail || '';
    $('hf-orb').className = `hf-orb ${next}`;
    $('hf-orb').innerHTML = `<i class="fa-solid fa-${icons[next]}"></i>`;
}

async function toggleMonitoring() {
    if (state !== 'off') return stopMonitoring();
    try {
        mic = new MicCapture({ bufferSeconds: 15 });
        await mic.start();
    } catch (error) {
        setState('off', 'Microphone permission was not granted.');
        return;
    }
    try { wakeLock = await navigator.wakeLock?.request('screen'); } catch (error) { wakeLock = null; }
    noiseFloor = -60;
    cryHits = 0;
    setState('listening', 'Quiet room. Waiting for crying.');
    $('toggle-btn').innerHTML = '<i class="fa-solid fa-stop"></i> Stop listening';
    $('toggle-btn').classList.add('stop');
    timer = setInterval(tick, TICK_MS);
}

function stopMonitoring() {
    clearInterval(timer);
    timer = null;
    if (mic) mic.stop();
    mic = null;
    try { wakeLock?.release(); } catch (error) { /* ignore */ }
    setState('off', 'Tap Start to begin listening.');
    $('toggle-btn').innerHTML = '<i class="fa-solid fa-play"></i> Start listening';
    $('meter-fill').style.width = '0%';
}

function updateMeter(level, gate) {
    const toPct = (db) => Math.max(0, Math.min(100, (db + 80) / 80 * 100));
    $('meter-fill').style.width = `${toPct(level)}%`;
    $('meter-gate').style.left = `${toPct(gate)}%`;
    $('level-db').textContent = `${Math.round(level)} dB`;
}

async function checkForCry(seconds) {
    const form = new FormData();
    form.append('audio', mic.latestWav(seconds), 'window.wav');
    const response = await fetch('/api/firstvoice/detect', { method: 'POST', body: form });
    if (!response.ok) throw new Error('Detection failed');
    return response.json();
}

async function tick() {
    if (!mic) return;
    const now = Date.now();
    const level = mic.levelDb(0.5);
    // Track the background level: fall quickly, rise slowly.
    noiseFloor = level < noiseFloor ? level : noiseFloor + 0.03;
    const gate = noiseFloor + Number($('sensitivity').value);
    updateMeter(level, gate);

    if (state === 'cooldown' && now >= cooldownUntil) setState('listening', 'Waiting for crying.');
    if (checking || state === 'analyzing' || state === 'cooldown' || state === 'off') return;
    if (now - lastCheck < CHECK_INTERVAL_MS) return;

    if (state === 'listening') {
        if (level < gate) { cryHits = Math.max(0, cryHits - 1); return; }
        checking = true;
        lastCheck = now;
        try {
            const detection = await checkForCry(2);
            cryHits = detection.cry_detected ? cryHits + 1 : 0;
            $('hf-detail').textContent = detection.cry_detected
                ? `Possible cry (${Math.round(detection.cry_score * 100)}%), checking again...`
                : `Heard: ${detection.top_sounds?.[0]?.label || 'noise'}. Not a cry.`;
            if (cryHits >= 2 || detection.cry_score >= 0.5) {
                captureStart = now - PRE_ROLL_S * 1000;
                quietChecks = 0;
                setState('capturing', 'Recording the cry for analysis...');
                addLog('Cry detected', `${Math.round(detection.cry_score * 100)}% cry score`);
            }
        } catch (error) { console.error(error); }
        checking = false;
    } else if (state === 'capturing') {
        const elapsed = (now - captureStart) / 1000;
        checking = true;
        lastCheck = now;
        try {
            const detection = await checkForCry(1.5);
            quietChecks = detection.cry_detected ? 0 : quietChecks + 1;
        } catch (error) { quietChecks += 1; }
        checking = false;
        if (elapsed >= MAX_CAPTURE_S || (elapsed >= MIN_CAPTURE_S && quietChecks >= 2)) {
            await analyzeCapture(Math.min(MAX_CAPTURE_S, elapsed));
        }
    }
}

async function analyzeCapture(seconds) {
    setState('analyzing', 'Running the cry model...');
    const form = new FormData();
    form.append('audio', mic.latestWav(seconds), 'handsfree.wav');
    form.append('source', 'auto');
    try {
        const response = await fetch('/api/firstvoice/analyze', { method: 'POST', body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Analysis failed');
        lastEvent = data.event;
        renderResult(lastEvent);
        addLog(lastEvent.prediction, `${lastEvent.confidence}% · ${lastEvent.action_plan?.[0] || ''}`);
        if ($('speak-toggle').checked) FirstVoiceUI.speak(FirstVoiceUI.spokenSummary(lastEvent, babyName));
    } catch (error) {
        addLog('Analysis failed', error.message);
    }
    cooldownUntil = Date.now() + Number($('cooldown').value) * 1000;
    cryHits = 0;
    if (state !== 'off') setState('cooldown', `Listening again in ${$('cooldown').value} s.`);
}

function renderResult(event) {
    const esc = FirstVoiceUI.escape;
    const plan = (event.action_plan || []).map(step => `<li>${esc(step)}</li>`).join('');
    const why = (event.explanation || []).slice(0, 3).map(line => `<li>${esc(line)}</li>`).join('');
    const time = new Date(event.timestamp).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    $('hf-result').innerHTML = `<div class="panel-heading"><div><p class="eyebrow">LATEST CRY · ${time}</p><h2>Possible reason right now</h2></div><span class="confidence-badge">${event.confidence}% likely</span></div>
        <div class="result-main"><span class="result-orb"><i class="fa-solid fa-wave-square"></i></span><div><span class="result-label">Most likely</span><strong>${esc(event.prediction)}</strong><span class="result-sub">${event.personalization?.examples ? `Personalized with ${event.personalization.examples} of ${esc(babyName)}'s reviewed cries` : 'General model'}</span></div></div>
        <div class="solution-callout"><span><i class="fa-solid fa-list-check"></i> What to try next</span><ol class="action-plan">${plan}</ol></div>
        ${why ? `<div class="why-block"><span><i class="fa-solid fa-magnifying-glass"></i> Why</span><ul>${why}</ul></div>` : ''}
        ${event.spectrogram?.frames ? `<div class="acoustic-block" style="margin-top:14px"><canvas id="hf-spectrogram" class="spectrogram"></canvas><div class="feature-chips">${FirstVoiceUI.featureChips(event.features)}</div></div>` : ''}
        <div class="hf-feedback" id="hf-feedback"><span>Was it right?</span><button class="yes" onclick="sendFeedback(true)">Yes</button>${['Hungry', 'Tired', 'Discomfort', 'Pain'].filter(c => c !== event.prediction).map(c => `<button onclick="sendFeedback(false, '${c}')">${c}</button>`).join('')}</div>`;
    FirstVoiceUI.drawSpectrogram($('hf-spectrogram'), event.spectrogram);
}

async function sendFeedback(confirmed, corrected = null) {
    if (!lastEvent) return;
    const response = await fetch('/api/firstvoice/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: lastEvent.id, confirmed, corrected_cause: corrected }),
    });
    if (response.ok) {
        $('hf-feedback').innerHTML = `<span class="saved-feedback"><i class="fa-solid fa-check"></i> Saved. ${confirmed ? 'Confirmed' : `Corrected to ${corrected}`}; the next cry will be personalized with it.</span>`;
    }
}

function addLog(title, detail) {
    log.unshift({ time: new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }), title, detail });
    $('hf-log').innerHTML = log.slice(0, 20).map(item => `<li><span><b>${FirstVoiceUI.escape(item.title)}</b> ${FirstVoiceUI.escape(item.detail)}</span><em>${item.time}</em></li>`).join('');
}
