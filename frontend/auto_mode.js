// DOM Elements
const toggleBtn = document.getElementById('toggle-monitor-btn');
const statusBadge = document.getElementById('status-badge');
const statusText = document.getElementById('status-text');
const activityFeed = document.getElementById('activity-feed');

// State
let isMonitoring = false;
let audioContext = null;
let analyser = null;
let microphone = null;
let listenInterval = null;
let silenceTimer = null; // To prevent rapid-fire triggering
let isAnalyzing = false; // Mutex for analysis

// Constants
const AUDIO_THRESHOLD = 50; // Tunable
const TRIGGER_DURATION = 10; // ~1 second of noise
const COOLDOWN_MS = 5000; // Wait 5s between analyses

toggleBtn.addEventListener('click', toggleMonitoring);

async function toggleMonitoring() {
    if (!isMonitoring) {
        // Start
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            startAudioContext(stream);
            isMonitoring = true;
            updateUI(true);
        } catch (err) {
            console.error("Microphone access denied:", err);
            alert("Could not access microphone. Please allow permissions.");
        }
    } else {
        // Stop
        stopAudioContext();
        isMonitoring = false;
        updateUI(false);
    }
}

function updateUI(active) {
    if (active) {
        statusBadge.classList.add('active');
        statusText.textContent = "Monitoring Active";
        toggleBtn.textContent = "Stop Monitoring";
        toggleBtn.classList.replace('primary-btn', 'secondary-btn');
    } else {
        statusBadge.classList.remove('active');
        statusText.textContent = "Inactive";
        toggleBtn.textContent = "Start Monitoring";
        toggleBtn.classList.replace('secondary-btn', 'primary-btn');
    }
}

function startAudioContext(stream) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    microphone = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    microphone.connect(analyser);

    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    let triggerCount = 0;

    listenInterval = setInterval(() => {
        if (isAnalyzing) return; // Skip if busy

        analyser.getByteFrequencyData(dataArray);
        const volume = dataArray.reduce((prev, curr) => prev + curr, 0) / dataArray.length;

        if (volume > AUDIO_THRESHOLD) {
            triggerCount++;
            if (triggerCount >= TRIGGER_DURATION) {
                // Trigger!
                triggerCount = 0;
                performAutoAnalysis();
            }
        } else {
            triggerCount = Math.max(0, triggerCount - 1);
        }
    }, 100);
}

function stopAudioContext() {
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
    if (listenInterval) {
        clearInterval(listenInterval);
        listenInterval = null;
    }
}

async function performAutoAnalysis() {
    isAnalyzing = true;
    addFeedItem("Analyzing sound...", "pending");

    try {
        // In a real app we'd send the actual audio buffer. 
        // Here we just trigger the 'audio' analysis type.
        const response = await fetch('/api/analyze', {
            method: 'POST',
            body: JSON.stringify({ type: 'audio' }),
            headers: { 'Content-Type': 'application/json' }
        });

        if (response.ok) {
            const data = await response.json();
            updateLastFeedItem(data);
        } else {
            updateLastFeedItem({ error: true });
        }

    } catch (err) {
        console.error("Analysis failed", err);
        updateLastFeedItem({ error: true });
    }

    // Cooldown
    setTimeout(() => {
        isAnalyzing = false;
    }, COOLDOWN_MS);
}

function addFeedItem(text, type) {
    const item = document.createElement('div');
    item.className = 'feed-item';
    item.dataset.pending = type === 'pending';

    const time = new Date().toLocaleTimeString();

    item.innerHTML = `
        <div class="feed-time">${time}</div>
        <div class="feed-emotion">${text} <i class="fa-solid fa-spinner fa-spin" style="font-size: 0.8em;"></i></div>
    `;

    activityFeed.prepend(item); // Add to top
}

function updateLastFeedItem(data) {
    // Find the most recent pending item
    const item = document.querySelector('.feed-item[data-pending="true"]');
    if (!item) return;

    item.dataset.pending = "false";
    item.classList.add('fresh');

    if (data.error) {
        item.querySelector('.feed-emotion').innerHTML = `<span style="color:red">Analysis Failed</span>`;
        return;
    }

    // Success
    item.querySelector('.feed-emotion').innerHTML = `
        <i class="fa-solid fa-check-circle" style="color: #4CAF50;"></i> Detected: ${data.emotion} (${data.confidence}%)
    `;

    const suggestions = document.createElement('div');
    suggestions.style.marginTop = "10px";
    suggestions.style.fontSize = "0.9rem";
    suggestions.innerHTML = `<strong>Suggestion:</strong> ${data.suggestions[0] || 'Check on baby.'}`;

    item.appendChild(suggestions);
}
