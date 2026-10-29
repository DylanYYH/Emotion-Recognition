const urlParams = new URLSearchParams(window.location.search);
const dateParam = urlParams.get('date');
const targetDate = dateParam ? new Date(dateParam + "T00:00:00") : new Date();

document.addEventListener('DOMContentLoaded', () => {
    // Set Title
    const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    document.getElementById('page-title').textContent = targetDate.toLocaleDateString(undefined, options);

    fetchEvents();
});

let dayEvents = [];

async function fetchEvents() {
    try {
        const response = await fetch('/api/calendar');
        if (response.ok) {
            const allEvents = await response.json();

            // Filter
            dayEvents = allEvents.filter(e => {
                const d = new Date(e.timestamp);
                return d.getFullYear() === targetDate.getFullYear() &&
                    d.getMonth() === targetDate.getMonth() &&
                    d.getDate() === targetDate.getDate();
            });

            // Sort
            dayEvents.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
            renderTimeline();
        }
    } catch (err) {
        document.getElementById('timeline-list').innerHTML = '<p style="text-align:center; color:red">Error loading events</p>';
    }
}

function renderTimeline() {
    const container = document.getElementById('timeline-list');
    container.innerHTML = '';

    if (dayEvents.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 40px; color: #888;">
                <i class="fa-regular fa-calendar-xmark" style="font-size: 3rem; margin-bottom: 20px;"></i>
                <p>No activity recorded for this day.</p>
            </div>
        `;
        return;
    }

    dayEvents.forEach(event => {
        const timeStr = new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        const div = document.createElement('div');
        div.className = `timeline-item ${getDotClass(event.event_type)}`;
        div.innerHTML = `
            <div class="timeline-time"><i class="fa-regular fa-clock"></i> ${timeStr} • ${event.event_type}</div>
            <h3 style="margin: 0 0 5px 0; color: #333;">${event.cause}</h3>
            <p style="color: #666; font-style: italic; margin-bottom: 15px;">${event.notes || 'No notes'}</p>
            <div style="text-align: right;">
                 <button onclick="openEdit(${event.id}, '${event.cause}', '${event.notes || ''}')" style="background:none; border:none; cursor:pointer; color:#007bff; margin-right:15px;"><i class="fa-solid fa-pen"></i> Edit</button>
                 <button onclick="deleteEvent(${event.id})" style="background:none; border:none; cursor:pointer; color:#dc3545;"><i class="fa-solid fa-trash"></i> Delete</button>
            </div>
        `;
        container.appendChild(div);
    });
}

function getDotClass(type) {
    if (type.includes("Crying") || type === "Analysis") return "type-crying";
    if (type === "Feeding") return "type-feeding";
    if (type === "Sleep") return "type-sleep";
    return "";
}

// --- CRUD Operations ---
let currentEditingId = null;

function openEdit(id, cause, notes) {
    currentEditingId = id;
    document.getElementById('edit-cause').value = cause;
    document.getElementById('edit-notes').value = notes;
    document.getElementById('edit-modal').classList.remove('hidden');
}

function openAddModal() {
    document.getElementById('add-modal').classList.remove('hidden');
}

function closeModal(id) {
    document.getElementById(id).classList.add('hidden');
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
        fetchEvents();
    } catch (err) { alert('Error updating'); }
}

async function saveAdd() {
    try {
        const type = document.getElementById('add-type').value;
        const cause = document.getElementById('add-cause').value;
        const notes = document.getElementById('add-notes').value;

        // Note: Ideally create for specific date, but for now creates for NOW. 
        // User is adding to "This Day" view but backend stamps NOW.
        // We will accept this limitation or use a more complex backend.
        // For prototype, assuming user adds events for current day usually.

        await fetch('/api/calendar', {
            method: 'POST',
            body: JSON.stringify({ event_type: type, cause, notes }),
            headers: { 'Content-Type': 'application/json' }
        });
        closeModal('add-modal');
        // If we added an event for today and viewing today, it shows. 
        // If viewing past, it won't show here unless we backdate. Simple limitation.
        fetchEvents();
    } catch (err) { alert('Error saving'); }
}

async function deleteEvent(id) {
    if (!confirm("Delete this event?")) return;
    try {
        await fetch(`/api/calendar/${id}`, { method: 'DELETE' });
        fetchEvents();
    } catch (err) { alert('Error deleting'); }
}
