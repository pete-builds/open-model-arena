// Audience vote page: one phone, one choice, changeable until the poll closes.
// Public: no passphrase, no cookies. The voter id lives in localStorage so a
// refresh keeps the same vote instead of minting a second one.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const safeMarkdown = (text) => DOMPurify.sanitize(marked.parse(text || ''));

const REFRESH_MS = 3000;
const code = (location.pathname.split('/').pop() || '').toUpperCase();
let closed = false;
let lastRenderedStatus = null;

function voterId() {
    const key = 'arena_voter_id';
    try {
        let id = localStorage.getItem(key);
        if (!id || !/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
            const bytes = new Uint8Array(18);
            crypto.getRandomValues(bytes);
            id = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
            localStorage.setItem(key, id);
        }
        return id;
    } catch (e) {
        // Private mode or storage blocked: a per-page id still dedupes taps.
        if (!window.__arenaVoter) {
            const bytes = new Uint8Array(18);
            crypto.getRandomValues(bytes);
            window.__arenaVoter = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
        }
        return window.__arenaVoter;
    }
}

const VOTER = voterId();

function showError(msg) {
    $('#av-loading').classList.add('hidden');
    $('#av-main').classList.add('hidden');
    const el = $('#av-error');
    el.textContent = msg;
    el.classList.remove('hidden');
}

function renderResponse(id, text) {
    const panel = $(id);
    panel.innerHTML = safeMarkdown(text);
    panel.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
}

function markChoice(choice) {
    $$('.audience-buttons .vote-btn').forEach(b => b.classList.toggle('chosen', b.dataset.choice === choice));
}

function renderClosed(data) {
    closed = true;
    $('#av-vote').classList.add('hidden');
    const result = $('#av-result');
    result.classList.remove('hidden');
    const tally = data.tally || { a: 0, b: 0, tie: 0, total: 0 };
    const total = tally.total || 0;
    ['a', 'tie', 'b'].forEach(k => {
        $(`#av-tally-${k}`).textContent = tally[k] || 0;
        $(`#av-fill-${k}`).style.width = total ? `${Math.round(((tally[k] || 0) / total) * 100)}%` : '0%';
    });
    $('#av-name-a').textContent = data.model_a_name || 'Model A';
    $('#av-name-b').textContent = data.model_b_name || 'Model B';
    const winnerText = data.winner === 'a'
        ? `A wins: ${data.model_a_name}`
        : data.winner === 'b'
            ? `B wins: ${data.model_b_name}`
            : 'It\'s a tie';
    $('#av-winner').textContent = winnerText;
    const pick = data.your_choice;
    $('#av-your-pick').textContent = pick
        ? `you picked ${pick === 'tie' ? 'tie' : pick.toUpperCase()}${pick === data.winner ? ' — with the room' : ''}`
        : 'you did not vote on this one';
}

async function load() {
    let resp;
    try {
        resp = await fetch(`/api/audience/${code}?voter_id=${encodeURIComponent(VOTER)}`, { cache: 'no-store' });
    } catch (e) {
        showError('Cannot reach the arena. Check your connection and reload.');
        return;
    }
    if (resp.status === 404) { showError(`No poll with code ${code}. Check the code on the screen.`); return; }
    if (!resp.ok) { showError('Something went wrong loading this poll.'); return; }
    const data = await resp.json();

    if (lastRenderedStatus === null) {
        $('#av-code').textContent = data.code;
        $('#av-prompt').textContent = data.prompt;
        renderResponse('#av-output-a', data.response_a);
        renderResponse('#av-output-b', data.response_b);
        $('#av-loading').classList.add('hidden');
        $('#av-main').classList.remove('hidden');
        if (data.your_choice) {
            markChoice(data.your_choice);
            $('#av-feedback').textContent = 'your vote is in. tap another button to change it.';
        }
    }
    $('#av-count').textContent = data.vote_count;

    if (data.status === 'closed') {
        renderClosed(data);
    } else if (data.status === 'expired') {
        showError('This poll has expired.');
        closed = true;
    }
    lastRenderedStatus = data.status;
}

async function vote(choice) {
    if (closed) return;
    const buttons = $$('.audience-buttons .vote-btn');
    buttons.forEach(b => b.disabled = true);
    try {
        const resp = await fetch(`/api/audience/${code}/vote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voter_id: VOTER, choice }),
        });
        const body = await resp.json().catch(() => ({}));
        if (resp.status === 409) {
            $('#av-feedback').textContent = 'the poll just closed.';
            await load();
            return;
        }
        if (resp.status === 429) {
            $('#av-feedback').textContent = 'too many taps, give it a second.';
            return;
        }
        if (!resp.ok) throw new Error(body.detail || 'vote failed');
        markChoice(choice);
        $('#av-feedback').textContent = 'your vote is in. tap another button to change it.';
        $('#av-count').textContent = body.vote_count;
    } catch (err) {
        $('#av-feedback').textContent = 'could not record that: ' + err.message;
    } finally {
        buttons.forEach(b => b.disabled = false);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (!/^[A-Z0-9]{6}$/.test(code)) { showError('That is not a valid poll link.'); return; }
    $$('.audience-buttons .vote-btn').forEach(btn => btn.addEventListener('click', () => vote(btn.dataset.choice)));
    load();
    setInterval(() => { if (!closed) load(); }, REFRESH_MS);
});
