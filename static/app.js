/**
 * BLK MRKT — Frontend SPA
 * Module-based vanilla JS architecture.
 */

// ============================================================
// STATE
// ============================================================
const state = {
    user: null,
    token: null,
    refreshToken: null,
    currentView: 'auth',  // auth | feed | detail | dashboard | profile | create
    currentDrop: null,
    drops: [],
    trendingDrops: [],
    scenes: [],
    feedTab: 'live',  // live | trending | scenes
    audio: null,       // HTMLAudioElement
    playing: false,
    playingDropId: null,
};

// ============================================================
// API
// ============================================================
const API = {
    base: '/api',

    async request(path, opts = {}) {
        const headers = { 'Content-Type': 'application/json', ...opts.headers };
        if (state.token) headers['Authorization'] = `Bearer ${state.token}`;

        const res = await fetch(`${this.base}${path}`, { ...opts, headers });

        if (res.status === 401 && state.refreshToken) {
            const refreshed = await this.refreshAuth();
            if (refreshed) {
                headers['Authorization'] = `Bearer ${state.token}`;
                return fetch(`${this.base}${path}`, { ...opts, headers });
            }
        }
        return res;
    },

    async json(path, opts = {}) {
        const res = await this.request(path, opts);
        const data = await res.json();
        if (!res.ok) throw { status: res.status, ...data };
        return data;
    },

    async upload(path, formData) {
        const headers = {};
        if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
        const res = await fetch(`${this.base}${path}`, { method: 'POST', headers, body: formData });
        const data = await res.json();
        if (!res.ok) throw { status: res.status, ...data };
        return data;
    },

    async refreshAuth() {
        try {
            const res = await fetch(`${this.base}/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: state.refreshToken }),
            });
            if (res.ok) {
                const data = await res.json();
                state.token = data.access_token;
                saveAuth();
                return true;
            }
        } catch (e) { /* ignore */ }
        logout();
        return false;
    }
};

// ============================================================
// AUTH PERSISTENCE
// ============================================================
function saveAuth() {
    try {
        const d = { user: state.user, token: state.token, refreshToken: state.refreshToken };
        window.__blkmrkt_auth = d;  // in-memory fallback
    } catch (e) { /* ignore */ }
}

function loadAuth() {
    try {
        const d = window.__blkmrkt_auth;
        if (d && d.token) {
            state.user = d.user;
            state.token = d.token;
            state.refreshToken = d.refreshToken;
            return true;
        }
    } catch (e) { /* ignore */ }
    return false;
}

function logout() {
    state.user = null;
    state.token = null;
    state.refreshToken = null;
    window.__blkmrkt_auth = null;
    navigate('auth');
}

// ============================================================
// ROUTER
// ============================================================
function navigate(view, data) {
    state.currentView = view;
    if (data) Object.assign(state, data);
    render();
}

// ============================================================
// TOAST
// ============================================================
function toast(msg, type = 'info') {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

// ============================================================
// HELPERS
// ============================================================
function formatCountdown(seconds) {
    if (seconds == null) return '';
    if (seconds <= 0) return 'EXPIRED';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function formatTime(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

function velocityEmoji(v) {
    if (v >= 100) return '\u{1F525}\u{1F525}\u{1F525}';
    if (v >= 50) return '\u{1F525}\u{1F525}';
    if (v >= 10) return '\u{1F525}';
    return '';
}

function coverUrl(drop) {
    if (drop.cover_image_path) {
        const fn = drop.cover_image_path.split('/').pop();
        return `/api/covers/${fn}`;
    }
    return null;
}

function audioUrl(dropId) {
    return `/api/audio/${dropId}`;
}

// ============================================================
// COMPONENTS
// ============================================================

function renderHeader() {
    if (state.currentView === 'auth') return '';
    return `
        <header class="app-header">
            <div class="logo">BLK<span>MRKT</span></div>
            <div class="header-actions">
                ${state.user ? `<button class="header-btn" onclick="logout()">OUT</button>` : ''}
            </div>
        </header>
    `;
}

function renderBottomNav() {
    if (state.currentView === 'auth') return '';
    const items = [
        { view: 'feed', icon: '\u{1F50A}', label: 'Drops' },
        ...(state.user?.role === 'artist' ? [{ view: 'dashboard', icon: '\u{1F4CA}', label: 'Dash' }] : []),
        ...(state.user?.role === 'artist' ? [{ view: 'create', icon: '\u{2795}', label: 'Create' }] : []),
        { view: 'profile', icon: '\u{1F464}', label: 'Profile' },
    ];

    return `
        <nav class="bottom-nav">
            ${items.map(i => `
                <button class="nav-item ${state.currentView === i.view ? 'active' : ''}" onclick="navigate('${i.view}')">
                    <span class="icon">${i.icon}</span>
                    <span class="label">${i.label}</span>
                </button>
            `).join('')}
        </nav>
    `;
}

// ---- Auth Screen ----
function renderAuth() {
    return `
        <div class="auth-screen" id="auth-screen">
            <div class="auth-logo">BLK<span>MRKT</span></div>
            <div class="auth-tagline">Drops. Not Streams.</div>
            <div id="auth-error"></div>
            <form class="auth-form" id="auth-form" onsubmit="handleAuth(event)">
                <div id="register-fields" class="hidden">
                    <input type="text" name="username" placeholder="Username" autocomplete="username" style="margin-bottom:14px">
                    <div class="role-selector" style="margin-bottom:14px">
                        <div class="role-option selected" data-role="fan" onclick="selectRole(this)">
                            <div class="icon">\u{1F3A7}</div>
                            <div class="name">Fan</div>
                        </div>
                        <div class="role-option" data-role="artist" onclick="selectRole(this)">
                            <div class="icon">\u{1F3A4}</div>
                            <div class="name">Artist</div>
                        </div>
                    </div>
                    <input type="text" name="city" placeholder="City (optional)" style="margin-bottom:14px">
                </div>
                <input type="email" name="email" placeholder="Email" required autocomplete="email">
                <input type="password" name="password" placeholder="Password" required autocomplete="current-password">
                <button type="submit" class="btn btn-primary" id="auth-submit">SIGN IN</button>
            </form>
            <div class="auth-toggle">
                <span id="auth-toggle-text">No account?</span>
                <a onclick="toggleAuthMode();" id="auth-toggle-link">Register</a>
            </div>
        </div>
    `;
}

// ---- Feed ----
function renderFeed() {
    const tabs = `
        <div class="tab-bar">
            <div class="tab ${state.feedTab === 'live' ? 'active' : ''}" onclick="switchFeedTab('live')">LIVE NOW</div>
            <div class="tab ${state.feedTab === 'trending' ? 'active' : ''}" onclick="switchFeedTab('trending')">TRENDING</div>
            <div class="tab ${state.feedTab === 'scenes' ? 'active' : ''}" onclick="switchFeedTab('scenes')">SCENES</div>
        </div>
    `;

    let content = '<div class="loading"><div class="spinner"></div></div>';

    if (state.feedTab === 'scenes') {
        if (state.scenes.length === 0) {
            content = `
                <div class="empty-state">
                    <div class="icon">\u{1F30D}</div>
                    <div class="title">No scenes yet</div>
                    <div class="subtitle">Scenes are coming soon</div>
                </div>
            `;
        } else {
            content = `<div class="feed">${state.scenes.map(s => `
                <div class="drop-card" onclick="loadSceneDrops('${s.id}')">
                    <div class="drop-info">
                        <div class="drop-title">${esc(s.name)}</div>
                        <div class="drop-artist">${esc(s.city || 'Global')} \u2022 ${s.drop_count || 0} drops</div>
                    </div>
                </div>
            `).join('')}</div>`;
        }
    } else {
        const drops = state.feedTab === 'trending' ? state.trendingDrops : state.drops;
        if (drops.length === 0) {
            content = `
                <div class="empty-state">
                    <div class="icon">\u{1F4BF}</div>
                    <div class="title">No drops yet</div>
                    <div class="subtitle">Check back soon</div>
                </div>
            `;
        } else {
            content = `<div class="feed">${drops.map(renderDropCard).join('')}</div>`;
        }
    }

    return tabs + content;
}

function renderDropCard(d) {
    const cv = coverUrl(d);
    const vel = d.velocity || 0;
    const isHot = vel >= 50;
    const supplyPct = d.supply_pct || 0;
    const remaining = d.remaining_supply;
    const total = d.total_supply;
    const hasSupply = total != null;
    const barClass = supplyPct > 80 ? 'critical' : supplyPct > 60 ? 'low' : '';

    let statusBadge = '';
    if (d.status === 'live') statusBadge = '<div class="drop-badge live">LIVE</div>';
    else if (d.status === 'expired') statusBadge = '<div class="drop-badge expired">ENDED</div>';
    else if (d.status === 'scheduled') statusBadge = '<div class="drop-badge scheduled">SOON</div>';

    return `
        <div class="drop-card ${isHot ? 'hot' : ''}" onclick="openDrop('${d.id}')">
            <div class="cover">
                ${cv ? `<img src="${cv}" alt="">` : `<div class="no-cover">\u{1F3B5}</div>`}
                ${statusBadge}
                ${vel > 0 ? `<div class="velocity-badge">${velocityEmoji(vel)}</div>` : ''}
            </div>
            <div class="drop-info">
                <div class="drop-title">${esc(d.title)}</div>
                <div class="drop-artist">@${esc(d.artist_name || 'unknown')}</div>
                <div class="drop-meta">
                    ${hasSupply ? `
                        <div class="supply-bar-wrap">
                            <div class="supply-bar ${barClass}" style="width:${supplyPct}%"></div>
                        </div>
                        <div class="supply-text">${total - remaining}/${total}</div>
                    ` : '<div class="supply-text">OPEN</div>'}
                </div>
                ${d.countdown_seconds != null && d.countdown_seconds > 0 ? `
                    <div class="countdown ${d.countdown_seconds < 3600 ? 'urgent' : ''}" data-countdown="${d.countdown_seconds}" data-drop-id="${d.id}">
                        ${formatCountdown(d.countdown_seconds)}
                    </div>
                ` : ''}
            </div>
        </div>
    `;
}

// ---- Drop Detail ----
function renderDetail() {
    const d = state.currentDrop;
    if (!d) return '<div class="loading"><div class="spinner"></div></div>';

    const cv = coverUrl(d);
    const eng = d.engagement || {};
    const hasAccess = d.user_has_access;
    const isLive = d.status === 'live';
    const isSoldOut = d.is_sold_out;
    const hasSupply = d.total_supply != null;
    const supplyPct = d.supply_pct || 0;
    const claimed = hasSupply ? d.total_supply - d.remaining_supply : 0;

    let actionBtn = '';
    if (isSoldOut) {
        actionBtn = '<button class="btn btn-sold-out" disabled>SOLD OUT</button>';
    } else if (d.status === 'expired') {
        actionBtn = '<button class="btn btn-sold-out" disabled>EXPIRED</button>';
    } else if (hasAccess) {
        actionBtn = '<button class="btn btn-secondary" disabled>ACCESS GRANTED</button>';
    } else if (isLive) {
        const price = d.access_price > 0 ? `$${d.access_price}` : 'FREE';
        actionBtn = `<button class="btn btn-primary" onclick="claimAccess('${d.id}', 'stream')">CLAIM ACCESS \u2022 ${price}</button>`;
    } else {
        actionBtn = '<button class="btn btn-secondary" disabled>NOT LIVE YET</button>';
    }

    let ownBtn = '';
    if (d.own_price != null && isLive && !isSoldOut) {
        ownBtn = `<button class="btn btn-secondary" onclick="claimAccess('${d.id}', 'own')">OWN THIS \u2022 $${d.own_price}</button>`;
    }

    return `
        <div class="drop-detail">
            <div class="detail-cover">
                ${cv ? `<img src="${cv}" alt="">` : `<div class="no-cover">\u{1F3B5}</div>`}
                <button class="back-btn" onclick="navigate('feed')">\u2190</button>
            </div>
            <div class="detail-body">
                <div class="detail-title">${esc(d.title)}</div>
                <div class="detail-artist" onclick="viewArtist('${d.artist_id}')">@${esc(d.artist_name || 'unknown')}</div>

                ${d.countdown_seconds != null && d.countdown_seconds > 0 ? `
                    <div class="detail-countdown ${d.countdown_seconds < 3600 ? 'urgent' : ''}" data-countdown="${d.countdown_seconds}" data-drop-id="${d.id}">
                        ${formatCountdown(d.countdown_seconds)}
                    </div>
                ` : ''}

                ${hasSupply ? `
                    <div class="detail-supply">
                        <div class="detail-supply-header">
                            <span>${claimed} of ${d.total_supply} claimed</span>
                            <span class="text-mono">${Math.round(supplyPct)}%</span>
                        </div>
                        <div class="detail-supply-bar">
                            <div class="detail-supply-fill" style="width:${supplyPct}%"></div>
                        </div>
                    </div>
                ` : ''}

                ${hasAccess && d.audio_path ? `
                    <div class="audio-player" id="audio-player">
                        <div class="player-controls">
                            <button class="play-btn" id="play-btn" onclick="togglePlay('${d.id}')">
                                ${state.playing && state.playingDropId === d.id ? '\u23F8' : '\u25B6'}
                            </button>
                            <div class="player-progress">
                                <div class="progress-bar" id="progress-bar" onclick="seekAudio(event)">
                                    <div class="progress-fill" id="progress-fill"></div>
                                </div>
                                <div class="player-time">
                                    <span id="current-time">0:00</span>
                                    <span id="total-time">0:00</span>
                                </div>
                            </div>
                        </div>
                    </div>
                ` : !hasAccess && d.audio_path ? `
                    <div class="audio-player">
                        <div class="player-controls">
                            <button class="play-btn" disabled>\u{1F512}</button>
                            <div class="player-progress">
                                <div class="progress-bar"><div class="progress-fill" style="width:0%"></div></div>
                                <div class="player-time"><span>Claim access to listen</span><span></span></div>
                            </div>
                        </div>
                    </div>
                ` : ''}

                <div class="action-buttons">
                    ${actionBtn}
                    ${ownBtn}
                </div>

                <div class="engagement-row">
                    <div class="engagement-stat">
                        <div class="num">${eng.plays || 0}</div>
                        <div class="label">Plays</div>
                    </div>
                    <div class="engagement-stat">
                        <div class="num">${eng.saves || 0}</div>
                        <div class="label">Saves</div>
                    </div>
                    <div class="engagement-stat">
                        <div class="num">${eng.shares || 0}</div>
                        <div class="label">Shares</div>
                    </div>
                    <div class="engagement-stat">
                        <div class="num text-red">${d.velocity || 0}</div>
                        <div class="label">Velocity</div>
                    </div>
                </div>

                ${d.description ? `<p class="text-gray" style="font-size:14px;margin-bottom:16px">${esc(d.description)}</p>` : ''}

                <div class="artist-card" onclick="viewArtist('${d.artist_id}')">
                    <div class="artist-avatar">${(d.artist_name || '?')[0].toUpperCase()}</div>
                    <div class="artist-info">
                        <div class="name">@${esc(d.artist_name || 'unknown')}</div>
                        <div class="city">${esc(d.artist_city || '')}</div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// ---- Dashboard (Artist) ----
function renderDashboard() {
    return `
        <div class="dashboard" id="dashboard">
            <div class="section-header">
                <div class="section-title">YOUR STATS</div>
            </div>
            <div class="dash-stats" id="dash-stats">
                <div class="stat-box"><div class="value">-</div><div class="label">Drops</div></div>
                <div class="stat-box"><div class="value">-</div><div class="label">Plays</div></div>
                <div class="stat-box"><div class="value">-</div><div class="label">Fans</div></div>
            </div>
            <div class="section-header">
                <div class="section-title">YOUR DROPS</div>
            </div>
            <div class="feed" id="dash-drops">
                <div class="loading"><div class="spinner"></div></div>
            </div>
        </div>
    `;
}

// ---- Create Drop ----
function renderCreate() {
    return `
        <div class="section-header" style="padding-top:12px">
            <div class="section-title">CREATE DROP</div>
        </div>
        <form class="create-form" id="create-form" onsubmit="handleCreateDrop(event)">
            <div class="form-group">
                <label>Title</label>
                <input type="text" name="title" placeholder="Drop title" required>
            </div>
            <div class="form-group">
                <label>Description</label>
                <textarea name="description" placeholder="What's this drop about?"></textarea>
            </div>
            <div class="form-group">
                <label>Audio File (MP3/WAV)</label>
                <input type="file" name="audio" accept=".mp3,.wav">
            </div>
            <div class="form-group">
                <label>Cover Image</label>
                <input type="file" name="cover" accept=".png,.jpg,.jpeg,.webp">
            </div>
            <div class="form-group">
                <label>Drop Type</label>
                <select name="drop_type" onchange="toggleDropTypeFields(this.value)">
                    <option value="open">Open (Unlimited)</option>
                    <option value="timed">Timed (Time Window)</option>
                    <option value="limited">Limited (Supply Cap)</option>
                    <option value="rare">Rare (Ultra Limited)</option>
                </select>
            </div>
            <div class="form-row" id="supply-fields" style="display:none">
                <div class="form-group">
                    <label>Total Supply</label>
                    <input type="number" name="total_supply" min="1" placeholder="e.g. 100">
                </div>
                <div class="form-group">
                    <label>Own Price ($)</label>
                    <input type="number" name="own_price" min="0" step="0.01" placeholder="Optional">
                </div>
            </div>
            <div class="form-group">
                <label>Access Price ($)</label>
                <input type="number" name="access_price" min="0" step="0.01" value="0" placeholder="0 = free">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Starts At</label>
                    <input type="datetime-local" name="starts_at">
                </div>
                <div class="form-group" id="expires-field">
                    <label>Expires At</label>
                    <input type="datetime-local" name="expires_at">
                </div>
            </div>
            <button type="submit" class="btn btn-primary" style="margin-top:8px">DROP IT</button>
        </form>
    `;
}

// ---- Profile ----
function renderProfile() {
    if (!state.user) return renderAuth();
    const u = state.user;
    return `
        <div class="profile-header">
            <div class="profile-avatar">${(u.username || '?')[0].toUpperCase()}</div>
            <div class="profile-name">@${esc(u.username)}</div>
            <div class="profile-role">${u.role}</div>
            <div class="profile-city">${esc(u.city || '')}</div>
        </div>
        <div class="section-header">
            <div class="section-title">COLLECTION</div>
        </div>
        <div class="collection-grid" id="collection-grid">
            <div class="loading"><div class="spinner"></div></div>
        </div>
    `;
}

// ============================================================
// RENDER
// ============================================================
function render() {
    const app = document.getElementById('app');
    let content = '';

    switch (state.currentView) {
        case 'auth': content = renderAuth(); break;
        case 'feed': content = renderHeader() + renderFeed() + renderBottomNav(); break;
        case 'detail': content = renderHeader() + renderDetail() + renderBottomNav(); break;
        case 'dashboard': content = renderHeader() + renderDashboard() + renderBottomNav(); break;
        case 'create': content = renderHeader() + renderCreate() + renderBottomNav(); break;
        case 'profile': content = renderHeader() + renderProfile() + renderBottomNav(); break;
        default: content = renderAuth();
    }

    app.innerHTML = content;

    // Post-render hooks
    if (state.currentView === 'feed') loadFeedData();
    if (state.currentView === 'dashboard') loadDashboardData();
    if (state.currentView === 'profile') loadCollection();
    startCountdownTimers();
}

// ============================================================
// EVENT HANDLERS
// ============================================================

// -- Auth --
window._authMode = 'login';

window.toggleAuthMode = function() {
    window._authMode = window._authMode === 'login' ? 'register' : 'login';
    const regFields = document.getElementById('register-fields');
    const submitBtn = document.getElementById('auth-submit');
    const toggleText = document.getElementById('auth-toggle-text');
    const toggleLink = document.getElementById('auth-toggle-link');

    if (window._authMode === 'register') {
        regFields.classList.remove('hidden');
        submitBtn.textContent = 'CREATE ACCOUNT';
        toggleText.textContent = 'Have an account?';
        toggleLink.textContent = 'Sign In';
    } else {
        regFields.classList.add('hidden');
        submitBtn.textContent = 'SIGN IN';
        toggleText.textContent = 'No account?';
        toggleLink.textContent = 'Register';
    }
};

window.selectRole = function(el) {
    document.querySelectorAll('.role-option').forEach(o => o.classList.remove('selected'));
    el.classList.add('selected');
};

window.handleAuth = async function(e) {
    e.preventDefault();
    const form = e.target;
    const email = form.email.value;
    const password = form.password.value;
    const errDiv = document.getElementById('auth-error');

    try {
        if (window._authMode === 'register') {
            const username = form.username.value;
            const role = document.querySelector('.role-option.selected')?.dataset.role || 'fan';
            const city = form.city?.value || '';

            const data = await API.json('/auth/register', {
                method: 'POST',
                body: JSON.stringify({ username, email, password, role, city }),
            });
            state.user = data.user;
            state.token = data.access_token;
            state.refreshToken = data.refresh_token;
        } else {
            const data = await API.json('/auth/login', {
                method: 'POST',
                body: JSON.stringify({ email, password }),
            });
            state.user = data.user;
            state.token = data.access_token;
            state.refreshToken = data.refresh_token;
        }
        saveAuth();
        navigate('feed');
    } catch (err) {
        errDiv.innerHTML = `<div class="auth-error">${esc(err.error || 'Something went wrong')}</div>`;
    }
};

// -- Feed --
window.switchFeedTab = function(tab) {
    state.feedTab = tab;
    render();
};

let _feedLoaded = {};
async function loadFeedData() {
    const tab = state.feedTab;
    if (tab === 'live' && state.drops.length === 0) {
        try {
            const data = await API.json('/drops?status=live&limit=50');
            state.drops = data.drops || [];
            document.querySelector('.feed')?.replaceWith(createEl(renderFeedContent(state.drops)));
        } catch (e) { /* ignore */ }
    }
    if (tab === 'trending' && state.trendingDrops.length === 0) {
        try {
            const data = await API.json('/drops/trending');
            state.trendingDrops = data.drops || [];
            document.querySelector('.feed')?.replaceWith(createEl(renderFeedContent(state.trendingDrops)));
        } catch (e) { /* ignore */ }
    }
    if (tab === 'scenes' && state.scenes.length === 0) {
        try {
            const data = await API.json('/scenes');
            state.scenes = data.scenes || [];
            render();
        } catch (e) { /* ignore */ }
    }
}

function renderFeedContent(drops) {
    if (drops.length === 0) {
        return `<div class="feed"><div class="empty-state"><div class="icon">\u{1F4BF}</div><div class="title">No drops yet</div></div></div>`;
    }
    return `<div class="feed">${drops.map(renderDropCard).join('')}</div>`;
}

function createEl(html) {
    const div = document.createElement('div');
    div.innerHTML = html;
    return div.firstElementChild;
}

// -- Drop Detail --
window.openDrop = async function(dropId) {
    state.currentDrop = null;
    navigate('detail');
    try {
        const data = await API.json(`/drops/${dropId}`);
        state.currentDrop = data.drop;
        render();
    } catch (e) {
        toast(e.error || 'Failed to load drop', 'error');
        navigate('feed');
    }
};

window.claimAccess = async function(dropId, accessType) {
    try {
        await API.json(`/drops/${dropId}/access`, {
            method: 'POST',
            body: JSON.stringify({ access_type: accessType }),
        });
        toast('Access granted!', 'success');
        // Reload drop
        const data = await API.json(`/drops/${dropId}`);
        state.currentDrop = data.drop;
        render();
    } catch (e) {
        toast(e.error || e.message || 'Failed to claim', 'error');
    }
};

// -- Audio --
window.togglePlay = function(dropId) {
    if (!state.audio || state.playingDropId !== dropId) {
        if (state.audio) { state.audio.pause(); state.audio = null; }
        state.audio = new Audio(audioUrl(dropId));
        state.playingDropId = dropId;
        state.audio.addEventListener('timeupdate', updateProgress);
        state.audio.addEventListener('loadedmetadata', () => {
            const totalEl = document.getElementById('total-time');
            if (totalEl) totalEl.textContent = formatTime(state.audio.duration);
        });
        state.audio.addEventListener('ended', () => {
            state.playing = false;
            const btn = document.getElementById('play-btn');
            if (btn) btn.textContent = '\u25B6';
            logEngagement(dropId, 'replay');
        });

        state.audio.play();
        state.playing = true;
        logEngagement(dropId, 'play');
    } else if (state.playing) {
        state.audio.pause();
        state.playing = false;
    } else {
        state.audio.play();
        state.playing = true;
    }

    const btn = document.getElementById('play-btn');
    if (btn) btn.textContent = state.playing ? '\u23F8' : '\u25B6';
};

function updateProgress() {
    if (!state.audio) return;
    const fill = document.getElementById('progress-fill');
    const cur = document.getElementById('current-time');
    if (fill) fill.style.width = `${(state.audio.currentTime / state.audio.duration) * 100}%`;
    if (cur) cur.textContent = formatTime(state.audio.currentTime);
}

window.seekAudio = function(e) {
    if (!state.audio) return;
    const bar = document.getElementById('progress-bar');
    const rect = bar.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    state.audio.currentTime = pct * state.audio.duration;
};

async function logEngagement(dropId, action, metadata = {}) {
    try {
        await API.json(`/drops/${dropId}/engage`, {
            method: 'POST',
            body: JSON.stringify({ action, metadata }),
        });
    } catch (e) { /* silent */ }
}

// -- Dashboard --
async function loadDashboardData() {
    if (!state.user) return;
    try {
        const data = await API.json(`/users/${state.user.id}/profile`);
        const profile = data.profile;
        const statsEl = document.getElementById('dash-stats');
        if (statsEl && profile.stats) {
            statsEl.innerHTML = `
                <div class="stat-box"><div class="value">${profile.stats.total_drops}</div><div class="label">Drops</div></div>
                <div class="stat-box"><div class="value">${profile.stats.total_plays}</div><div class="label">Plays</div></div>
                <div class="stat-box"><div class="value">${profile.stats.total_fans}</div><div class="label">Fans</div></div>
            `;
        }
        const dropsEl = document.getElementById('dash-drops');
        if (dropsEl && profile.drops) {
            if (profile.drops.length === 0) {
                dropsEl.innerHTML = '<div class="empty-state"><div class="icon">\u{1F3A4}</div><div class="title">No drops yet</div><div class="subtitle">Create your first drop</div></div>';
            } else {
                dropsEl.innerHTML = profile.drops.map(d => `
                    <div class="drop-card" onclick="openDrop('${d.id}')">
                        <div class="drop-info">
                            <div class="drop-title">${esc(d.title)}</div>
                            <div class="drop-meta">
                                <span class="drop-badge ${d.status}">${d.status.toUpperCase()}</span>
                                ${d.total_supply ? `<span class="supply-text">${d.total_supply - d.remaining_supply}/${d.total_supply}</span>` : ''}
                            </div>
                        </div>
                    </div>
                `).join('');
            }
        }
    } catch (e) { /* ignore */ }
}

// -- Create Drop --
window.toggleDropTypeFields = function(type) {
    const supplyFields = document.getElementById('supply-fields');
    if (type === 'limited' || type === 'rare' || type === 'tiered') {
        supplyFields.style.display = 'grid';
    } else {
        supplyFields.style.display = 'none';
    }
};

window.handleCreateDrop = async function(e) {
    e.preventDefault();
    const form = e.target;
    const fd = new FormData(form);

    // Convert datetime-local to ISO
    const startsAt = form.starts_at.value;
    const expiresAt = form.expires_at.value;
    if (startsAt) fd.set('starts_at', new Date(startsAt).toISOString().replace('.000Z', 'Z'));
    if (expiresAt) fd.set('expires_at', new Date(expiresAt).toISOString().replace('.000Z', 'Z'));
    if (!startsAt) fd.delete('starts_at');
    if (!expiresAt) fd.delete('expires_at');

    // Clean up empty numeric fields
    if (!form.total_supply?.value) fd.delete('total_supply');
    if (!form.own_price?.value) fd.delete('own_price');

    try {
        const data = await API.upload('/drops', fd);
        toast('Drop created!', 'success');
        state.drops = [];  // Force refresh
        navigate('feed');
    } catch (e) {
        toast(e.error || 'Failed to create drop', 'error');
    }
};

// -- Profile / Collection --
async function loadCollection() {
    if (!state.user) return;
    try {
        const data = await API.json(`/users/${state.user.id}/collection`);
        const grid = document.getElementById('collection-grid');
        if (!grid) return;

        if (data.collection.length === 0) {
            grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">\u{1F4E6}</div><div class="title">Empty collection</div><div class="subtitle">Claim some drops to build your collection</div></div>';
        } else {
            grid.innerHTML = data.collection.map(item => {
                const cv = item.cover_image_path ? `/api/covers/${item.cover_image_path.split('/').pop()}` : null;
                return `
                    <div class="collection-item" onclick="openDrop('${item.drop_id}')">
                        <div class="thumb">${cv ? `<img src="${cv}" alt="">` : '\u{1F3B5}'}</div>
                        <div class="info">
                            <div class="title">${esc(item.title)}</div>
                            <div class="artist">@${esc(item.artist_name)}</div>
                        </div>
                    </div>
                `;
            }).join('');
        }
    } catch (e) { /* ignore */ }
}

window.viewArtist = function(userId) {
    // For now just show profile — in future, navigate to artist public page
    toast('Artist profiles coming soon');
};

// ============================================================
// COUNTDOWN TIMERS
// ============================================================
let _countdownInterval = null;

function startCountdownTimers() {
    if (_countdownInterval) clearInterval(_countdownInterval);
    _countdownInterval = setInterval(() => {
        document.querySelectorAll('[data-countdown]').forEach(el => {
            let sec = parseInt(el.dataset.countdown);
            if (sec > 0) {
                sec--;
                el.dataset.countdown = sec;
                el.textContent = formatCountdown(sec);
                if (sec < 3600) el.classList.add('urgent');
                if (sec <= 0) {
                    el.textContent = 'EXPIRED';
                    // Refresh feed data
                    state.drops = [];
                    state.trendingDrops = [];
                }
            }
        });
    }, 1000);
}

// ============================================================
// UTIL
// ============================================================
function esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

// ============================================================
// EXPOSE GLOBALS
// ============================================================
window.navigate = navigate;
window.logout = logout;
window.state = state;

// ============================================================
// INIT
// ============================================================
if (loadAuth()) {
    navigate('feed');
} else {
    render();
}
