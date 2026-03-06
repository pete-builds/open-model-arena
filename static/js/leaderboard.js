import { $, state } from './state.js';

export async function loadLeaderboard(category) {
    state.lbCategory = category || 'overall';
    try {
        const resp = await fetch(`/api/leaderboard?category=${state.lbCategory}`);
        if (!resp.ok) return;
        const data = await resp.json();

        const tbody = $('#lb-body');
        const empty = $('#lb-empty');

        if (data.length === 0) {
            tbody.innerHTML = '';
            empty.classList.remove('hidden');
            return;
        }

        empty.classList.add('hidden');
        const maxElo = Math.max(...data.map(d => d.rating), 1600);
        const minElo = Math.min(...data.map(d => d.rating), 1400);

        tbody.innerHTML = data.map((row) => {
            const rankClass = !row.provisional && row.rank <= 3 ? `rank-${row.rank}` : '';
            const provClass = row.provisional ? 'provisional' : '';
            const barWidth = ((row.rating - minElo) / (maxElo - minElo) * 100).toFixed(0);
            const badge = row.provider === 'ollama-mac' ? 'local' : 'gateway';
            const rankDisplay = row.provisional ? '~' : row.rank;

            return `<tr class="${rankClass} ${provClass}">
                <td class="rank-num">${rankDisplay}</td>
                <td>${row.display_name}${row.provisional ? ' <span class="prov-badge">provisional</span>' : ''}</td>
                <td><span class="provider-badge ${badge}">${badge}</span></td>
                <td>${row.rating.toFixed(0)}</td>
                <td><div class="elo-bar"><div class="elo-bar-fill" style="width:${Math.max(barWidth, 5)}%"></div></div></td>
                <td>${row.wins}/${row.losses}/${row.ties}</td>
                <td>${row.win_rate.toFixed(0)}%</td>
                <td>${row.avg_latency_ms ? (row.avg_latency_ms / 1000).toFixed(1) + 's' : '-'}</td>
            </tr>`;
        }).join('');
    } catch (e) { /* silent */ }
}

export function exportData(format) {
    window.location.href = `/api/export?format=${format}`;
}
