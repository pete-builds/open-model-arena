import { $, $$, state } from './state.js';

function getTemplates() {
    return JSON.parse(localStorage.getItem('arena-templates') || '[]');
}

function saveTemplates(templates) {
    localStorage.setItem('arena-templates', JSON.stringify(templates));
}

function renderTemplateDropdown() {
    const select = $('#template-select');
    const bar = $('#template-bar');
    const templates = getTemplates();
    select.innerHTML = '<option value="">load template...</option>';
    templates.forEach((t, i) => {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = `${t.name} [${t.category}]`;
        select.appendChild(opt);
    });
    bar.classList.toggle('visible', templates.length > 0);
}

export function setupTemplates() {
    renderTemplateDropdown();

    $('#template-select').addEventListener('change', (e) => {
        const idx = e.target.value;
        if (idx === '') return;
        const templates = getTemplates();
        const t = templates[idx];
        if (!t) return;
        $('#prompt').value = t.prompt;
        $$('#category-group .btn-option').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.value === t.category);
        });
        state.selectedCategory = t.category;
    });

    $('#save-template-btn').addEventListener('click', () => {
        const prompt = $('#prompt').value.trim();
        if (!prompt) return;
        const name = window.prompt('Template name:');
        if (!name) return;
        const templates = getTemplates();
        templates.push({ name, prompt, category: state.selectedCategory });
        saveTemplates(templates);
        renderTemplateDropdown();
    });

    $('#delete-template-btn').addEventListener('click', () => {
        const select = $('#template-select');
        const idx = select.value;
        if (idx === '') return;
        const templates = getTemplates();
        templates.splice(parseInt(idx), 1);
        saveTemplates(templates);
        renderTemplateDropdown();
    });
}
