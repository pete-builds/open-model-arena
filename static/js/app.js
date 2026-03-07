// Open Model Arena — Main Entry Point

import { $, $$, state } from './state.js';
import { initTheme, toggleTheme } from './theme.js';
import { setupTemplates } from './templates.js';
import { loadLeaderboard, exportData } from './leaderboard.js';
import { startBattle, submitVote } from './battle.js';

// --- Stats ---

async function loadStats() {
    try {
        const resp = await fetch('/api/stats');
        if (!resp.ok) return;
        const data = await resp.json();
        $('#stat-battles').textContent = data.total_battles;
        $('#stat-voted').textContent = data.total_voted;
        $('#stat-today').textContent = data.battles_today;
    } catch (e) { /* silent */ }
}

// --- Model Selectors ---

async function loadModels() {
    try {
        const resp = await fetch('/api/models');
        if (!resp.ok) return;
        state.allModels = await resp.json();
        populateModelSelects();
    } catch (e) { /* silent */ }
}

function populateModelSelects() {
    const filtered = state.allModels.filter(m => m.categories.includes(state.selectedCategory));
    ['select-model-a', 'select-model-b'].forEach(id => {
        const select = $(`#${id}`);
        const current = select.value;
        select.innerHTML = '<option value="">mystery match</option>';
        filtered.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.display_name;
            select.appendChild(opt);
        });
        if ([...select.options].some(o => o.value === current)) {
            select.value = current;
        }
    });
}

// --- View Management ---

function showView(name) {
    $$('#arena-view, #battle-view, #reveal-view, #leaderboard-view').forEach(el => el.classList.add('hidden'));
    $$('.top-nav a').forEach(a => a.classList.remove('active'));

    const newBattleLink = $('#nav-new-battle');
    if (name === 'arena') {
        newBattleLink.classList.add('hidden');
    } else {
        newBattleLink.classList.remove('hidden');
    }

    if (name === 'arena') {
        $('#arena-view').classList.remove('hidden');
        $('#arena-view').classList.add('arena-centered');
        $('#nav-arena').classList.add('active');
        loadStats();
    } else if (name === 'battle') {
        $('#battle-view').classList.remove('hidden');
        $('#battle-view').classList.add('fade-in');
        $('#nav-arena').classList.add('active');
    } else if (name === 'reveal') {
        $('#reveal-view').classList.remove('hidden');
        $('#reveal-view').classList.add('fade-in');
        $('#nav-arena').classList.add('active');
    } else if (name === 'leaderboard') {
        $('#leaderboard-view').classList.remove('hidden');
        $('#nav-leaderboard').classList.add('active');
        loadLeaderboard(state.lbCategory);
    }
}

// Register showView on state so other modules can call it
state.showView = showView;

// --- Nav ---

function setupNav() {
    $$('.top-nav a').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const href = link.getAttribute('href');
            history.pushState(null, '', href);
            route();
        });
    });
}

function setupButtonGroup(groupId, callback) {
    $$(`#${groupId} .btn-option`).forEach(btn => {
        btn.addEventListener('click', () => {
            $$(`#${groupId} .btn-option`).forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            callback(btn.dataset.value);
        });
    });
}

// --- Router ---

function route() {
    const path = location.pathname;
    if (path === '/leaderboard') {
        showView('leaderboard');
    } else {
        showView('arena');
    }
}

// --- Init ---

document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    setupNav();
    setupButtonGroup('category-group', (val) => { state.selectedCategory = val; populateModelSelects(); $('#category-mobile').value = val; });
    setupButtonGroup('lb-category-group', (val) => { loadLeaderboard(val); $('#lb-category-mobile').value = val; });

    // Mobile category dropdowns
    $('#category-mobile').addEventListener('change', (e) => {
        state.selectedCategory = e.target.value;
        populateModelSelects();
        $$('#category-group .btn-option').forEach(b => b.classList.toggle('active', b.dataset.value === e.target.value));
    });
    $('#lb-category-mobile').addEventListener('change', (e) => {
        loadLeaderboard(e.target.value);
        $$('#lb-category-group .btn-option').forEach(b => b.classList.toggle('active', b.dataset.value === e.target.value));
    });
    setupTemplates();

    const battleBtn = $('#battle-btn');
    if (battleBtn) battleBtn.addEventListener('click', startBattle);
    $('#nav-new-battle').addEventListener('click', (e) => {
        e.preventDefault();
        history.pushState(null, '', '/');
        showView('arena');
    });

    // Vote buttons
    $$('.vote-btn').forEach(btn => {
        btn.addEventListener('click', () => submitVote(btn.dataset.vote));
    });

    // Theme toggle
    $('#theme-toggle').addEventListener('click', toggleTheme);

    // Skip button
    $('#skip-btn').addEventListener('click', () => {
        history.pushState(null, '', '/');
        showView('arena');
    });

    // Export buttons
    $('#export-csv').addEventListener('click', () => exportData('csv'));
    $('#export-json').addEventListener('click', () => exportData('json'));

    // Ctrl/Cmd+Enter to submit
    $('#prompt').addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            startBattle();
        }
    });

    // Handle browser back/forward
    window.addEventListener('popstate', route);

    loadModels();
    route();
});
