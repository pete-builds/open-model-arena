import { $ } from './state.js';

export function initTheme() {
    const saved = localStorage.getItem('arena-theme') || 'dark';
    if (saved === 'light') document.documentElement.setAttribute('data-theme', 'light');
    updateThemeIcon();
}

export function toggleTheme() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    if (isLight) {
        document.documentElement.removeAttribute('data-theme');
        localStorage.setItem('arena-theme', 'dark');
    } else {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('arena-theme', 'light');
    }
    updateThemeIcon();
}

function updateThemeIcon() {
    const btn = $('#theme-toggle');
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    btn.textContent = isLight ? '\u263E' : '\u263C';
}
