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
    judgeEnabled: false,
    judgeName: 'the judge',
    reasoningEfforts: ['low', 'medium', 'high'],
    poll: null, // { code, battleId, timer } while an audience poll is open
    showView: null, // set by app.js to avoid circular imports
};

// Human label for a reasoning_effort value.
export const effortLabel = (v) => (v ? `thinking: ${v}` : 'thinking: off');
