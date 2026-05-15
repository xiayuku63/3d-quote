// ── Shared state ──
export let authToken = '';
export let currentUser = null;
export const quoteOptions = {
    material: 'PLA',
    color: 'White',
    infill: 20,
    layerHeight: 0.2,
    wallCount: 3,
    quantity: 1,
    slicer_preset_id: null,
};
export const selectedFilesMap = new Map();
export const currentResults = [];
export let MATERIAL_OPTIONS = [];
export let PRICING_CONFIG = null;

// ── Utilities ──
export function escapeHtml(value) {
    if (!value) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export function formatTimeHMS(hours) {
    if (!hours || hours <= 0) return '-';
    const totalSeconds = Math.round(hours * 3600);
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = (totalSeconds % 60).toString().padStart(2, '0');
    if (h > 0) return `${h}h ${m}m ${s}s`;
    return `${m}m ${s}s`;
}

export function formatColorLabel(colorKey) {
    if (!colorKey) return '';
    const key = String(colorKey).toLowerCase();
    const map = {
        white: '⚪', black: '⚫', gray: '⚙️', grey: '⚙️',
        red: '🔴', blue: '🔵', green: '🟢', yellow: '🟡',
        orange: '🟠', purple: '🟣',
    };
    const icon = map[key] || '🎨';
    return `${icon} ${colorKey}`;
}

export function normalizeColorToken(token) {
    const lookup = {
        '⚪': 'white', '⚫': 'black', '⚙️': 'gray', '🔴': 'red',
        '🔵': 'blue', '🟢': 'green', '🟡': 'yellow', '🟠': 'orange',
        '🟣': 'purple', '🎨': token,
    };
    return lookup[token] || token;
}

export async function authFetch(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    return fetch(url, { ...options, headers });
}

export function loadUserSession() {
    try {
        const raw = localStorage.getItem('pricer3d_session');
        if (raw) {
            const data = JSON.parse(raw);
            if (data.token && data.user) {
                authToken = data.token;
                currentUser = data.user;
            }
        }
    } catch (e) {}
}

export function saveUserSession() {
    try {
        if (!authToken || !currentUser) {
            localStorage.removeItem('pricer3d_session');
        } else {
            localStorage.setItem('pricer3d_session', JSON.stringify({ token: authToken, user: currentUser }));
        }
    } catch (e) {}
}

export function clearUserSession() {
    authToken = '';
    currentUser = null;
    try { localStorage.removeItem('pricer3d_session'); } catch (e) {}
}
