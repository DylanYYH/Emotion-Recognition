let allEvents = [];
let currentDate = new Date();
let selectedDate = new Date();

document.addEventListener('DOMContentLoaded', () => {
    fetchEvents();
});

async function fetchEvents() {
    try {
        const response = await fetch('/api/calendar');
        if (response.ok) {
            allEvents = await response.json();
            // Sort
            allEvents.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
            renderCalendar();
        }
    } catch (err) { console.error(err); }
}

function renderCalendar() {
    const daysGrid = document.getElementById('days-grid');
    const monthYearTitle = document.getElementById('month-year');

    const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    monthYearTitle.textContent = `${monthNames[currentDate.getMonth()]} ${currentDate.getFullYear()}`;

    daysGrid.innerHTML = '';

    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    const dayCount = lastDay.getDate();
    const startDayIndex = firstDay.getDay();

    for (let i = 0; i < startDayIndex; i++) {
        const div = document.createElement('div');
        div.className = 'day-cell empty';
        daysGrid.appendChild(div);
    }

    for (let d = 1; d <= dayCount; d++) {
        const div = document.createElement('div');
        div.className = 'day-cell';
        div.textContent = d;
        const cellDate = new Date(year, month, d);

        if (isSameDay(cellDate, new Date())) div.classList.add('today');

        const eventsForDay = allEvents.filter(e => isSameDay(new Date(e.timestamp), cellDate));
        if (eventsForDay.length > 0) {
            const dotsContainer = document.createElement('div');
            dotsContainer.className = 'event-dots';
            eventsForDay.slice(0, 4).forEach(e => {
                const dot = document.createElement('div');
                dot.className = `dot ${getDotClass(e.event_type)}`;
                dotsContainer.appendChild(dot);
            });
            div.appendChild(dotsContainer);

            const reasons = [...new Set(eventsForDay.map(event => event.cause).filter(Boolean))].slice(0, 2);
            const flagsContainer = document.createElement('div');
            flagsContainer.className = 'reason-flags';
            reasons.forEach(reason => {
                const flag = document.createElement('span');
                flag.className = `reason-flag ${getReasonClass(reason)}`;
                flag.textContent = reason;
                flagsContainer.appendChild(flag);
            });
            div.appendChild(flagsContainer);
        }
        div.onclick = () => selectDate(cellDate);
        daysGrid.appendChild(div);
    }
}

function selectDate(date) {
    selectedDate = date;
    const overlay = document.getElementById('day-overlay');
    const title = document.getElementById('overlay-date-title');
    const timeline = document.getElementById('overlay-timeline');

    const options = { weekday: 'long', month: 'long', day: 'numeric' };
    title.textContent = date.toLocaleDateString(undefined, options);

    const dayEvents = allEvents.filter(e => isSameDay(new Date(e.timestamp), selectedDate));
    dayEvents.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

    timeline.innerHTML = '';
    if (dayEvents.length === 0) {
        timeline.innerHTML = `
             <div style="text-align: center; padding: 40px; color: #888;">
                <i class="fa-regular fa-calendar-xmark" style="font-size: 3rem; margin-bottom: 20px; color:#ddd;"></i>
                <p>No activity yet.</p>
            </div>`;
    } else {
        dayEvents.forEach(event => {
            const timeStr = new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            const div = document.createElement('div');
            div.className = `timeline-item ${getDotClass(event.event_type)}`;
            div.innerHTML = `
                <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
                    <strong style="color:#555;">${timeStr}</strong>
                    <span style="font-size:0.8rem; color:#888;">${event.event_type}</span>
                </div>
                <h4 style="margin:0 0 5px 0; color:#333;">${event.cause}</h4>
                <div style="font-size:0.9rem; color:#666;">${event.notes || ''}</div>
                <div style="text-align:right; margin-top:5px;">
                    <button class="edit-btn" onclick="openEdit(${event.id}, '${event.cause}', '${event.notes || ''}')" style="background:none; border:none; color:#007bff; cursor:pointer;"><i class="fa-solid fa-pen"></i></button>
                    <button class="delete-btn" onclick="deleteEvent(${event.id})" style="background:none; border:none; color:#dc3545; cursor:pointer;"><i class="fa-solid fa-trash"></i></button>
                </div>
            `;
            timeline.appendChild(div);
        });
    }

    overlay.classList.add('active');
}

function closeOverlay() {
    document.getElementById('day-overlay').classList.remove('active');
}

function changeMonth(delta) {
    currentDate.setMonth(currentDate.getMonth() + delta);
    renderCalendar();
}

function getDotClass(type) {
    if (type.includes("Crying") || type.includes("FirstVoice") || type === "Analysis") return "type-crying";
    if (type === "Feeding") return "type-feeding";
    if (type === "Sleep") return "type-sleep";
    return "";
}

function getReasonClass(reason) {
    const normalized = reason.toLowerCase();
    if (normalized.includes('hungry') || normalized.includes('feeding')) return 'reason-hungry';
    if (normalized.includes('tired') || normalized.includes('sleep')) return 'reason-tired';
    if (normalized.includes('pain')) return 'reason-pain';
    return 'reason-discomfort';
}

function isSameDay(d1, d2) {
    return d1.getFullYear() === d2.getFullYear() &&
        d1.getMonth() === d2.getMonth() &&
        d1.getDate() === d2.getDate();
}

// --- CRUD ---
let currentEditingId = null;

function openAddModal() { document.getElementById('add-modal').classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

async function saveAdd() {
    try {
        const type = document.getElementById('add-type').value;
        const cause = document.getElementById('add-cause').value;
        const notes = document.getElementById('add-notes').value;

        await fetch('/api/calendar', {
            method: 'POST',
            body: JSON.stringify({ event_type: type, cause, notes }),
            headers: { 'Content-Type': 'application/json' }
        });
        closeModal('add-modal');
        fetchEvents().then(() => selectDate(selectedDate)); // Reload and refresh overlay
    } catch (err) { alert('Error saving'); }
}

function openEdit(id, cause, notes) {
    currentEditingId = id;
    document.getElementById('edit-cause').value = cause;
    document.getElementById('edit-notes').value = notes;
    document.getElementById('edit-modal').classList.remove('hidden');
}

async function saveEdit() {
    try {
        const cause = document.getElementById('edit-cause').value;
        const notes = document.getElementById('edit-notes').value;
        await fetch(`/api/calendar/${currentEditingId}`, {
            method: 'PUT',
            body: JSON.stringify({ cause, notes }),
            headers: { 'Content-Type': 'application/json' }
        });
        closeModal('edit-modal');
        fetchEvents().then(() => selectDate(selectedDate));
    } catch (err) { alert('Error updating'); }
}

async function deleteEvent(id) {
    if (!confirm("Delete?")) return;
    try {
        await fetch(`/api/calendar/${id}`, { method: 'DELETE' });
        fetchEvents().then(() => selectDate(selectedDate));
    } catch (err) { alert('Error deleting'); }
}
