/**
 * Sundarkand Seva Team - Vanilla JavaScript ES Module Application
 */

// Application State
const state = {
  currentUser: null,
  activeTab: 'tab-events',
  events: { upcoming: [], past: [] },
  selectedEventId: null,
  activeEventDetail: null,
  chatMessages: [],
  oldestChatId: null,
  sseSource: null,
  vapidPublicKey: null,
  deferredInstallPrompt: null,
  swRegistration: null,
  isSubscribedPush: false
};

// --- UTILITIES ---

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type === 'error' ? 'toast-error' : type === 'success' ? 'toast-success' : ''}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3200);
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function getInitials(name) {
  if (!name) return 'S';
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return parts[0].slice(0, 2).toUpperCase();
}

// Convert "YYYY-MM-DD" and "HH:MM" into formatted local time: "Sat, 12 Oct · 6:30 PM"
function formatEventDateTime(dateStr, timeStr) {
  try {
    const [year, month, day] = dateStr.split('-').map(Number);
    const [hours, minutes] = timeStr.split(':').map(Number);
    const date = new Date(year, month - 1, day, hours, minutes);
    const datePart = date.toLocaleDateString(undefined, {
      weekday: 'short',
      day: 'numeric',
      month: 'short'
    });
    const timePart = date.toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    });
    return `${datePart} · ${timePart}`;
  } catch (e) {
    return `${dateStr} · ${timeStr}`;
  }
}

// Get Month Header (e.g. "October 2026")
function getMonthYearHeader(dateStr) {
  try {
    const [year, month, day] = dateStr.split('-').map(Number);
    const date = new Date(year, month - 1, day);
    return date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  } catch (e) {
    return 'Upcoming Events';
  }
}

// Convert Base64 URL-safe string to Uint8Array for VAPID subscription
function urlB64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

// --- API CLIENT ---

async function api(path, options = {}) {
  const defaultHeaders = {
    'Content-Type': 'application/json'
  };
  const config = {
    ...options,
    headers: {
      ...defaultHeaders,
      ...(options.headers || {})
    }
  };
  if (config.body && typeof config.body === 'object') {
    config.body = JSON.stringify(config.body);
  }

  try {
    const res = await fetch(path, config);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `Server error (${res.status})`);
    }
    return data;
  } catch (err) {
    throw err;
  }
}

// --- NAVIGATION & TABS ---

function switchTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.bottom-nav .nav-item').forEach(el => el.classList.remove('active'));

  const targetTab = document.getElementById(tabId);
  const targetBtn = document.querySelector(`.bottom-nav .nav-item[data-tab="${tabId}"]`);
  if (targetTab) targetTab.classList.add('active');
  if (targetBtn) targetBtn.classList.add('active');

  // Load relevant data
  if (tabId === 'tab-events') loadEvents();
  if (tabId === 'tab-attendance') loadAttendanceTab();
  if (tabId === 'tab-chat') {
    if (state.chatMessages.length === 0) loadChatMessages();
    scrollChatToBottom();
  }
  if (tabId === 'tab-profile') loadProfile();
}

async function refreshCurrentView(manual = true) {
  const spinIcons = document.querySelectorAll('.refresh-spinner-icon');
  spinIcons.forEach(el => el.classList.add('spinning'));

  try {
    if (state.activeTab === 'tab-events') {
      await loadEvents(true);
    } else if (state.activeTab === 'tab-attendance') {
      await loadAttendanceTab(true);
    } else if (state.activeTab === 'tab-chat') {
      await loadChatMessages();
    } else if (state.activeTab === 'tab-profile') {
      await loadProfile();
    }

    if (manual) {
      showToast('✨ Live data updated', 'success');
    }
  } catch (err) {
    if (manual) {
      showToast('Sync error: ' + err.message, 'error');
    }
  } finally {
    setTimeout(() => {
      spinIcons.forEach(el => el.classList.remove('spinning'));
    }, 600);
  }
}

function setupNavigation() {
  document.querySelectorAll('.bottom-nav .nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.getAttribute('data-tab');
      switchTab(tabId);
    });
  });

  // Sticky header refresh button
  const headerRefreshBtn = document.getElementById('btn-sticky-refresh');
  if (headerRefreshBtn) {
    headerRefreshBtn.addEventListener('click', () => refreshCurrentView(true));
  }

  // Auto-refresh when user switches back to the app/tab
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && state.currentUser) {
      refreshCurrentView(false);
    }
  });

  // Handle URL hash navigation (e.g. #/events/1 or #/chat)
  window.addEventListener('hashchange', handleRouteHash);
  // Service worker message navigation
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (event) => {
      if (event.data && event.data.type === 'NAVIGATE') {
        window.location.hash = event.data.url;
      }
    });
  }
}

function handleRouteHash() {
  const hash = window.location.hash;
  if (!hash || !state.currentUser) return;
  if (hash.startsWith('#/events/')) {
    const eventId = hash.replace('#/events/', '');
    switchTab('tab-events');
    openEventDetail(parseInt(eventId));
  } else if (hash.startsWith('#/chat')) {
    switchTab('tab-chat');
  } else if (hash.startsWith('#/attendance')) {
    switchTab('tab-attendance');
  } else if (hash.startsWith('#/profile')) {
    switchTab('tab-profile');
  }
}

// --- MODALS ---

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.add('open');
}

function closeModals() {
  document.querySelectorAll('.modal-overlay').forEach(el => el.classList.remove('open'));
}

window.App = {
  closeModals
};

// --- AUTHENTICATION ---

async function checkAuth() {
  try {
    const data = await api('/api/auth/check');
    if (data.user) {
      state.currentUser = data.user;
      renderAppShell();
      switchTab('tab-events');
      initPushNotifications();
      connectChatSSE();
      handleRouteHash();
    } else {
      showAuthScreen();
    }
  } catch (err) {
    showAuthScreen();
  }
}

function showAuthScreen() {
  document.getElementById('auth-view').style.display = 'flex';
  document.getElementById('app-container').style.display = 'none';
  document.getElementById('auth-email-form').style.display = 'block';
  document.getElementById('auth-login-form').style.display = 'none';
  document.getElementById('auth-register-form').style.display = 'none';
}

function renderAppShell() {
  document.getElementById('auth-view').style.display = 'none';
  document.getElementById('app-container').style.display = 'flex';

  const user = state.currentUser;
  const roleBadge = document.getElementById('user-role-badge');
  if (user.role === 'admin') {
    roleBadge.textContent = 'Admin';
    roleBadge.className = 'role-badge admin';
    document.getElementById('btn-new-event').style.display = 'inline-flex';
    document.getElementById('admin-section').style.display = 'block';
  } else {
    roleBadge.textContent = 'Sevak';
    roleBadge.className = 'role-badge member';
    document.getElementById('btn-new-event').style.display = 'none';
    document.getElementById('admin-section').style.display = 'none';
  }

  // Update profile avatar & name
  document.getElementById('profile-avatar').textContent = getInitials(user.display_name);
  document.getElementById('profile-name-heading').textContent = user.display_name;
  document.getElementById('profile-email-text').textContent = user.email;
  document.getElementById('profile-display-name-input').value = user.display_name;
}

function setupAuthForms() {
  let pendingEmail = '';

  // 1. Check email invite status
  const emailForm = document.getElementById('auth-email-form');
  emailForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const emailInput = document.getElementById('auth-email');
    const email = emailInput.value.trim().toLowerCase();
    const btn = document.getElementById('btn-check-email');
    btn.disabled = true;
    btn.textContent = 'Checking...';

    try {
      const res = await api('/api/auth/status-for-email', {
        method: 'POST',
        body: { email }
      });
      pendingEmail = email;

      if (!res.invited) {
        showToast('This email has not been invited yet. Please contact the team admin for an invite.', 'error');
        btn.disabled = false;
        btn.textContent = 'Continue';
        return;
      }

      emailForm.style.display = 'none';
      if (res.registered) {
        // Registered -> Show Login form
        const loginForm = document.getElementById('auth-login-form');
        loginForm.style.display = 'block';
        document.getElementById('login-password').focus();
      } else {
        // Newly invited -> Show Register form
        const regForm = document.getElementById('auth-register-form');
        regForm.style.display = 'block';
        document.getElementById('reg-display-name').focus();
      }
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Continue';
    }
  });

  // Back to email buttons
  document.getElementById('btn-back-to-email').addEventListener('click', () => {
    document.getElementById('auth-login-form').style.display = 'none';
    document.getElementById('auth-email-form').style.display = 'block';
  });
  document.getElementById('btn-back-to-email-2').addEventListener('click', () => {
    document.getElementById('auth-register-form').style.display = 'none';
    document.getElementById('auth-email-form').style.display = 'block';
  });

  // 2. Login submit
  document.getElementById('auth-login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const password = document.getElementById('login-password').value;
    const btn = document.getElementById('btn-login-submit');
    btn.disabled = true;
    btn.textContent = 'Signing in...';

    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: { email: pendingEmail, password }
      });
      showToast('Jai Siya Ram! Welcome back.', 'success');
      state.currentUser = data.user;
      renderAppShell();
      switchTab('tab-events');
      initPushNotifications();
      connectChatSSE();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Sign In';
    }
  });

  // 3. Register submit
  document.getElementById('auth-register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const displayName = document.getElementById('reg-display-name').value.trim();
    const password = document.getElementById('reg-password').value;
    const btn = document.getElementById('btn-register-submit');
    btn.disabled = true;
    btn.textContent = 'Activating...';

    try {
      const data = await api('/api/auth/register', {
        method: 'POST',
        body: { email: pendingEmail, display_name: displayName, password }
      });
      showToast('Account activated! Welcome to the Seva Team.', 'success');
      state.currentUser = data.user;
      renderAppShell();
      switchTab('tab-events');
      initPushNotifications();
      connectChatSSE();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Activate Account';
    }
  });

  // 4. Sign Out
  document.getElementById('btn-sign-out').addEventListener('click', async () => {
    if (!confirm('Are you sure you want to sign out?')) return;
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } catch (e) {}
    if (state.sseSource) {
      state.sseSource.close();
      state.sseSource = null;
    }
    state.currentUser = null;
    showAuthScreen();
    showToast('Signed out successfully.');
  });
}

// --- EVENTS SCREEN ---

async function loadEvents(silent = false) {
  const container = document.getElementById('events-upcoming-container');
  if (!silent && (!state.events.upcoming || state.events.upcoming.length === 0)) {
    container.innerHTML = '<div class="spinner"></div>';
  }
  try {
    const data = await api('/api/events');
    state.events = data;

    renderUpcomingEvents(data.upcoming);
    renderPastEvents(data.past);
  } catch (err) {
    if (!silent) {
      container.innerHTML = `<div class="empty-state"><p>Error loading events: ${escapeHtml(err.message)}</p></div>`;
    }
  }
}

function renderUpcomingEvents(upcoming) {
  const container = document.getElementById('events-upcoming-container');
  if (!upcoming || upcoming.length === 0) {
    container.innerHTML = `
      <div class="card empty-state">
        <div class="empty-state-icon">🪔</div>
        <h3>No Upcoming Seva Events</h3>
        <p>Stay tuned! You will receive a notification when the next Sundarkand event is announced.</p>
      </div>
    `;
    return;
  }

  // Group by Month
  const grouped = {};
  upcoming.forEach(ev => {
    const header = getMonthYearHeader(ev.event_date);
    if (!grouped[header]) grouped[header] = [];
    grouped[header].push(ev);
  });

  let html = '';
  for (const [month, eventsList] of Object.entries(grouped)) {
    html += `<div class="month-divider">${escapeHtml(month)}</div>`;
    eventsList.forEach(ev => {
      html += renderEventCardHtml(ev);
    });
  }
  container.innerHTML = html;

  // Add click listeners to event cards
  container.querySelectorAll('.event-card').forEach(card => {
    card.addEventListener('click', () => {
      const id = parseInt(card.getAttribute('data-id'));
      openEventDetail(id);
    });
  });
}

function renderPastEvents(past) {
  const section = document.getElementById('past-events-section');
  const countSpan = document.getElementById('past-events-count');
  const container = document.getElementById('events-past-container');

  if (!past || past.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  countSpan.textContent = past.length;

  let html = '';
  past.forEach(ev => {
    html += renderEventCardHtml(ev, true);
  });
  container.innerHTML = html;

  container.querySelectorAll('.event-card').forEach(card => {
    card.addEventListener('click', () => {
      const id = parseInt(card.getAttribute('data-id'));
      openEventDetail(id);
    });
  });
}

function renderEventCardHtml(ev, isPast = false) {
  const dateTimeFormatted = formatEventDateTime(ev.event_date, ev.start_time);
  let rsvpBadgeClass = 'no_response';
  let rsvpBadgeText = 'No RSVP';

  if (ev.my_rsvp === 'going') {
    rsvpBadgeClass = 'going';
    rsvpBadgeText = 'Going 🌸';
  } else if (ev.my_rsvp === 'maybe') {
    rsvpBadgeClass = 'maybe';
    rsvpBadgeText = 'Maybe 🙏';
  } else if (ev.my_rsvp === 'not_going') {
    rsvpBadgeClass = 'not_going';
    rsvpBadgeText = 'Not Going ❌';
  }

  return `
    <div class="card event-card ${isPast ? 'past-card' : ''}" data-id="${ev.id}">
      <div class="event-card-top">
        <div class="event-title">${escapeHtml(ev.title)}</div>
        <span class="rsvp-badge ${rsvpBadgeClass}">${rsvpBadgeText}</span>
      </div>
      <div class="event-meta">
        <div class="event-meta-row">
          <span class="event-meta-icon">⏰</span>
          <span>${escapeHtml(dateTimeFormatted)}</span>
        </div>
        <div class="event-meta-row">
          <span class="event-meta-icon">📍</span>
          <span>${escapeHtml(ev.venue)}</span>
        </div>
      </div>
    </div>
  `;
}

// Event Detail Modal
async function openEventDetail(eventId, silent = false) {
  state.selectedEventId = eventId;
  if (!silent) {
    openModal('modal-event-detail');
  }

  try {
    const data = await api(`/api/events/${eventId}`);
    const ev = data.event;
    state.activeEventDetail = ev;

    document.getElementById('modal-event-title').textContent = ev.title;
    document.getElementById('modal-event-datetime').textContent = formatEventDateTime(ev.event_date, ev.start_time);
    document.getElementById('modal-event-venue').textContent = ev.venue;
    document.getElementById('modal-event-address').textContent = ev.address || 'Address provided at venue';

    // Google Maps Link
    const query = encodeURIComponent(`${ev.venue} ${ev.address || ''}`.trim());
    document.getElementById('modal-event-maps-btn').href = `https://www.google.com/maps/search/?api=1&query=${query}`;

    // Notes
    const notesBox = document.getElementById('modal-event-notes-box');
    const notesEl = document.getElementById('modal-event-notes');
    if (ev.notes && ev.notes.trim()) {
      notesEl.textContent = ev.notes;
      notesBox.style.display = 'block';
    } else {
      notesBox.style.display = 'none';
    }

    // Attendees preview: Who is Going
    const attendeesBox = document.getElementById('modal-event-attendees-box');
    const goingList = document.getElementById('modal-event-going-list');
    const goingCount = document.getElementById('modal-event-going-count');

    try {
      const attData = await api(`/api/attendance/${eventId}`);
      if (attData.roster && attData.roster.going.length > 0) {
        const names = attData.roster.going.map(m => m.display_name).join(', ');
        goingList.textContent = names;
        goingCount.textContent = attData.roster.going.length;
        attendeesBox.style.display = 'block';
      } else {
        attendeesBox.style.display = 'none';
      }
    } catch (e) {
      if (attendeesBox) attendeesBox.style.display = 'none';
    }

    // Highlight active RSVP button
    highlightRsvpButton(ev.my_rsvp);

    // Admin edit/delete buttons
    const adminActions = document.getElementById('modal-event-admin-actions');
    if (state.currentUser && state.currentUser.role === 'admin') {
      adminActions.style.display = 'block';
    } else {
      adminActions.style.display = 'none';
    }
  } catch (err) {
    if (!silent) {
      showToast(err.message, 'error');
      closeModals();
    }
  }
}

function highlightRsvpButton(status) {
  document.querySelectorAll('.rsvp-choice-btn').forEach(btn => {
    btn.classList.remove('selected-going', 'selected-maybe', 'selected-not_going');
    if (btn.getAttribute('data-status') === status) {
      btn.classList.add(`selected-${status}`);
    }
  });
}

function setupEventHandlers() {
  // Toggle past events accordion
  document.getElementById('btn-toggle-past-events').addEventListener('click', () => {
    const container = document.getElementById('events-past-container');
    const icon = document.getElementById('past-accordion-icon');
    container.classList.toggle('open');
    icon.textContent = container.classList.contains('open') ? '▲' : '▼';
  });

  // RSVP Buttons click
  document.querySelectorAll('.rsvp-choice-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const status = btn.getAttribute('data-status');
      if (!state.selectedEventId) return;

      try {
        await api(`/api/events/${state.selectedEventId}/rsvp`, {
          method: 'POST',
          body: { status }
        });
        highlightRsvpButton(status);
        showToast(`Attendance marked as: ${status.replace('_', ' ').toUpperCase()}`, 'success');
        // Refresh events list in background
        loadEvents();
      } catch (err) {
        showToast(err.message, 'error');
      }
    });
  });

  // Admin: Open Create Event modal
  document.getElementById('btn-new-event').addEventListener('click', () => {
    document.getElementById('modal-form-heading').textContent = 'Schedule New Seva';
    document.getElementById('form-event-id').value = '';
    document.getElementById('event-editor-form').reset();
    openModal('modal-event-form');
  });

  // Admin: Open Edit Event modal
  document.getElementById('btn-edit-current-event').addEventListener('click', () => {
    const ev = state.activeEventDetail;
    if (!ev) return;
    closeModals();

    document.getElementById('modal-form-heading').textContent = 'Edit Seva Event';
    document.getElementById('form-event-id').value = ev.id;
    document.getElementById('form-title').value = ev.title;
    document.getElementById('form-date').value = ev.event_date;
    document.getElementById('form-time').value = ev.start_time;
    document.getElementById('form-venue').value = ev.venue;
    document.getElementById('form-address').value = ev.address || '';
    document.getElementById('form-notes').value = ev.notes || '';

    openModal('modal-event-form');
  });

  // Admin: Delete current event
  document.getElementById('btn-delete-current-event').addEventListener('click', async () => {
    if (!confirm('Are you sure you want to delete this event? This action cannot be undone.')) return;
    try {
      await api(`/api/events/${state.selectedEventId}`, { method: 'DELETE' });
      showToast('Event deleted successfully.');
      closeModals();
      loadEvents();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // Admin: Save Event (Create or Edit)
  document.getElementById('event-editor-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('form-event-id').value;
    const body = {
      title: document.getElementById('form-title').value.trim(),
      event_date: document.getElementById('form-date').value,
      start_time: document.getElementById('form-time').value,
      venue: document.getElementById('form-venue').value.trim(),
      address: document.getElementById('form-address').value.trim(),
      notes: document.getElementById('form-notes').value.trim()
    };

    const btn = document.getElementById('btn-save-event');
    btn.disabled = true;

    try {
      if (eventId) {
        await api(`/api/events/${eventId}`, { method: 'PUT', body });
        showToast('Event updated successfully.', 'success');
      } else {
        await api('/api/events', { method: 'POST', body });
        showToast('New Seva Event scheduled and announced!', 'success');
      }
      closeModals();
      loadEvents();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });
}

// --- ATTENDANCE SCREEN ---

async function loadAttendanceTab(silent = false) {
  const select = document.getElementById('attendance-event-select');
  try {
    const data = await api('/api/events');
    const allEvents = [...data.upcoming, ...data.past];

    if (allEvents.length === 0) {
      select.innerHTML = '<option value="">No events found</option>';
      document.getElementById('attendance-content').innerHTML = `
        <div class="card empty-state"><p>No events scheduled yet.</p></div>
      `;
      return;
    }

    // Populate dropdown
    const currentVal = select.value;
    let html = '';
    allEvents.forEach(ev => {
      html += `<option value="${ev.id}">${escapeHtml(ev.title)} (${ev.event_date})</option>`;
    });
    select.innerHTML = html;

    if (currentVal && allEvents.some(e => e.id == currentVal)) {
      select.value = currentVal;
    } else {
      select.value = allEvents[0].id;
    }

    loadAttendanceData(select.value, silent);
  } catch (err) {
    if (!silent) showToast(err.message, 'error');
  }
}

async function loadAttendanceData(eventId, silent = false) {
  const container = document.getElementById('attendance-content');
  if (!silent) {
    container.innerHTML = '<div class="spinner"></div>';
  }

  try {
    const res = await api(`/api/attendance/${eventId}`);
    const counts = res.counts;
    const total = counts.total_members || 1;

    // Progress bar ratios
    const pGoing = (counts.going / total) * 100;
    const pMaybe = (counts.maybe / total) * 100;
    const pNotGoing = (counts.not_going / total) * 100;
    const pNone = (counts.no_response / total) * 100;

    let html = `
      <!-- Metric Cards -->
      <div class="attendance-summary-grid">
        <div class="attendance-metric-card">
          <div class="metric-count metric-going">${counts.going}</div>
          <div class="metric-label">Going 🌸</div>
        </div>
        <div class="attendance-metric-card">
          <div class="metric-count metric-maybe">${counts.maybe}</div>
          <div class="metric-label">Maybe 🙏</div>
        </div>
        <div class="attendance-metric-card">
          <div class="metric-count metric-not-going">${counts.not_going}</div>
          <div class="metric-label">No ❌</div>
        </div>
        <div class="attendance-metric-card">
          <div class="metric-count metric-none">${counts.no_response}</div>
          <div class="metric-label">Pending ⏳</div>
        </div>
      </div>

      <!-- Ratio Bar -->
      <div class="attendance-progress-bar">
        <div class="progress-segment seg-going" style="width: ${pGoing}%" title="Going: ${counts.going}"></div>
        <div class="progress-segment seg-maybe" style="width: ${pMaybe}%" title="Maybe: ${counts.maybe}"></div>
        <div class="progress-segment seg-not-going" style="width: ${pNotGoing}%" title="Not Going: ${counts.not_going}"></div>
        <div class="progress-segment seg-none" style="width: ${pNone}%" title="No response: ${counts.no_response}"></div>
      </div>
    `;

    // Make roster visible for everybody so anyone can see who is going and who is not
    if (res.roster) {
      html += renderAttendanceRoster(res.roster);
    }

    container.innerHTML = html;
  } catch (err) {
    if (!silent) {
      container.innerHTML = `<div class="card empty-state"><p>Error: ${escapeHtml(err.message)}</p></div>`;
    }
  }
}

function renderAttendanceRoster(roster) {
  let html = `<div style="margin-top: 10px;">`;

  const sections = [
    { title: '🌸 Confirmed (Going)', list: roster.going, count: roster.going.length },
    { title: '🙏 Tentative (Maybe)', list: roster.maybe, count: roster.maybe.length },
    { title: '❌ Not Going', list: roster.not_going, count: roster.not_going.length },
    { title: '⏳ Pending Response', list: roster.no_response, count: roster.no_response.length }
  ];

  sections.forEach(sec => {
    html += `
      <div class="attendance-group-title">
        <span>${sec.title}</span>
        <span style="font-size: 0.78rem; color: var(--text-muted);">(${sec.count})</span>
      </div>
    `;
    if (sec.list.length === 0) {
      html += `<div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 8px; padding-left: 4px;">None</div>`;
    } else {
      sec.list.forEach(m => {
        html += `
          <div class="member-list-item">
            <div class="member-info">
              <div class="avatar-circle">${getInitials(m.display_name)}</div>
              <div>
                <div style="font-weight: 600; font-size: 0.88rem; color: var(--text-dark);">${escapeHtml(m.display_name)}</div>
                <div style="font-size: 0.72rem; color: var(--text-muted);">${escapeHtml(m.email)}</div>
              </div>
            </div>
            <span class="role-badge ${m.role}">${m.role}</span>
          </div>
        `;
      });
    }
  });

  html += `</div>`;
  return html;
}

function setupAttendanceHandlers() {
  document.getElementById('attendance-event-select').addEventListener('change', (e) => {
    loadAttendanceData(e.target.value);
  });
}

// --- REALTIME SSE STREAM & LIVE UPDATES ---

function connectChatSSE() {
  if (state.sseSource) return;

  const sse = new EventSource('/api/chat/stream');
  state.sseSource = sse;

  // Realtime Chat Message
  sse.addEventListener('message', (event) => {
    try {
      const msg = JSON.parse(event.data);
      appendChatMessage(msg);
      scrollChatToBottom();
    } catch (e) {}
  });

  // Realtime Chat Message Delete
  sse.addEventListener('delete', (event) => {
    try {
      const data = JSON.parse(event.data);
      const row = document.querySelector(`.chat-bubble-row[data-id="${data.id}"]`);
      if (row) row.remove();
      state.chatMessages = state.chatMessages.filter(m => m.id !== data.id);
    } catch (e) {}
  });

  // Realtime RSVP Updates (Live reflecting across app)
  sse.addEventListener('rsvp', (event) => {
    try {
      const data = JSON.parse(event.data);
      // 1. If currently viewing Events tab, refresh events silently
      if (state.activeTab === 'tab-events') {
        loadEvents(true);
      }
      // 2. If currently viewing Attendance tab and this event is selected, refresh attendance silently
      if (state.activeTab === 'tab-attendance') {
        const select = document.getElementById('attendance-event-select');
        if (select && select.value == data.event_id) {
          loadAttendanceData(data.event_id, true);
        }
      }
      // 3. If modal event detail is currently open for this event, refresh detail
      if (state.selectedEventId === data.event_id && document.getElementById('modal-event-detail').classList.contains('open')) {
        openEventDetail(data.event_id, true);
      }
      // 4. Toast notification if another member updated RSVP
      if (state.currentUser && data.user_id !== state.currentUser.id) {
        const statusMap = { going: 'Going 🌸', maybe: 'Maybe 🙏', not_going: 'Not Going ❌' };
        showToast(`${data.display_name} is ${statusMap[data.status] || data.status}`);
      }
    } catch (e) {}
  });

  // Realtime Event Created
  sse.addEventListener('event_created', (event) => {
    try {
      loadEvents(true);
      if (state.activeTab === 'tab-attendance') loadAttendanceTab(true);
      showToast('📅 A new Seva Event was just scheduled!', 'info');
    } catch (e) {}
  });

  // Realtime Event Updated
  sse.addEventListener('event_updated', (event) => {
    try {
      loadEvents(true);
      if (state.activeTab === 'tab-attendance') {
        const select = document.getElementById('attendance-event-select');
        if (select) loadAttendanceData(select.value, true);
      }
      if (state.selectedEventId && document.getElementById('modal-event-detail').classList.contains('open')) {
        openEventDetail(state.selectedEventId, true);
      }
    } catch (e) {}
  });

  // Realtime Event Deleted
  sse.addEventListener('event_deleted', (event) => {
    try {
      loadEvents(true);
      if (state.activeTab === 'tab-attendance') loadAttendanceTab(true);
      if (state.selectedEventId && document.getElementById('modal-event-detail').classList.contains('open')) {
        closeModals();
        showToast('This seva event was cancelled.', 'info');
      }
    } catch (e) {}
  });

  // Realtime User / Profile Name Updated
  sse.addEventListener('user_updated', (event) => {
    try {
      if (state.activeTab === 'tab-attendance') {
        const select = document.getElementById('attendance-event-select');
        if (select) loadAttendanceData(select.value, true);
      }
    } catch (e) {}
  });

  sse.onerror = () => {
    // EventSource handles auto-reconnects natively
  };
}

async function loadChatMessages(beforeId = null) {
  const list = document.getElementById('chat-messages-list');
  const url = beforeId ? `/api/chat?before_id=${beforeId}` : '/api/chat';

  try {
    const data = await api(url);
    const msgs = data.messages || [];

    if (!beforeId) {
      state.chatMessages = msgs;
      renderChatList(msgs);
      scrollChatToBottom();
    } else {
      if (msgs.length > 0) {
        state.chatMessages = [...msgs, ...state.chatMessages];
        prependChatList(msgs);
      }
    }

    if (msgs.length > 0) {
      state.oldestChatId = msgs[0].id;
    }

    const loadMoreBtn = document.getElementById('chat-load-more');
    if (msgs.length >= 50) {
      loadMoreBtn.style.display = 'block';
    } else {
      loadMoreBtn.style.display = 'none';
    }
  } catch (err) {
    if (!beforeId) {
      list.innerHTML = `<div class="empty-state"><p>Unable to load chat: ${escapeHtml(err.message)}</p></div>`;
    }
  }
}

function renderChatList(messages) {
  const list = document.getElementById('chat-messages-list');
  if (!messages || messages.length === 0) {
    list.innerHTML = `
      <div class="card empty-state">
        <div class="empty-state-icon">💬</div>
        <h3>Seva Team Group Chat</h3>
        <p>Jai Siya Ram! Start the conversation with your fellow sevaks.</p>
      </div>
    `;
    return;
  }

  let html = '';
  messages.forEach(msg => {
    html += renderChatBubbleHtml(msg);
  });
  list.innerHTML = html;
}

function prependChatList(messages) {
  const list = document.getElementById('chat-messages-list');
  const history = document.getElementById('chat-history');
  const oldScrollHeight = history.scrollHeight;

  const temp = document.createElement('div');
  let html = '';
  messages.forEach(msg => {
    html += renderChatBubbleHtml(msg);
  });
  temp.innerHTML = html;

  list.prepend(...temp.childNodes);
  // Restore scroll position
  history.scrollTop = history.scrollHeight - oldScrollHeight;
}

function appendChatMessage(msg) {
  const list = document.getElementById('chat-messages-list');
  // If empty state exists, clear it
  const emptyState = list.querySelector('.empty-state');
  if (emptyState) list.innerHTML = '';

  // Avoid duplicates
  if (document.querySelector(`.chat-bubble-row[data-id="${msg.id}"]`)) return;

  state.chatMessages.push(msg);
  const temp = document.createElement('div');
  temp.innerHTML = renderChatBubbleHtml(msg);
  list.appendChild(temp.firstElementChild);
}

function renderChatBubbleHtml(msg) {
  const isMe = state.currentUser && state.currentUser.id === msg.user_id;
  const canDelete = isMe || (state.currentUser && state.currentUser.role === 'admin');

  // Format time
  let timeStr = '';
  try {
    const d = new Date(msg.created_at);
    timeStr = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true });
  } catch (e) {
    timeStr = '';
  }

  return `
    <div class="chat-bubble-row ${isMe ? 'me' : 'other'}" data-id="${msg.id}">
      <div class="avatar-circle" style="width: 30px; height: 30px; font-size: 0.72rem;">${getInitials(msg.display_name)}</div>
      <div class="chat-bubble-body">
        <div class="chat-sender-name">${escapeHtml(msg.display_name)}</div>
        <div class="chat-bubble">
          ${escapeHtml(msg.content)}
          <div class="chat-meta">
            <span>${timeStr}</span>
            ${canDelete ? `<button class="chat-delete-btn" onclick="window.App.deleteChatMessage(${msg.id})" title="Delete message">✕</button>` : ''}
          </div>
        </div>
      </div>
    </div>
  `;
}

function scrollChatToBottom() {
  const history = document.getElementById('chat-history');
  setTimeout(() => {
    history.scrollTop = history.scrollHeight;
  }, 50);
}

window.App.deleteChatMessage = async function(msgId) {
  if (!confirm('Delete this message?')) return;
  try {
    await api(`/api/chat/${msgId}`, { method: 'DELETE' });
  } catch (err) {
    showToast(err.message, 'error');
  }
};

function setupChatHandlers() {
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input-text');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const content = input.value.trim();
    if (!content) return;

    input.value = '';
    try {
      await api('/api/chat', {
        method: 'POST',
        body: { content }
      });
      scrollChatToBottom();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  document.getElementById('btn-load-older-chat').addEventListener('click', () => {
    if (state.oldestChatId) {
      loadChatMessages(state.oldestChatId);
    }
  });
}

// --- PROFILE & ADMIN SCREEN ---

async function loadProfile() {
  try {
    const data = await api('/api/profile');
    const prefs = data.notif_prefs;

    document.getElementById('pref-new-events').checked = prefs.new_events;
    document.getElementById('pref-event-changes').checked = prefs.event_changes;
    document.getElementById('pref-reminders').checked = prefs.reminders;
    document.getElementById('pref-chat').checked = prefs.chat;

    if (state.currentUser.role === 'admin') {
      loadAdminTeamManagement();
    }
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function loadAdminTeamManagement() {
  try {
    const data = await api('/api/admin/members');

    // Render Pending Invites
    const invContainer = document.getElementById('admin-pending-invites-list');
    if (data.pending_invites.length === 0) {
      invContainer.innerHTML = '<p style="font-size: 0.82rem; color: var(--text-muted);">No pending invites.</p>';
    } else {
      let invHtml = '';
      data.pending_invites.forEach(inv => {
        invHtml += `
          <div class="member-list-item">
            <div>
              <div style="font-weight: 600; font-size: 0.85rem;">${escapeHtml(inv.email)}</div>
              <div style="font-size: 0.72rem; color: var(--text-muted);">Invited: ${inv.created_at.slice(0, 10)}</div>
            </div>
            <button class="btn btn-outline-maroon btn-sm" onclick="window.App.cancelInvite('${encodeURIComponent(inv.email)}')">Revoke</button>
          </div>
        `;
      });
      invContainer.innerHTML = invHtml;
    }

    // Render Registered Members
    const memContainer = document.getElementById('admin-members-list');
    let memHtml = '';
    data.members.forEach(m => {
      const isSelf = m.id === state.currentUser.id;
      memHtml += `
        <div class="member-list-item">
          <div class="member-info">
            <div class="avatar-circle">${getInitials(m.display_name)}</div>
            <div>
              <div style="font-weight: 600; font-size: 0.88rem;">${escapeHtml(m.display_name)} ${isSelf ? '(You)' : ''}</div>
              <div style="font-size: 0.72rem; color: var(--text-muted);">${escapeHtml(m.email)}</div>
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 6px;">
            ${!isSelf ? `
              <button class="btn btn-secondary btn-sm" onclick="window.App.toggleRole(${m.id}, '${m.role === 'admin' ? 'member' : 'admin'}')">
                ${m.role === 'admin' ? 'Make Sevak' : 'Make Admin'}
              </button>
              <button class="btn btn-outline-maroon btn-sm" onclick="window.App.removeMember(${m.id})" title="Remove user">✕</button>
            ` : `<span class="role-badge admin">Admin</span>`}
          </div>
        </div>
      `;
    });
    memContainer.innerHTML = memHtml;

  } catch (err) {
    showToast(err.message, 'error');
  }
}

window.App.cancelInvite = async function(encodedEmail) {
  try {
    await api(`/api/admin/invites/${encodedEmail}`, { method: 'DELETE' });
    showToast('Invite cancelled.');
    loadAdminTeamManagement();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

window.App.toggleRole = async function(userId, newRole) {
  try {
    await api(`/api/admin/members/${userId}/role`, {
      method: 'POST',
      body: { role: newRole }
    });
    showToast(`Role updated to: ${newRole}`, 'success');
    loadAdminTeamManagement();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

window.App.removeMember = async function(userId) {
  if (!confirm('Are you sure you want to remove this member from the team?')) return;
  try {
    await api(`/api/admin/members/${userId}`, { method: 'DELETE' });
    showToast('Member removed.');
    loadAdminTeamManagement();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

function setupProfileHandlers() {
  // Update display name
  document.getElementById('edit-name-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('profile-display-name-input').value.trim();
    if (!name) return;

    try {
      await api('/api/profile/name', {
        method: 'PUT',
        body: { display_name: name }
      });
      state.currentUser.display_name = name;
      document.getElementById('profile-name-heading').textContent = name;
      document.getElementById('profile-avatar').textContent = getInitials(name);
      showToast('Name updated successfully!', 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // Toggles for notification prefs
  const prefInputs = ['pref-new-events', 'pref-event-changes', 'pref-reminders', 'pref-chat'];
  prefInputs.forEach(id => {
    document.getElementById(id).addEventListener('change', async () => {
      const body = {
        new_events: document.getElementById('pref-new-events').checked,
        event_changes: document.getElementById('pref-event-changes').checked,
        reminders: document.getElementById('pref-reminders').checked,
        chat: document.getElementById('pref-chat').checked
      };
      try {
        await api('/api/profile/notifications', { method: 'PUT', body });
        showToast('Notification preferences updated.', 'success');
      } catch (err) {
        showToast(err.message, 'error');
      }
    });
  });

  // Enable Push Notifications button
  document.getElementById('btn-enable-push').addEventListener('click', subscribeToPush);

  // Send Test Notification button
  document.getElementById('btn-test-notification').addEventListener('click', async () => {
    try {
      const res = await api('/api/push/test', { method: 'POST' });
      showToast(res.message || 'Test push notification sent!', 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // Install guide card trigger
  document.getElementById('card-install-guide').addEventListener('click', () => {
    openModal('modal-install-guide');
  });

  // Admin: Invite form
  document.getElementById('admin-invite-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('invite-email-input');
    const email = input.value.trim().toLowerCase();
    if (!email) return;

    try {
      await api('/api/admin/invites', {
        method: 'POST',
        body: { email }
      });
      input.value = '';
      showToast(`Invited ${email} successfully!`, 'success');
      loadAdminTeamManagement();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

// --- PUSH NOTIFICATIONS ---

async function initPushNotifications() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    document.getElementById('push-permission-status').textContent = 'Web push not supported in this browser';
    document.getElementById('btn-enable-push').disabled = true;
    return;
  }

  try {
    const data = await api('/api/push/vapid-public-key');
    state.vapidPublicKey = data.public_key;

    const reg = await navigator.serviceWorker.ready;
    state.swRegistration = reg;

    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      state.isSubscribedPush = true;
      document.getElementById('push-permission-status').textContent = '✅ Push notifications are active';
      document.getElementById('btn-enable-push').textContent = 'Active';
      document.getElementById('btn-enable-push').classList.replace('btn-primary', 'btn-secondary');
    } else {
      updatePushStatusByPermission();
    }
  } catch (err) {
    console.warn('[PUSH] Init failed:', err);
  }
}

function updatePushStatusByPermission() {
  const perm = Notification.permission;
  const statusEl = document.getElementById('push-permission-status');
  const btn = document.getElementById('btn-enable-push');

  if (perm === 'granted') {
    statusEl.textContent = 'Permission granted. Tap to register device.';
    btn.textContent = 'Register';
  } else if (perm === 'denied') {
    statusEl.textContent = 'Notifications blocked in browser settings.';
    btn.textContent = 'Blocked';
    btn.disabled = true;
  } else {
    statusEl.textContent = 'Enable alerts for seva events and reminders.';
    btn.textContent = 'Enable';
  }
}

async function subscribeToPush() {
  if (!state.vapidPublicKey) {
    showToast('Push key not available on server', 'error');
    return;
  }

  try {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      showToast('Push permission was not granted.', 'error');
      updatePushStatusByPermission();
      return;
    }

    const reg = state.swRegistration || await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(state.vapidPublicKey)
      });
    }

    // Send subscription to server
    const subJson = sub.toJSON();
    await api('/api/push/subscribe', {
      method: 'POST',
      body: subJson
    });

    state.isSubscribedPush = true;
    document.getElementById('push-permission-status').textContent = '✅ Push notifications are active';
    document.getElementById('btn-enable-push').textContent = 'Active';
    document.getElementById('btn-enable-push').classList.replace('btn-primary', 'btn-secondary');
    showToast('Push notifications successfully enabled!', 'success');
  } catch (err) {
    showToast('Failed to subscribe: ' + err.message, 'error');
  }
}

// --- PWA INSTALL PROMPT HANDLING ---

function setupPWAInstall() {
  // Register Service Worker
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').then((reg) => {
      state.swRegistration = reg;
    }).catch((err) => {
      console.warn('[SW] Registration failed:', err);
    });
  }

  // Check if running as standalone
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const installBannerDismissed = localStorage.getItem('sundarkand_install_dismissed');

  // Android & Chrome beforeinstallprompt event
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    state.deferredInstallPrompt = e;

    if (!isStandalone && !installBannerDismissed) {
      document.getElementById('install-banner').style.display = 'flex';
    }
  });

  // Show banner for iOS if not installed
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  if (isIOS && !isStandalone && !installBannerDismissed) {
    document.getElementById('install-banner').style.display = 'flex';
  }

  // Banner Install button
  document.getElementById('btn-install-banner').addEventListener('click', async () => {
    if (state.deferredInstallPrompt) {
      state.deferredInstallPrompt.prompt();
      const choice = await state.deferredInstallPrompt.userChoice;
      if (choice.outcome === 'accepted') {
        document.getElementById('install-banner').style.display = 'none';
      }
      state.deferredInstallPrompt = null;
    } else {
      // Open guide modal for iOS or manual install
      openModal('modal-install-guide');
    }
  });

  // Banner Dismiss button
  document.getElementById('btn-dismiss-install').addEventListener('click', () => {
    document.getElementById('install-banner').style.display = 'none';
    localStorage.setItem('sundarkand_install_dismissed', 'true');
  });
}

// --- APP INITIALIZATION ---

document.addEventListener('DOMContentLoaded', () => {
  setupNavigation();
  setupAuthForms();
  setupEventHandlers();
  setupAttendanceHandlers();
  setupChatHandlers();
  setupProfileHandlers();
  setupPWAInstall();

  // Begin auth check
  checkAuth();
});
