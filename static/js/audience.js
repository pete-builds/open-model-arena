// Audience polls (presenter side): open a poll for the current battle, show
// the join QR + code, keep the tally live, and close it into a recorded vote.

import { $, getCsrfToken, state } from './state.js';
import { showReveal } from './battle.js';

const POLL_INTERVAL_MS = 2000;

export function showAudienceControls() {
    $('#audience-section').classList.remove('hidden');
}

export function resetAudience() {
    stopPolling();
    state.poll = null;
    $('#audience-section').classList.add('hidden');
    $('#audience-panel').classList.add('hidden');
    $('#audience-qr').innerHTML = '';
    renderTally({ a: 0, b: 0, tie: 0, total: 0 });
    const openBtn = $('#audience-open-btn');
    openBtn.disabled = false;
    openBtn.textContent = 'let the audience vote (phones)';
    const closeBtn = $('#audience-close-btn');
    closeBtn.disabled = false;
    closeBtn.textContent = 'CLOSE POLL & REVEAL';
    $('#audience-live').textContent = 'live';
}

function stopPolling() {
    if (state.poll && state.poll.timer) {
        clearInterval(state.poll.timer);
        state.poll.timer = null;
    }
}

function renderTally(tally) {
    const total = tally.total || 0;
    ['a', 'tie', 'b'].forEach(k => {
        const n = tally[k] || 0;
        $(`#tally-count-${k}`).textContent = n;
        $(`#tally-fill-${k}`).style.width = total ? `${Math.round((n / total) * 100)}%` : '0%';
    });
    $('#audience-total').textContent = total;
}

async function presenterFetch(path, method = 'GET') {
    const resp = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
    });
    if (resp.status === 401 || resp.status === 403) {
        window.location.href = '/login';
        throw new Error('not signed in');
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.detail || `${method} ${path} failed`);
    return body;
}

export async function openAudiencePoll() {
    if (!state.currentBattleId) return;
    const openBtn = $('#audience-open-btn');
    openBtn.disabled = true;
    openBtn.textContent = 'opening…';
    try {
        const poll = await presenterFetch(`/api/battle/${state.currentBattleId}/poll`, 'POST');
        state.poll = { code: poll.code, battleId: state.currentBattleId, timer: null };

        const url = `${location.origin}${poll.join_path}`;
        $('#audience-url').textContent = url.replace(/^https?:\/\//, '');
        $('#audience-code').textContent = poll.code;
        const qrEl = $('#audience-qr');
        qrEl.innerHTML = '';
        if (typeof QRCode !== 'undefined') {
            new QRCode(qrEl, { text: url, width: 160, height: 160, correctLevel: QRCode.CorrectLevel.M });
        } else {
            qrEl.textContent = url;
        }
        renderTally(poll.tally);

        $('#audience-section').classList.add('hidden');
        $('#audience-panel').classList.remove('hidden');
        $('#audience-panel').classList.add('fade-in');
        // Presenter keeps the manual buttons: they can overrule the room.
        state.poll.timer = setInterval(refreshTally, POLL_INTERVAL_MS);
    } catch (err) {
        alert('Could not open the audience poll: ' + err.message);
        openBtn.disabled = false;
        openBtn.textContent = 'let the audience vote (phones)';
    }
}

async function refreshTally() {
    if (!state.poll) return;
    try {
        const poll = await presenterFetch(`/api/battle/${state.poll.battleId}/poll`);
        renderTally(poll.tally);
        if (poll.status !== 'open') {
            $('#audience-live').textContent = poll.status;
            stopPolling();
        }
    } catch (err) {
        $('#audience-live').textContent = 'reconnecting…';
    }
}

export async function closeAudiencePoll() {
    if (!state.poll) return;
    const closeBtn = $('#audience-close-btn');
    closeBtn.disabled = true;
    closeBtn.textContent = 'COUNTING…';
    try {
        const data = await presenterFetch(`/api/battle/${state.poll.battleId}/poll/close`, 'POST');
        stopPolling();
        showReveal(data);
    } catch (err) {
        alert('Could not close the poll: ' + err.message);
        closeBtn.disabled = false;
        closeBtn.textContent = 'CLOSE POLL & REVEAL';
    }
}
