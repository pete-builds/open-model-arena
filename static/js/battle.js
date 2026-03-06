import { $, $$, safeMarkdown, getCsrfToken, state } from './state.js';

export async function startBattle() {
    const prompt = $('#prompt').value.trim();
    if (!prompt) return;

    const btn = $('#battle-btn');
    btn.disabled = true;
    btn.querySelector('.btn-text').textContent = 'MATCHING...';

    try {
        const resp = await fetch('/api/battle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
            body: JSON.stringify({
                prompt,
                category: state.selectedCategory,
                model_a: $('#select-model-a').value || null,
                model_b: $('#select-model-b').value || null,
            })
        });

        if (resp.status === 403 || resp.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'failed to create battle');
        }

        const { battle_id } = await resp.json();
        state.currentBattleId = battle_id;
        state.responseA = '';
        state.responseB = '';
        state.battleMeta = { a: {}, b: {} };

        $('#battle-prompt').textContent = prompt;
        state.showView('battle');
        streamBattle(battle_id);
    } catch (err) {
        alert('Error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.querySelector('.btn-text').textContent = 'BATTLE';
    }
}

function streamBattle(battleId) {
    $('#output-a').textContent = '';
    $('#output-b').textContent = '';
    $('#footer-a').textContent = '';
    $('#footer-b').textContent = '';
    $('#status-a').textContent = 'streaming...';
    $('#status-b').textContent = 'streaming...';
    $('#vote-section').classList.add('hidden');
    $('#skip-section').classList.add('hidden');

    const cursorA = document.createElement('span');
    cursorA.className = 'typing-cursor';
    $('#output-a').appendChild(cursorA);
    const cursorB = document.createElement('span');
    cursorB.className = 'typing-cursor';
    $('#output-b').appendChild(cursorB);

    const source = new EventSource(`/api/battle/${battleId}/stream`);
    let doneA = false, doneB = false;

    source.addEventListener('model_a', (e) => {
        const data = JSON.parse(e.data);
        state.responseA += data.token;
        renderPanel('output-a', state.responseA, cursorA);
    });

    source.addEventListener('model_b', (e) => {
        const data = JSON.parse(e.data);
        state.responseB += data.token;
        renderPanel('output-b', state.responseB, cursorB);
    });

    source.addEventListener('model_a_done', (e) => {
        doneA = true;
        state.battleMeta.a = JSON.parse(e.data);
        $('#status-a').textContent = '';
        if (cursorA.parentNode) cursorA.remove();
        $('#footer-a').textContent = `${(state.battleMeta.a.latency_ms / 1000).toFixed(1)}s / ${state.battleMeta.a.tokens} tokens`;
        if (doneA && doneB) showVoteButtons();
    });

    source.addEventListener('model_b_done', (e) => {
        doneB = true;
        state.battleMeta.b = JSON.parse(e.data);
        $('#status-b').textContent = '';
        if (cursorB.parentNode) cursorB.remove();
        $('#footer-b').textContent = `${(state.battleMeta.b.latency_ms / 1000).toFixed(1)}s / ${state.battleMeta.b.tokens} tokens`;
        if (doneA && doneB) showVoteButtons();
    });

    source.addEventListener('model_a_error', (e) => {
        doneA = true;
        const data = JSON.parse(e.data);
        $('#status-a').textContent = '';
        if (cursorA.parentNode) cursorA.remove();
        $('#output-a').textContent = `Error: ${data.error}`;
        $('#output-a').style.color = 'var(--danger)';
        if (doneA && doneB) showVoteButtons();
    });

    source.addEventListener('model_b_error', (e) => {
        doneB = true;
        const data = JSON.parse(e.data);
        $('#status-b').textContent = '';
        if (cursorB.parentNode) cursorB.remove();
        $('#output-b').textContent = `Error: ${data.error}`;
        $('#output-b').style.color = 'var(--danger)';
        if (doneA && doneB) showVoteButtons();
    });

    source.addEventListener('battle_complete', () => {
        source.close();
    });

    source.addEventListener('error', () => {
        source.close();
        if (!doneA) { $('#status-a').textContent = 'disconnected'; if (cursorA.parentNode) cursorA.remove(); }
        if (!doneB) { $('#status-b').textContent = 'disconnected'; if (cursorB.parentNode) cursorB.remove(); }
        if (doneA || doneB) showVoteButtons();
    });
}

function renderPanel(panelId, text, cursor) {
    const panel = $(`#${panelId}`);
    const rendered = safeMarkdown(text);
    panel.innerHTML = rendered;
    panel.appendChild(cursor);
    panel.querySelectorAll('pre code').forEach(block => {
        if (!block.dataset.highlighted) {
            hljs.highlightElement(block);
            block.dataset.highlighted = 'true';
        }
    });
    panel.scrollTop = panel.scrollHeight;
}

function showVoteButtons() {
    $$('.vote-btn').forEach(b => b.disabled = false);

    if (state.responseA.trim() === state.responseB.trim()) {
        $$('.vote-btn.vote-a, .vote-btn.vote-b').forEach(b => b.disabled = true);
    }

    $('#vote-section').classList.remove('hidden');
    $('#vote-section').classList.add('fade-in');
    $('#skip-section').classList.remove('hidden');
}

export async function submitVote(winner) {
    $$('.vote-btn').forEach(b => b.disabled = true);

    try {
        const resp = await fetch(`/api/battle/${state.currentBattleId}/vote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
            body: JSON.stringify({ winner })
        });

        if (resp.status === 403 || resp.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'vote failed');
        }

        const data = await resp.json();
        showReveal(data);
    } catch (err) {
        alert('Error: ' + err.message);
        $$('.vote-btn').forEach(b => b.disabled = false);
    }
}

function showReveal(data) {
    $('#reveal-prompt').textContent = $('#battle-prompt').textContent;

    $('#reveal-output-a').innerHTML = safeMarkdown(state.responseA);
    $('#reveal-output-b').innerHTML = safeMarkdown(state.responseB);

    $$('#reveal-output-a pre code, #reveal-output-b pre code').forEach(block => hljs.highlightElement(block));

    $('#reveal-name-a').textContent = data.model_a_name;
    $('#reveal-name-b').textContent = data.model_b_name;

    const badgeA = data.model_a_provider === 'ollama-mac' ? 'local' : 'gateway';
    const badgeB = data.model_b_provider === 'ollama-mac' ? 'local' : 'gateway';

    const costA = data.cost_a > 0 ? `$${data.cost_a.toFixed(4)}` : 'free';
    const costB = data.cost_b > 0 ? `$${data.cost_b.toFixed(4)}` : 'free';

    $('#reveal-meta-a').innerHTML = `<span class="provider-badge ${badgeA}">${badgeA}</span> / ${(data.latency_a_ms / 1000).toFixed(1)}s / ${data.tokens_a} tok / <span class="cost">${costA}</span>`;
    $('#reveal-meta-b').innerHTML = `<span class="provider-badge ${badgeB}">${badgeB}</span> / ${(data.latency_b_ms / 1000).toFixed(1)}s / ${data.tokens_b} tok / <span class="cost">${costB}</span>`;

    const eloChangeA = data.rating_a_after - data.rating_a_before;
    const eloChangeB = data.rating_b_after - data.rating_b_before;

    const eloClass = (v) => v > 0 ? 'positive' : v < 0 ? 'negative' : 'neutral';
    const eloSign = (v) => v > 0 ? '+' : '';

    $('#reveal-footer-a').innerHTML = `ELO: ${data.rating_a_after.toFixed(0)} <span class="elo-change ${eloClass(eloChangeA)}">(${eloSign(eloChangeA)}${eloChangeA.toFixed(0)})</span>`;
    $('#reveal-footer-b').innerHTML = `ELO: ${data.rating_b_after.toFixed(0)} <span class="elo-change ${eloClass(eloChangeB)}">(${eloSign(eloChangeB)}${eloChangeB.toFixed(0)})</span>`;

    state.showView('reveal');
}
