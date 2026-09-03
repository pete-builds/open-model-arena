import { $, $$, safeMarkdown, getCsrfToken, state, effortLabel } from './state.js';
import { resetAudience, showAudienceControls } from './audience.js';

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
                reasoning_effort: $('#reasoning-select').value || null,
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
    $('#judge-section').classList.add('hidden');
    resetAudience();
    ['a', 'b'].forEach(side => {
        const block = $(`#thinking-${side}`);
        block.classList.add('hidden');
        block.open = false;
        $(`#thinking-${side}-body`).textContent = '';
        $(`#thinking-${side}-meta`).textContent = '';
        $(`#output-${side}`).style.color = '';
    });
    const judgeBtn = $('#judge-btn');
    if (judgeBtn) {
        judgeBtn.disabled = false;
        judgeBtn.innerHTML = 'let <span id="judge-model-name">' + (state.judgeName || 'the judge') + '</span> decide';
    }

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

    // Thinking traces stream separately from the answer, into a collapsed block above it.
    ['a', 'b'].forEach(side => {
        source.addEventListener(`model_${side}_thinking`, (e) => {
            const data = JSON.parse(e.data);
            const block = $(`#thinking-${side}`);
            const body = $(`#thinking-${side}-body`);
            body.textContent += data.token;
            if (block.classList.contains('hidden')) {
                block.classList.remove('hidden');
                block.open = true;
            }
            body.scrollTop = body.scrollHeight;
        });
        source.addEventListener(`model_${side}_notice`, (e) => {
            const data = JSON.parse(e.data);
            if (data.notice === 'reasoning_unsupported') {
                $(`#status-${side}`).textContent = 'thinking not supported here, answering plainly';
            }
        });
    });

    source.addEventListener('model_a_done', (e) => {
        doneA = true;
        state.battleMeta.a = JSON.parse(e.data);
        // A replayed battle carries the full text on the done event instead of tokens.
        if (!state.responseA && state.battleMeta.a.response) {
            state.responseA = state.battleMeta.a.response;
            renderPanel('output-a', state.responseA, cursorA);
        }
        $('#status-a').textContent = '';
        if (cursorA.parentNode) cursorA.remove();
        $('#footer-a').textContent = footerText(state.battleMeta.a);
        finishThinking('a', state.battleMeta.a);
        if (doneA && doneB) showVoteButtons();
    });

    source.addEventListener('model_b_done', (e) => {
        doneB = true;
        state.battleMeta.b = JSON.parse(e.data);
        if (!state.responseB && state.battleMeta.b.response) {
            state.responseB = state.battleMeta.b.response;
            renderPanel('output-b', state.responseB, cursorB);
        }
        $('#status-b').textContent = '';
        if (cursorB.parentNode) cursorB.remove();
        $('#footer-b').textContent = footerText(state.battleMeta.b);
        finishThinking('b', state.battleMeta.b);
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

function footerText(meta) {
    let text = `${(meta.latency_ms / 1000).toFixed(1)}s / ${meta.tokens} tokens`;
    if (meta.reasoning_effort) {
        text += ` / ${effortLabel(meta.reasoning_effort)}`;
        if (meta.reasoning_tokens) text += ` (${meta.reasoning_tokens} reasoning tokens)`;
    }
    return text;
}

function finishThinking(side, meta) {
    const block = $(`#thinking-${side}`);
    if (block.classList.contains('hidden')) return;
    const chars = $(`#thinking-${side}-body`).textContent.length;
    $(`#thinking-${side}-meta`).textContent = `(${chars.toLocaleString()} chars${meta.reasoning_tokens ? `, ${meta.reasoning_tokens} tokens` : ''})`;
    block.open = false; // collapse once the answer is in, so the answer gets the space
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

    // Judge button only shown when both responses are usable AND server has judge configured
    const bothPresent = state.responseA.trim() && state.responseB.trim();
    if (state.judgeEnabled && bothPresent) {
        $('#judge-section').classList.remove('hidden');
    }
    // Audience vote needs both answers on screen, same as the judge.
    if (bothPresent) showAudienceControls();
}

export async function requestJudgeVote() {
    if (!state.currentBattleId) return;
    const btn = $('#judge-btn');
    const origLabel = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'judging…';
    $$('.vote-btn').forEach(b => b.disabled = true);
    try {
        const resp = await fetch(`/api/battle/${state.currentBattleId}/judge`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
        });
        if (resp.status === 401 || resp.status === 403) {
            window.location.href = '/login';
            return;
        }
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: 'judge failed' }));
            throw new Error(err.detail || 'judge failed');
        }
        const data = await resp.json();
        showReveal(data);
    } catch (err) {
        alert('Judge error: ' + err.message);
        $$('.vote-btn').forEach(b => b.disabled = false);
        btn.disabled = false;
        btn.innerHTML = origLabel;
    }
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

export async function loadPermalink(battleId) {
    // Rehydrate the reveal view from a shared /battle/<id> URL, when the user
    // did not just come from voting themselves.
    try {
        const resp = await fetch(`/api/battle/${battleId}`);
        if (resp.status === 401 || resp.status === 403) {
            window.location.href = '/login';
            return false;
        }
        if (!resp.ok) return false;

        const data = await resp.json();
        state.currentBattleId = data.id;
        state.responseA = data.response_a;
        state.responseB = data.response_b;
        $('#battle-prompt').textContent = data.prompt;
        showReveal(data);
        return true;
    } catch (err) {
        return false;
    }
}

export function showReveal(data) {
    resetAudience();
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

    const effort = data.reasoning_effort ? ` / ${effortLabel(data.reasoning_effort)}` : '';
    $('#reveal-meta-a').innerHTML = `<span class="provider-badge ${badgeA}">${badgeA}</span> / ${(data.latency_a_ms / 1000).toFixed(1)}s / ${data.tokens_a} tok / <span class="cost">${costA}</span>${effort}`;
    $('#reveal-meta-b').innerHTML = `<span class="provider-badge ${badgeB}">${badgeB}</span> / ${(data.latency_b_ms / 1000).toFixed(1)}s / ${data.tokens_b} tok / <span class="cost">${costB}</span>${effort}`;

    const eloChangeA = data.rating_a_after - data.rating_a_before;
    const eloChangeB = data.rating_b_after - data.rating_b_before;

    const eloClass = (v) => v > 0 ? 'positive' : v < 0 ? 'negative' : 'neutral';
    const eloSign = (v) => v > 0 ? '+' : '';

    // ELO section is only present when the vote_log has a row — omit gracefully otherwise.
    if (data.rating_a_after != null && data.rating_b_after != null) {
        $('#reveal-footer-a').innerHTML = `ELO: ${data.rating_a_after.toFixed(0)} <span class="elo-change ${eloClass(eloChangeA)}">(${eloSign(eloChangeA)}${eloChangeA.toFixed(0)})</span>`;
        $('#reveal-footer-b').innerHTML = `ELO: ${data.rating_b_after.toFixed(0)} <span class="elo-change ${eloClass(eloChangeB)}">(${eloSign(eloChangeB)}${eloChangeB.toFixed(0)})</span>`;
    } else {
        $('#reveal-footer-a').textContent = '';
        $('#reveal-footer-b').textContent = '';
    }

    // Judge verdict — visible only when a judge cast this vote.
    const verdictBlock = $('#judge-verdict');
    if (data.vote_method === 'judge' && data.judge_reasoning) {
        $('#verdict-judge-name').textContent = data.judge_display_name || data.judge_model_id || 'the judge';
        $('#verdict-reasoning').textContent = data.judge_reasoning;
        verdictBlock.classList.remove('hidden');
    } else {
        verdictBlock.classList.add('hidden');
    }

    // Audience verdict — visible when a poll decided this battle.
    const audienceBlock = $('#audience-verdict');
    const tally = data.audience_tally;
    if (data.vote_method === 'audience' && tally) {
        const pick = data.winner === 'a' ? 'A wins' : data.winner === 'b' ? 'B wins' : 'tie';
        $('#audience-verdict-body').textContent =
            `${tally.total} vote${tally.total === 1 ? '' : 's'}: A ${tally.a} / tie ${tally.tie} / B ${tally.b}. Result: ${pick}.`;
        audienceBlock.classList.remove('hidden');
    } else {
        audienceBlock.classList.add('hidden');
    }

    // Put the permalink in the address bar so the user can copy the URL directly.
    const battleId = data.id || state.currentBattleId;
    if (battleId && location.pathname !== `/battle/${battleId}`) {
        history.replaceState(null, '', `/battle/${battleId}`);
    }

    state.showView('reveal');
}
