// DOM Elements
const recordBtn = document.getElementById('record-btn');
const recordStatus = document.getElementById('record-status');
const analyzeBtn = document.getElementById('analyze-btn');
const imageUpload = document.getElementById('image-upload');
const imagePreview = document.getElementById('image-preview');
const resultsSection = document.getElementById('results-section');
const inputSection = document.querySelector('.input-section');

// Camera Elements
const cameraBtn = document.getElementById('camera-btn');
const cameraContainer = document.getElementById('camera-container');
const cameraStream = document.getElementById('camera-stream');
const captureBtn = document.getElementById('capture-btn');
const cameraCanvas = document.getElementById('camera-canvas');

// Settings Elements
// None currently

let isRecording = false;
let currentMode = 'audio'; // 'audio' or 'vision'
let audioBlob = null;
let imageFile = null;

// Auto Listen State
// Removed - Moved to Auto Mode Page

// Tab Switching
function switchTab(mode) {
    currentMode = mode;
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    if (mode === 'audio') {
        document.querySelector('button[onclick="switchTab(\'audio\')"]').classList.add('active');
        document.getElementById('audio-tab').classList.add('active');
        stopCamera();
    } else {
        document.querySelector('button[onclick="switchTab(\'vision\')"]').classList.add('active');
        document.getElementById('vision-tab').classList.add('active');
    }

    checkAnalyzeButton();
}

// --- Audio Recording ---
recordBtn.addEventListener('click', () => {
    if (!isRecording) {
        startRecording();
    } else {
        stopRecording();
    }
});

function startRecording() {
    isRecording = true;
    recordBtn.classList.add('recording');
    recordBtn.innerHTML = '<i class="fa-solid fa-stop"></i>';
    recordStatus.textContent = "Listening... Tap to Stop";
    // Simulation of recording for prototype
    setTimeout(() => {
        // mock 'blob' created after 3s if user doesn't stop
    }, 3000);
}

function stopRecording() {
    isRecording = false;
    recordBtn.classList.remove('recording');
    recordBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>';
    recordStatus.textContent = "Audio Captured!";
    audioBlob = new Blob(["mock"], { type: "audio/wav" }); // Mock blob
    checkAnalyzeButton();
}

// --- Image Upload ---
imageUpload.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        handleImageSelection(file);
    }
});

function handleImageSelection(file) {
    imageFile = file;
    const reader = new FileReader();
    reader.onload = (e) => {
        imagePreview.innerHTML = `<img src="${e.target.result}" alt="Preview">`;
        stopCamera(); // Hide camera if file selected
        cameraContainer.classList.add('hidden');
    };
    reader.readAsDataURL(file);
    checkAnalyzeButton();
}

// --- Camera Logic ---
cameraBtn.addEventListener('click', async () => {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true });
        cameraStream.srcObject = stream;
        cameraContainer.classList.remove('hidden');
        imagePreview.innerHTML = ''; // Clear prev image
        imageFile = null;
        checkAnalyzeButton();
    } catch (err) {
        alert("Camera access denied or not available.");
        console.error(err);
    }
});

captureBtn.addEventListener('click', () => {
    const context = cameraCanvas.getContext('2d');
    cameraCanvas.width = cameraStream.videoWidth;
    cameraCanvas.height = cameraStream.videoHeight;
    context.drawImage(cameraStream, 0, 0, cameraCanvas.width, cameraCanvas.height);

    cameraCanvas.toBlob((blob) => {
        const file = new File([blob], "capture.jpg", { type: "image/jpeg" });
        handleImageSelection(file);
    }, 'image/jpeg');
});

function stopCamera() {
    if (cameraStream.srcObject) {
        const tracks = cameraStream.srcObject.getTracks();
        tracks.forEach(track => track.stop());
        cameraStream.srcObject = null;
    }
    cameraContainer.classList.add('hidden');
}

// --- Analysis ---
function checkAnalyzeButton() {
    if ((currentMode === 'audio' && audioBlob) || (currentMode === 'vision' && imageFile)) {
        analyzeBtn.disabled = false;
    } else {
        analyzeBtn.disabled = true;
    }
}

analyzeBtn.addEventListener('click', () => performAnalysis());

async function performAnalysis() {
    analyzeBtn.textContent = 'Analyzing...';
    analyzeBtn.disabled = true;

    try {
        const response = await fetch('/api/analyze', {
            method: 'POST',
            body: JSON.stringify({ type: currentMode }),
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }

        const data = await response.json();

        displayResults(data);

    } catch (error) {
        console.error('Error:', error);
        alert('Analysis failed. Please try again.');
    } finally {
        analyzeBtn.textContent = 'Analyze Status';
        analyzeBtn.disabled = false;
    }
}

function displayResults(data) {
    inputSection.style.display = 'none';
    resultsSection.classList.remove('hidden');

    document.getElementById('emotion-text').textContent = data.emotion;
    document.getElementById('confidence-badge').textContent = `${data.confidence}% Confidence`;

    const stepsList = document.getElementById('steps-list');
    stepsList.innerHTML = '';
    data.suggestions.forEach(step => {
        const li = document.createElement('li');
        li.textContent = step;
        stepsList.appendChild(li);
    });

    if (data.medical_alert) {
        document.getElementById('medical-alert').classList.remove('hidden');
        document.getElementById('medical-text').textContent = data.medical_alert;
    } else {
        document.getElementById('medical-alert').classList.add('hidden');
    }
}

function resetApp() {
    resultsSection.classList.add('hidden');
    inputSection.style.display = 'block';

    // Reset state
    imagePreview.innerHTML = '';
    imageUpload.value = '';
    imageFile = null;
    audioBlob = null;
    recordStatus.textContent = "Tap to Listen";

    stopCamera();
    checkAnalyzeButton();
}

async function logout() {
    try {
        await fetch('/api/logout');
        window.location.href = '/login';
    } catch (err) {
        console.error('Logout failed', err);
    }
}

// --- Modal & Settings ---
function closeModal(id) {
    document.getElementById(id).classList.add('hidden');
}

function openSettings() {
    document.getElementById('settings-modal').classList.remove('hidden');
}

