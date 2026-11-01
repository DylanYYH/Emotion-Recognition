let selectedDemo = 'evening';
let selectedSource = 'live';
let selectedAge = 4;
let selectedPatternPeriod = 'week';
let activeEvent = null;
let currentProfile = { name: 'Luca', age_months: 4 };
let mic = null;
let savedAudioReference = null;
let demoPreviewNode = null;
let demoPreviewContext = null;
let profileSetupRequired = false;

const $ = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', () => {
    setSource('live');
    loadDashboard();
});

async function api(url, options = {}) {
    const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
    const response = await fetch(url, { headers, ...options });
    if (response.status === 401) { window.location.href = '/login'; return null; }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed');
    return data;
}

async function loadDashboard() {
    try {
        const data = await api(`/api/firstvoice/dashboard?age_months=${selectedAge}&period=${selectedPatternPeriod}`);
        if (data) renderDashboard(data);
    } catch (error) { console.error(error); }
}

function renderDashboard(data) {
    currentProfile = data.profile;
    profileSetupRequired = Boolean(data.profile_setup_required);
    $('user-name').textContent = data.username || '';
    selectedAge = data.profile.age_months;
    $('hero-name').textContent = currentProfile.name;
    $('profile-name').textContent = currentProfile.name;
    $('profile-age').textContent = formatAge(currentProfile.age_months);
    renderProfilePhoto(currentProfile);
    $('event-count').textContent = data.stats.events;
    $('confirmed-count').textContent = data.stats.confirmed;
    $('corrected-count').textContent = data.stats.corrected;
    $('weekly-count').textContent = data.stats.recent_events ?? data.stats.events;
    const causes = data.stats.causes || {};
    const recentReasons = data.recent_patterns?.reasons || [];
    $('common-cause').textContent = recentReasons[0]?.label || Object.keys(causes).sort((a, b) => causes[b] - causes[a])[0] || '-';
    $('feedback-count').textContent = data.stats.recommendation_feedback;
    $('effectiveness').textContent = data.stats.recommendation_feedback ? `${data.stats.recommendation_effectiveness}%` : '-';
    renderRecentPatterns(data.recent_patterns);
    if (data.report) renderReport(data.report);
    renderModelPanel(data);
    $('demo-pill').classList.toggle('hidden', !data.demo_mode);
    if (data.stats.peak_period) $('peak-period').textContent = data.stats.peak_period;
    if (data.latest && !activeEvent) renderLatest(data.latest);
    if (profileSetupRequired && $('profile-modal').classList.contains('hidden')) {
        setTimeout(() => openProfile(true), 0);
    }
}

async function selectPatternPeriod(period) {
    selectedPatternPeriod = period;
    document.querySelectorAll('.pattern-tab').forEach(button => button.classList.toggle('active', button.dataset.period === period));
    await loadDashboard();
}

function renderRecentPatterns(patterns = {}) {
    $('pattern-period-label').textContent = patterns.label || 'Last 7 days';
    $('pattern-event-count').textContent = patterns.events || 0;
    if (!patterns.reasons || !patterns.reasons.length) {
        $('pattern-bars').innerHTML = '<p class="pattern-empty">No cry patterns recorded for this period yet.</p>';
        return;
    }
    $('pattern-bars').innerHTML = patterns.reasons.map(reason => `<div class="pattern-row"><div class="pattern-row-label"><span>${reason.label}</span><b>${reason.count}</b></div><div class="pattern-track"><div class="pattern-fill" style="width: ${reason.percent}%"></div></div><span class="pattern-percent">${reason.percent}%</span></div>`).join('');
}

function formatAge(months) { return months < 1 ? 'Newborn' : `${months} month${months === 1 ? '' : 's'}`; }

function renderProfilePhoto(profile) {
    $('profile-avatar').textContent = (profile.name || 'L').trim().charAt(0).toUpperCase() || 'L';
    const image = $('profile-photo');
    if (profile.photo_url) {
        image.src = `${profile.photo_url}?v=${Date.now()}`;
        image.classList.remove('hidden');
        $('profile-avatar').classList.add('hidden');
    } else {
        image.removeAttribute('src');
        image.classList.add('hidden');
        $('profile-avatar').classList.remove('hidden');
    }
}

function setSource(source) {
    const sourceChanged = selectedSource !== source;
    selectedSource = source;
    document.querySelectorAll('.source-btn').forEach(button => button.classList.toggle('active', button.dataset.source === source));
    document.querySelector('.recording-picker').classList.toggle('hidden', source !== 'demo');
    document.querySelector('.preview-row').classList.toggle('hidden', source !== 'demo');
    $('upload-recording-controls').classList.toggle('hidden', source !== 'upload');
    $('live-recording-controls').classList.toggle('hidden', source !== 'live');
    if (sourceChanged) {
        savedAudioReference = null;
        $('live-audio-preview').classList.add('hidden');
        $('live-audio-preview').removeAttribute('src');
        $('uploaded-audio-preview').classList.add('hidden');
        $('uploaded-audio-preview').removeAttribute('src');
        $('upload-recording-status').textContent = 'WAV or MP3 works out of the box. WebM/M4A need ffmpeg on the computer running FirstVoice.';
    }
    if (source !== 'live') {
        stopLiveRecording();
        $('recording-status').textContent = 'Records from this computer\'s microphone. For hands-free use, open Hands-free mode.';
    }
    updateAnalyzeButton();
}

function selectDemo(key) {
    selectedDemo = key;
    $('recording-select').value = key;
    stopDemoPreview();
    $('preview-status').textContent = 'Hear the selected demo signal';
}

function updateAnalyzeButton() {
    const ready = selectedSource === 'demo' || Boolean(savedAudioReference);
    $('analyze-btn').disabled = !ready;
    if (selectedSource === 'live') {
        $('analyze-btn').innerHTML = ready ? '<i class="fa-solid fa-wave-square"></i> Analyze saved recording' : '<i class="fa-solid fa-wave-square"></i> Record something first';
    } else if (selectedSource === 'upload') {
        $('analyze-btn').innerHTML = ready ? '<i class="fa-solid fa-wave-square"></i> Analyze uploaded recording' : '<i class="fa-solid fa-wave-square"></i> Choose a recording first';
    } else {
        $('analyze-btn').innerHTML = '<i class="fa-solid fa-wave-square"></i> Analyze demo preset';
    }
}

function toggleDemoPreview() {
    if (demoPreviewNode) {
        stopDemoPreview();
        return;
    }
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
        $('preview-status').textContent = 'Audio preview is not supported in this browser.';
        return;
    }
    demoPreviewContext = demoPreviewContext || new AudioContextClass();
    const context = demoPreviewContext;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const frequencies = { evening: [390, 620], feeding: [480, 760], nap: [300, 470] };
    const [startFrequency, endFrequency] = frequencies[selectedDemo] || frequencies.evening;
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(startFrequency, context.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(endFrequency, context.currentTime + 0.65);
    oscillator.frequency.exponentialRampToValueAtTime(startFrequency, context.currentTime + 1.1);
    gain.gain.setValueAtTime(0.001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.13, context.currentTime + 0.08);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 1.2);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 1.25);
    demoPreviewNode = oscillator;
    $('preview-btn').innerHTML = '<i class="fa-solid fa-stop"></i> Stop preview';
    $('preview-status').textContent = 'Previewing the selected demo signal';
    oscillator.onended = stopDemoPreview;
}

function stopDemoPreview() {
    if (demoPreviewNode) {
        try { demoPreviewNode.stop(); } catch (error) { /* Already stopped. */ }
        demoPreviewNode = null;
    }
    if ($('preview-btn')) $('preview-btn').innerHTML = '<i class="fa-solid fa-play"></i> Play preview';
}

async function startLiveRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        $('recording-status').textContent = 'Live recording is not supported in this browser.';
        return;
    }
    try {
        mic = new MicCapture({ bufferSeconds: 5 });
        await mic.start();
        mic.startRecording();
        savedAudioReference = null;
        $('record-btn').disabled = true;
        $('stop-record-btn').disabled = false;
        $('recording-status').textContent = 'Listening... record 3 to 10 seconds of crying, then tap Stop.';
        updateAnalyzeButton();
    } catch (error) {
        $('recording-status').textContent = 'Microphone permission was not granted.';
    }
}

function stopLiveRecording() {
    if (!mic || !mic.active) return;
    const blob = mic.stopRecording();
    mic.stop();
    mic = null;
    if ($('stop-record-btn')) $('stop-record-btn').disabled = true;
    if (selectedSource === 'live') saveLiveRecording(blob);
}

async function uploadNewRecording(event) {
    const file = event.target.files[0];
    if (!file) return;
    $('upload-recording-status').textContent = 'Saving your uploaded recording...';
    $('uploaded-audio-preview').src = URL.createObjectURL(file);
    $('uploaded-audio-preview').classList.remove('hidden');
    try {
        const form = new FormData();
        form.append('audio', file, file.name);
        const data = await api('/api/firstvoice/recordings', { method: 'POST', body: form });
        savedAudioReference = data.audio_reference;
        $('upload-recording-status').textContent = 'Recording saved. It is ready for analysis.';
    } catch (error) {
        savedAudioReference = null;
        $('upload-recording-status').textContent = error.message;
    }
    updateAnalyzeButton();
}

async function saveLiveRecording(blob) {
    $('recording-status').textContent = 'Saving your recording...';
    $('live-audio-preview').src = URL.createObjectURL(blob);
    $('live-audio-preview').classList.remove('hidden');
    try {
        const form = new FormData();
        form.append('audio', blob, 'live-recording.wav');
        const data = await api('/api/firstvoice/recordings', { method: 'POST', body: form });
        savedAudioReference = data.audio_reference;
        $('recording-status').textContent = 'Saved. Tap Analyze to run FirstVoice on it.';
    } catch (error) {
        $('recording-status').textContent = error.message;
    }
    $('record-btn').disabled = false;
    updateAnalyzeButton();
}

async function analyzeCry() {
    if (selectedSource === 'live' && !savedAudioReference) {
        $('recording-status').textContent = 'Record and save a live sound before analyzing it.';
        return;
    }
    if (selectedSource === 'upload' && !savedAudioReference) {
        $('upload-recording-status').textContent = 'Choose and save an audio file before analyzing it.';
        return;
    }
    const button = $('analyze-btn');
    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Analyzing...';
    try {
        const data = await api('/api/firstvoice/analyze', { method: 'POST', body: JSON.stringify({ demo_key: selectedDemo, source: selectedSource, age_months: selectedAge, audio_reference: savedAudioReference }) });
        activeEvent = data.event;
        renderLatest(activeEvent);
        resetFeedbackPanel();
        resetRecommendationActions();
        $('feedback-panel').classList.remove('hidden');
        $('correction-options').classList.add('hidden');
        $('recommendation-actions').classList.remove('hidden');
        $('recommendation-copy').textContent = activeEvent.recommendation;
        if (activeEvent.prediction === 'Pain' && data.medical_alert) $('recommendation-copy').textContent += ' ' + data.medical_alert;
        renderDashboard(data.dashboard);
        document.querySelector('.feedback-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (error) { alert(error.message); }
    updateAnalyzeButton();
}

function renderLatest(event) {
    const esc = FirstVoiceUI.escape;
    const isDemo = !event.probabilities;
    const sourceNote = isDemo ? 'Demo preset (scripted, no audio model)'
        : `${event.model_source}${event.source === 'auto' ? ' · hands-free' : ''}`;
    const alternatives = (event.alternatives || []).map(item => `<span class="alternative-item"><b>${esc(item.label)}</b><em>${item.confidence}%</em></span>`).join('');
    const plan = (event.action_plan || []).map(step => `<li>${esc(step)}</li>`).join('');
    const why = (event.explanation || []).map(line => `<li>${esc(line)}</li>`).join('');
    const personal = event.personalization;
    const personalLine = personal && personal.examples
        ? `<span class="personal-note"><i class="fa-solid fa-user-check"></i> Personalized with ${personal.examples} of ${esc(currentProfile.name)}'s reviewed cries</span>`
        : (isDemo ? '' : `<span class="personal-note muted"><i class="fa-solid fa-user"></i> General model only. Your feedback starts personalization.</span>`);
    const detection = event.detection;
    const detectionLine = detection ? `<span class="detection-note ${detection.cry_detected ? '' : 'warn'}"><i class="fa-solid fa-${detection.cry_detected ? 'baby' : 'triangle-exclamation'}"></i> ${detection.cry_detected ? 'Infant cry detected' : 'No clear infant cry heard'} (YAMNet ${Math.round(detection.cry_score * 100)}%)</span>` : '';
    const feedbackState = event.feedback_confirmed === true ? `<span class="reviewed">You confirmed: ${esc(event.parent_feedback)}</span>`
        : event.feedback_confirmed === false ? `<span class="reviewed">You corrected it to ${esc(event.parent_feedback)}</span>` : '';
    $('latest-panel').innerHTML = `<div class="panel-heading"><div><p class="eyebrow">LATEST SIGNAL</p><h2>Possible reason right now</h2></div><span class="confidence-badge" title="Model probability after personalization. Not a measure of medical accuracy.">${event.confidence}% likely</span></div>
        <div class="result-main"><span class="result-orb"><i class="fa-solid fa-wave-square"></i></span><div><span class="result-label">Most likely</span><strong>${esc(event.prediction)}</strong><span class="result-sub">${esc(sourceNote)}</span>${feedbackState}</div></div>
        <div class="signal-notes">${detectionLine}${personalLine}</div>
        ${why ? `<div class="why-block"><span><i class="fa-solid fa-magnifying-glass"></i> Why FirstVoice thinks so</span><ul>${why}</ul></div>` : ''}
        <div class="solution-callout"><span><i class="fa-solid fa-list-check"></i> What to try next</span><ol class="action-plan">${plan || '<li>Try a familiar soothing routine and watch how your baby responds.</li>'}</ol></div>
        ${event.spectrogram && event.spectrogram.frames ? `<div class="acoustic-block"><canvas id="spectrogram-canvas" class="spectrogram" aria-label="log-Mel spectrogram of the cry"></canvas><div class="feature-chips">${FirstVoiceUI.featureChips(event.features)}</div></div>` : ''}
        <div class="alternatives"><span>Other possibilities</span><div class="alternative-list">${alternatives || '<span class="alternative-item">Parent feedback helps personalize this result.</span>'}</div></div>`;
    FirstVoiceUI.drawSpectrogram($('spectrogram-canvas'), event.spectrogram);
}

function renderModelPanel(data) {
    const model = data.model || {};
    if (model.trained) {
        const replay = model.personalization_replay;
        $('model-layer1').textContent = `FirstVoice listens for clues in each cry and compares them with ${Object.values(model.class_counts || {}).reduce((a, b) => a + b, 0)} labeled recordings to suggest a possible reason. It gets the right answer about ${Math.round((model.balanced_accuracy || 0) * 100)}% of the time with babies it has not heard before${replay ? `, and improved from ${Math.round(replay.general_accuracy * 100)}% to ${Math.round(replay.personalized_accuracy * 100)}% when it used parents' feedback` : ''}.`;
    } else {
        $('model-layer1').textContent = model.message || 'FirstVoice is still learning how to understand cries.';
    }
    const examples = data.stats.personal_examples || 0;
    $('model-personal').textContent = examples
        ? `${examples} of ${currentProfile.name}'s cries have your confirmation or correction attached. Each new prediction blends the general model with these examples, and their weight grows as you add more.`
        : `No reviewed cries yet. Confirm or correct a result and FirstVoice will start weighting ${currentProfile.name}'s own history.`;
    const llm = data.llm || {};
    $('model-layer2').textContent = llm.configured
        ? `${llm.provider} reads ${currentProfile.name}'s age, times of day, cause frequencies, your feedback and what helped, then writes the weekly report and the per-cause action plans used for every new cry.`
        : 'No API key set, so the weekly report uses the built-in local analysis. Add an Anthropic or OpenAI key to .env to switch on the LLM.';
}

async function refreshReport() {
    const button = $('refresh-report-btn');
    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Writing...';
    try {
        const data = await api('/api/firstvoice/report/refresh', { method: 'POST' });
        if (data) renderDashboard(data);
    } catch (error) { alert(error.message); }
    button.disabled = false;
    button.innerHTML = '<i class="fa-solid fa-rotate"></i> Refresh';
}

function resetFeedbackPanel() {
    $('feedback-panel').classList.remove('feedback-complete');
    $('feedback-copy').innerHTML = `<p class="eyebrow">YOUR FEEDBACK</p><h2>Does this sound right?</h2><p>When you confirm or correct the result, it becomes one of ${currentProfile.name}'s labeled cries. The next prediction is personalized with it straight away.</p>`;
}

function resetRecommendationActions() {
    $('recommendation-actions').innerHTML = '<span>Was this suggestion helpful?</span><button class="helpful-btn" onclick="sendRecommendationFeedback(true)"><i class="fa-solid fa-thumbs-up"></i> Helpful</button><button class="not-helpful-btn" onclick="sendRecommendationFeedback(false)"><i class="fa-solid fa-thumbs-down"></i> Not helpful</button><select id="helpful-detail" aria-label="What worked?"><option value="">What worked?</option><option>Feeding</option><option>Rocking</option><option>Burping</option><option>Changing diaper</option><option>Nap</option></select>';
}

async function sendClassificationFeedback(confirmed, correctedCause = null) {
    if (!activeEvent) return;
    try {
        const data = await api('/api/firstvoice/feedback', { method: 'POST', body: JSON.stringify({ event_id: activeEvent.id, confirmed, corrected_cause: correctedCause }) });
        activeEvent = data.event;
        $('feedback-panel').classList.add('feedback-complete');
        const examples = data.dashboard.stats.personal_examples;
        $('feedback-copy').innerHTML = `<p class="eyebrow">YOUR FEEDBACK</p><h2>Saved to ${currentProfile.name}'s history.</h2><p>${confirmed ? 'You confirmed this possible reason.' : `You told FirstVoice this was ${correctedCause}.`} ${examples ? `FirstVoice now personalizes with ${examples} of ${currentProfile.name}'s cries.` : ''}</p>`;
        renderLatest(activeEvent);
        $('correction-options').classList.add('hidden');
        renderDashboard(data.dashboard);
    } catch (error) { alert(error.message); }
}

function showCorrectionOptions() {
    document.querySelectorAll('#correction-options button').forEach(button => {
        button.classList.toggle('hidden', Boolean(activeEvent) && button.textContent.trim() === activeEvent.prediction);
    });
    $('correction-options').classList.remove('hidden');
}

async function sendRecommendationFeedback(helpful) {
    if (!activeEvent) return;
    try {
        const data = await api('/api/firstvoice/recommendation-feedback', { method: 'POST', body: JSON.stringify({ event_id: activeEvent.id, helpful, detail: $('helpful-detail').value }) });
        activeEvent = data.event;
        $('recommendation-actions').innerHTML = '<span class="saved-feedback"><i class="fa-solid fa-check"></i> Feedback saved for future suggestions.</span>';
        renderDashboard(data.dashboard);
    } catch (error) { alert(error.message); }
}

function renderReport(report) {
    const esc = FirstVoiceUI.escape;
    $('report-provider').textContent = report.provider || 'Local analysis';
    const when = report.generated_at ? new Date(report.generated_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
    $('report-meta').textContent = [when && `Written ${when}`, report.provider_error && 'LLM call failed, so this is the local version. Check the API key in .env.'].filter(Boolean).join(' · ');
    $('report-summary').textContent = report.summary;
    $('patterns-list').innerHTML = (report.patterns || []).map(item => `<li>${esc(item)}</li>`).join('');
    $('recommendations-list').innerHTML = (report.recommendations || []).map(item => `<li>${esc(item)}</li>`).join('');
    $('changes-list').innerHTML = (report.changes || []).map(item => `<li>${esc(item)}</li>`).join('');
    $('focus-text').textContent = report.focus_for_next_week || '';
    $('caution-text').textContent = (report.cautions || []).join(' ') || 'FirstVoice provides AI-generated insights for informational purposes and is not a medical diagnostic tool.';
}

function openProfile(forceSetup = false) {
    $('profile-input-name').value = currentProfile.name;
    $('profile-input-age').value = currentProfile.age_months;
    $('profile-input-gender').value = currentProfile.gender || '';
    $('profile-photo-input').value = '';
    $('profile-photo-status').textContent = currentProfile.photo_url ? 'Choose a new picture to replace it' : 'JPG, PNG, or WebP';
    const preview = $('profile-photo-preview');
    if (currentProfile.photo_url) {
        preview.src = `${currentProfile.photo_url}?v=${Date.now()}`;
        preview.classList.remove('hidden');
        $('profile-photo-placeholder').classList.add('hidden');
    } else {
        preview.removeAttribute('src');
        preview.classList.add('hidden');
        $('profile-photo-placeholder').classList.remove('hidden');
    }
    $('profile-modal-title').textContent = forceSetup ? 'Tell us about your baby' : 'Update profile';
    $('profile-setup-copy').classList.toggle('hidden', !forceSetup);
    $('profile-modal-close').classList.toggle('hidden', forceSetup);
    $('profile-modal').classList.remove('hidden');
}
function closeProfile() {
    if (profileSetupRequired) return;
    $('profile-modal').classList.add('hidden');
}

function previewProfilePhoto(event) {
    const file = event.target.files[0];
    if (!file) return;
    const preview = $('profile-photo-preview');
    preview.src = URL.createObjectURL(file);
    preview.classList.remove('hidden');
    $('profile-photo-placeholder').classList.add('hidden');
    $('profile-photo-status').textContent = file.name;
}

async function saveProfile() {
    try {
        const name = $('profile-input-name').value.trim();
        if (!name) { $('profile-input-name').focus(); return; }
        const data = await api('/api/firstvoice/profile', { method: 'PUT', body: JSON.stringify({ name, age_months: Number($('profile-input-age').value || 4), gender: $('profile-input-gender').value }) });
        const photo = $('profile-photo-input').files[0];
        if (photo) {
            const form = new FormData();
            form.append('photo', photo);
            await api('/api/firstvoice/profile/photo', { method: 'POST', body: form });
        }
        selectedAge = data.profile.age_months;
        closeProfile();
        const refreshed = await api(`/api/firstvoice/dashboard?age_months=${selectedAge}`);
        renderDashboard(refreshed || data);
    } catch (error) { alert(error.message); }
}
async function logout() { await fetch('/api/logout'); window.location.href = '/login'; }
