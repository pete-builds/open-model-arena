// Shared state and DOM helpers

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => document.querySelectorAll(sel);
export const safeMarkdown = (text) => DOMPurify.sanitize(marked.parse(text));

export function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)arena_csrf=([^;]*)/);
    return match ? match[1] : '';
}

export const state = {
    selectedCategory: 'general',
    currentBattleId: null,
    responseA: '',
    responseB: '',
    battleMeta: { a: {}, b: {} },
    allModels: [],
    lbCategory: 'overall',
    showView: null, // set by app.js to avoid circular imports
};
