/**
 * BLK MRKT — Role-Based SPA
 * Fan / Artist / Label / Admin — each with their own experience.
 * Responsive: desktop sidebar + mobile bottom nav.
 */

// ============================================================
// STATE
// ============================================================
const state = {
  user: null,
  token: null,
  refreshToken: null,
  view: 'auth',         // current view name
  viewData: {},         // arbitrary data for current view
  drops: [],
  trendingDrops: [],
  scenes: [],
  feedTab: 'live',
  audio: null,
  playing: false,
  playingDropId: null,
  label: null,          // label user's label object (if role=label)
};

// ============================================================
// API
// ============================================================
const API = {
  base: '/api',

  async request(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...opts.headers };
    if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
    let res = await fetch(`${this.base}${path}`, { ...opts, headers });
    if (res.status === 401 && state.refreshToken) {
      const ok = await this.refreshAuth();
      if (ok) {
        headers['Authorization'] = `Bearer ${state.token}`;
        res = await fetch(`${this.base}${path}`, { ...opts, headers });
      }
    }
    return res;
  },

  async json(path, opts = {}) {
    const res = await this.request(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw { status: res.status, ...data };
    return data;
  },

  async upload(path, formData) {
    const headers = {};
    if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
    const res = await fetch(`${this.base}${path}`, { method: 'POST', headers, body: formData });
    const data = await res.json().catch(() => ({}));
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
    } catch (e) {}
    logout();
    return false;
  },
};

// ============================================================
// AUTH PERSISTENCE (in-memory, no localStorage)
// ============================================================
function saveAuth() {
  window.__blkmrkt = { user: state.user, token: state.token, refreshToken: state.refreshToken };
}
function loadAuth() {
  const d = window.__blkmrkt;
  if (d?.token) {
    state.user = d.user;
    state.token = d.token;
    state.refreshToken = d.refreshToken;
    return true;
  }
  return false;
}
function logout() {
  state.user = null; state.token = null; state.refreshToken = null;
  state.label = null;
  window.__blkmrkt = null;
  nav('auth');
}

// ============================================================
// ROUTER
// ============================================================
function nav(view, data = {}) {
  state.view = view;
  state.viewData = data;
  render();
}

// ============================================================
// UTILS
// ============================================================
function esc(str) {
  if (str == null) return '';
  const d = document.createElement('div');
  d.textContent = String(str);
  return d.innerHTML;
}
function fmt(n) { return (n == null) ? '-' : Number(n).toLocaleString(); }
function fmtMoney(n) { return n == null ? '-' : `$${Number(n).toFixed(2)}`; }
function fmtCountdown(s) {
  if (s == null) return '';
  if (s <= 0) return 'EXPIRED';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return `${pad(h)}:${pad(m)}:${pad(sec)}`;
}
function pad(n) { return String(n).padStart(2, '0'); }
function fmtTime(s) { const m = Math.floor(s / 60); return `${m}:${pad(Math.floor(s % 60))}`; }
function velEmoji(v) {
  if (v >= 100) return '🔥🔥🔥'; if (v >= 50) return '🔥🔥'; if (v >= 10) return '🔥'; return '';
}
function coverUrl(d) {
  if (!d?.cover_image_path) return null;
  return `/api/covers/${d.cover_image_path.split('/').pop()}`;
}
function toast(msg, type = 'info') {
  document.querySelector('.toast')?.remove();
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}
function roleBadge(role) {
  const map = { fan: 'badge-blue', artist: 'badge-red', label: 'badge-orange', admin: 'badge-gray', curator: 'badge-green' };
  return `<span class="badge ${map[role] || 'badge-gray'}">${role}</span>`;
}
function statusBadge(s) {
  const map = { live: 'badge-red', scheduled: 'badge-gray', expired: 'badge-gray', locked: 'badge-gray' };
  return `<span class="badge ${map[s] || 'badge-gray'}">${s}</span>`;
}

// ============================================================
// NAV CONFIG PER ROLE
// ============================================================
function navItems() {
  const role = state.user?.role;
  const all = [
    { v: 'feed',             icon: '🔊', label: 'Drops' },
  ];
  if (role === 'fan') all.push(
    { v: 'collection',       icon: '📦', label: 'Collection' },
    { v: 'fan-profile',      icon: '👤', label: 'Profile' },
  );
  if (role === 'artist') all.push(
    { v: 'artist-dashboard', icon: '📊', label: 'Dashboard' },
    { v: 'artist-drops',     icon: '💿', label: 'My Drops' },
    { v: 'create-drop',      icon: '＋', label: 'Drop' },
    { v: 'artist-revenue',   icon: '💰', label: 'Revenue' },
    { v: 'artist-profile',   icon: '👤', label: 'Profile' },
  );
  if (role === 'label') all.push(
    { v: 'label-roster',     icon: '🎤', label: 'Roster' },
    { v: 'label-drops',      icon: '💿', label: 'Drops' },
    { v: 'label-revenue',    icon: '💰', label: 'Revenue' },
    { v: 'label-analytics',  icon: '📈', label: 'Analytics' },
    { v: 'label-profile',    icon: '🏷️', label: 'Label' },
  );
  if (role === 'admin') all.push(
    { v: 'admin-stats',      icon: '⚙️', label: 'Platform' },
    { v: 'admin-users',      icon: '👥', label: 'Users' },
    { v: 'admin-drops',      icon: '💿', label: 'Drops' },
    { v: 'admin-revenue',    icon: '💰', label: 'Revenue' },
    { v: 'admin-velocity',   icon: '🚀', label: 'Velocity' },
  );
  return all;
}

// ============================================================
// SHELL COMPONENTS
// ============================================================
function renderSidebar() {
  const u = state.user;
  const items = navItems();
  return `
    <aside class="sidebar">
      <div class="sidebar-logo">BLK<span>MRKT</span></div>
      <div class="sidebar-role-tag">${u?.role || ''}</div>
      <nav class="sidebar-nav">
        ${items.map(i => `
          <button class="nav-item ${state.view === i.v ? 'active' : ''}" onclick="nav('${i.v}')">
            <span class="nav-icon">${i.icon}</span>
            <span class="nav-label">${i.label}</span>
          </button>
        `).join('')}
      </nav>
      <div class="sidebar-footer">
        <div class="user-chip" onclick="nav('${roleProfile()}')">
          <div class="chip-avatar">${(u?.username || '?')[0].toUpperCase()}</div>
          <div class="chip-info">
            <div class="chip-name">@${esc(u?.username)}</div>
            <div class="chip-role">${u?.role}</div>
          </div>
          <button class="chip-out" onclick="event.stopPropagation();logout()" title="Sign out">↪</button>
        </div>
      </div>
    </aside>`;
}

function roleProfile() {
  const role = state.user?.role;
  if (role === 'artist') return 'artist-profile';
  if (role === 'label') return 'label-profile';
  return 'fan-profile';
}

function renderMobileHeader() {
  const u = state.user;
  return `
    <header class="mobile-header">
      <div class="mobile-logo">BLK<span>MRKT</span></div>
      <div class="mobile-header-right">
        ${u ? `<span class="text-sm text-gray">@${esc(u.username)}</span>` : ''}
        <button class="btn btn-sm btn-ghost" onclick="logout()">OUT</button>
      </div>
    </header>`;
}

function renderBottomNav() {
  const items = navItems();
  return `
    <nav class="bottom-nav">
      <div class="bottom-nav-inner">
        ${items.slice(0, 5).map(i => `
          <button class="bnav-item ${state.view === i.v ? 'active' : ''}" onclick="nav('${i.v}')">
            <span class="bnav-icon">${i.icon}</span>
            <span>${i.label}</span>
          </button>
        `).join('')}
      </div>
    </nav>`;
}

function pageWrap(title, subtitle, body, actions = '') {
  return `
    <div class="page-header">
      <div class="page-title">${title}</div>
      ${subtitle ? `<div class="page-subtitle">${subtitle}</div>` : ''}
      ${actions ? `<div class="page-actions">${actions}</div>` : ''}
    </div>
    <div class="page-body">${body}</div>`;
}

// ============================================================
// MASTER RENDER
// ============================================================
function render() {
  const app = document.getElementById('app');

  if (state.view === 'auth') {
    app.innerHTML = renderAuth();
    return;
  }

  // Check label setup for label role
  if (state.user?.role === 'label' && !state.label &&
      ['label-roster','label-drops','label-revenue','label-analytics'].includes(state.view)) {
    loadLabelThenRender();
    app.innerHTML = shellWrap('<div class="loading"><div class="spinner"></div></div>');
    return;
  }

  const content = getViewContent();
  app.innerHTML = shellWrap(content);

  // Post-render hooks
  afterRender();
  startCountdowns();
}

function shellWrap(content) {
  return `
    <div class="app-shell">
      ${renderSidebar()}
      <main class="main-content">
        ${renderMobileHeader()}
        ${content}
      </main>
    </div>
    ${renderBottomNav()}`;
}

async function loadLabelThenRender() {
  try {
    const data = await API.json('/labels/me');
    state.label = data.label;
  } catch (e) {}
  render();
}

function getViewContent() {
  switch (state.view) {
    // Shared
    case 'feed':              return renderFeed();
    case 'drop-detail':       return renderDropDetail();
    // Fan
    case 'collection':        return renderCollection();
    case 'fan-profile':       return renderFanProfile();
    // Artist
    case 'artist-dashboard':  return renderArtistDashboard();
    case 'artist-drops':      return renderArtistDrops();
    case 'create-drop':       return renderCreateDrop();
    case 'edit-drop':         return renderEditDrop();
    case 'artist-revenue':    return renderArtistRevenue();
    case 'artist-profile':    return renderArtistProfile();
    // Label
    case 'label-setup':       return renderLabelSetup();
    case 'label-roster':      return renderLabelRoster();
    case 'label-drops':       return renderLabelDrops();
    case 'label-revenue':     return renderLabelRevenue();
    case 'label-analytics':   return renderLabelAnalytics();
    case 'label-profile':     return renderLabelProfile();
    // Admin
    case 'admin-stats':       return renderAdminStats();
    case 'admin-users':       return renderAdminUsers();
    case 'admin-drops':       return renderAdminDrops();
    case 'admin-revenue':     return renderAdminRevenue();
    case 'admin-velocity':    return renderAdminVelocity();
    default:                  return renderFeed();
  }
}

function afterRender() {
  switch (state.view) {
    case 'feed':              loadFeed(); break;
    case 'collection':        loadCollection(); break;
    case 'fan-profile':       loadFanProfile(); break;
    case 'artist-dashboard':  loadArtistDashboard(); break;
    case 'artist-drops':      loadArtistDropsList(); break;
    case 'artist-revenue':    loadArtistRevenue(); break;
    case 'label-roster':      loadLabelRoster(); break;
    case 'label-drops':       loadLabelDropsList(); break;
    case 'label-revenue':     loadLabelRevenue(); break;
    case 'label-analytics':   loadLabelAnalytics(); break;
    case 'admin-stats':       loadAdminStats(); break;
    case 'admin-users':       loadAdminUsers(); break;
    case 'admin-drops':       loadAdminDrops(); break;
    case 'admin-revenue':     loadAdminRevenue(); break;
    case 'admin-velocity':    loadAdminVelocity(); break;
  }
}

// ============================================================
// AUTH VIEW
// ============================================================
function renderAuth() {
  return `
    <div class="auth-screen">
      <div class="auth-hero">
        <div class="auth-logo">BLK<span>MRKT</span></div>
        <div class="auth-tagline">Drops. Not Streams.</div>
      </div>
      <div class="auth-card">
        <div class="auth-tabs">
          <div class="auth-tab active" id="tab-login" onclick="switchAuthTab('login')">SIGN IN</div>
          <div class="auth-tab" id="tab-register" onclick="switchAuthTab('register')">CREATE ACCOUNT</div>
        </div>
        <div id="auth-error"></div>
        <form class="auth-form" id="auth-form" onsubmit="handleAuth(event)">
          <div id="register-fields" class="hidden">
            <div class="form-group">
              <label class="form-label">Username</label>
              <input type="text" name="username" placeholder="@handle" autocomplete="username">
            </div>
            <div class="form-group">
              <label class="form-label">I am a...</label>
              <div class="role-grid">
                <div class="role-option selected" data-role="fan" onclick="selectRole(this)">
                  <div class="role-icon">🎧</div>
                  <div class="role-name">Fan</div>
                  <div class="role-desc">Discover &amp; collect</div>
                </div>
                <div class="role-option" data-role="artist" onclick="selectRole(this)">
                  <div class="role-icon">🎤</div>
                  <div class="role-name">Artist</div>
                  <div class="role-desc">Drop your music</div>
                </div>
                <div class="role-option" data-role="label" onclick="selectRole(this)">
                  <div class="role-icon">🏷️</div>
                  <div class="role-name">Label</div>
                  <div class="role-desc">Manage a roster</div>
                </div>
              </div>
            </div>
            <div class="form-group">
              <label class="form-label">City (optional)</label>
              <input type="text" name="city" placeholder="New York, LA, ATL...">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Email</label>
            <input type="email" name="email" placeholder="you@domain.com" required autocomplete="email">
          </div>
          <div class="form-group">
            <label class="form-label">Password</label>
            <input type="password" name="password" placeholder="••••••••" required autocomplete="current-password">
          </div>
          <button type="submit" class="btn btn-primary btn-full btn-lg" id="auth-submit">SIGN IN</button>
        </form>
      </div>
    </div>`;
}

window._authMode = 'login';

window.switchAuthTab = function(mode) {
  window._authMode = mode;
  document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
  document.getElementById(`tab-${mode}`).classList.add('active');
  const regFields = document.getElementById('register-fields');
  const btn = document.getElementById('auth-submit');
  if (mode === 'register') {
    regFields.classList.remove('hidden');
    btn.textContent = 'CREATE ACCOUNT';
  } else {
    regFields.classList.add('hidden');
    btn.textContent = 'SIGN IN';
  }
};

window.selectRole = function(el) {
  document.querySelectorAll('.role-option').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
};

window.handleAuth = async function(e) {
  e.preventDefault();
  const form = e.target;
  const errDiv = document.getElementById('auth-error');
  const btn = document.getElementById('auth-submit');
  btn.disabled = true;
  errDiv.innerHTML = '';

  try {
    let data;
    if (window._authMode === 'register') {
      const role = document.querySelector('.role-option.selected')?.dataset.role || 'fan';
      data = await API.json('/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username?.value || '',
          email: form.email.value,
          password: form.password.value,
          role,
          city: form.city?.value || '',
        }),
      });
    } else {
      data = await API.json('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email: form.email.value, password: form.password.value }),
      });
    }
    state.user = data.user;
    state.token = data.access_token;
    state.refreshToken = data.refresh_token;
    saveAuth();

    // Label users: load their label
    if (state.user.role === 'label') {
      try {
        const ld = await API.json('/labels/me');
        state.label = ld.label;
        nav(state.label ? 'label-roster' : 'label-setup');
      } catch (e) { nav('label-setup'); }
    } else if (state.user.role === 'artist') {
      nav('artist-dashboard');
    } else if (state.user.role === 'admin') {
      nav('admin-stats');
    } else {
      nav('feed');
    }
  } catch (err) {
    errDiv.innerHTML = `<div class="auth-error">${esc(err.error || 'Something went wrong')}</div>`;
    btn.disabled = false;
  }
};

// ============================================================
// FEED (shared — all roles)
// ============================================================
function renderFeed() {
  return pageWrap('DROPS', 'Scarcity-driven music releases', `
    <div class="tab-bar">
      <div class="tab ${state.feedTab==='live'?'active':''}" onclick="switchFeedTab('live')">LIVE NOW</div>
      <div class="tab ${state.feedTab==='trending'?'active':''}" onclick="switchFeedTab('trending')">TRENDING</div>
      <div class="tab ${state.feedTab==='scenes'?'active':''}" onclick="switchFeedTab('scenes')">SCENES</div>
    </div>
    <div id="feed-content"><div class="loading"><div class="spinner"></div></div></div>
  `);
}

window.switchFeedTab = function(tab) {
  state.feedTab = tab;
  render();
};

async function loadFeed() {
  const el = document.getElementById('feed-content');
  if (!el) return;
  try {
    let drops, scenes;
    if (state.feedTab === 'live') {
      const d = await API.json('/drops?status=live&limit=50');
      drops = d.drops || [];
      el.innerHTML = drops.length
        ? `<div class="drops-grid">${drops.map(renderDropCard).join('')}</div>`
        : emptyState('💿', 'No live drops yet', 'Check back soon');
    } else if (state.feedTab === 'trending') {
      const d = await API.json('/drops/trending');
      drops = d.drops || [];
      el.innerHTML = drops.length
        ? `<div class="drops-grid">${drops.map(renderDropCard).join('')}</div>`
        : emptyState('🔥', 'Nothing trending yet', 'Be the first to drop');
    } else {
      const d = await API.json('/scenes');
      scenes = d.scenes || [];
      el.innerHTML = scenes.length
        ? `<div class="drops-grid">${scenes.map(renderSceneCard).join('')}</div>`
        : emptyState('🌍', 'No scenes yet', 'Scenes are collections of drops by city/genre');
    }
  } catch (e) {
    if (el) el.innerHTML = emptyState('⚠️', 'Failed to load', e.error || 'Try refreshing');
  }
}

function renderDropCard(d) {
  const cv = coverUrl(d);
  const vel = d.velocity || 0;
  const sp = d.supply_pct || 0;
  const barClass = sp > 80 ? 'critical' : sp > 60 ? 'low' : '';
  const hot = vel >= 50;
  return `
    <div class="drop-card ${hot ? 'hot' : ''}" onclick="openDrop('${d.id}')">
      <div class="cover">
        ${cv ? `<img src="${cv}" alt="">` : `<div class="no-cover">🎵</div>`}
        <span class="status-badge ${d.status}">${d.status === 'live' ? 'LIVE' : d.status === 'scheduled' ? 'SOON' : 'ENDED'}</span>
        ${vel > 0 ? `<span class="vel-badge">${velEmoji(vel)}</span>` : ''}
      </div>
      <div class="card-body">
        <div class="drop-title">${esc(d.title)}</div>
        <div class="drop-artist-name">@${esc(d.artist_name || 'unknown')}</div>
        ${d.total_supply != null ? `
          <div class="supply-wrap">
            <div class="supply-bar-track"><div class="supply-bar-fill ${barClass}" style="width:${sp}%"></div></div>
            <div class="supply-text">${(d.total_supply - d.remaining_supply)}/${d.total_supply} claimed</div>
          </div>` : '<div class="supply-text">OPEN</div>'}
        ${d.countdown_seconds > 0 ? `<div class="drop-countdown ${d.countdown_seconds < 3600 ? 'urgent' : ''}" data-cd="${d.countdown_seconds}">${fmtCountdown(d.countdown_seconds)}</div>` : ''}
      </div>
    </div>`;
}

function renderSceneCard(s) {
  return `
    <div class="drop-card" onclick="toast('Scene drops coming soon')">
      <div class="cover"><div class="no-cover">🌍</div></div>
      <div class="card-body">
        <div class="drop-title">${esc(s.name)}</div>
        <div class="drop-artist-name">${esc(s.city || 'Global')} • ${s.drop_count || 0} drops</div>
      </div>
    </div>`;
}

function emptyState(icon, title, sub, btn = '') {
  return `<div class="empty-state"><div class="icon">${icon}</div><div class="title">${title}</div><div class="subtitle">${sub}</div>${btn}</div>`;
}

// ============================================================
// DROP DETAIL
// ============================================================
function renderDropDetail() {
  const d = state.viewData.drop;
  if (!d) return `<div class="loading"><div class="spinner"></div></div>`;

  const cv = coverUrl(d);
  const eng = d.engagement || {};
  const hasAccess = d.user_has_access;
  const isLive = d.status === 'live';
  const isSoldOut = d.is_sold_out;
  const sp = d.supply_pct || 0;
  const claimed = d.total_supply != null ? d.total_supply - d.remaining_supply : 0;

  let actionBtn = '';
  if (isSoldOut) actionBtn = '<button class="btn btn-secondary btn-full" disabled>SOLD OUT</button>';
  else if (d.status === 'expired') actionBtn = '<button class="btn btn-secondary btn-full" disabled>EXPIRED</button>';
  else if (hasAccess) actionBtn = '<button class="btn btn-outline btn-full" disabled>✓ ACCESS GRANTED</button>';
  else if (isLive) {
    const price = d.access_price > 0 ? fmtMoney(d.access_price) : 'FREE';
    actionBtn = `<button class="btn btn-primary btn-full btn-lg" onclick="claimDrop('${d.id}','stream')">CLAIM ACCESS • ${price}</button>`;
  } else actionBtn = '<button class="btn btn-secondary btn-full" disabled>NOT LIVE YET</button>';

  const ownBtn = d.own_price != null && isLive && !isSoldOut
    ? `<button class="btn btn-outline btn-full" onclick="claimDrop('${d.id}','own')">OWN THIS • ${fmtMoney(d.own_price)}</button>`
    : '';

  return `
    <div class="page-body" style="padding-top:24px">
      <button class="back-btn" onclick="nav('feed')">← Back to Drops</button>
      <div class="drop-detail-layout">
        <div class="detail-cover-sticky">
          <div class="detail-cover-img">
            ${cv ? `<img src="${cv}" alt="">` : '🎵'}
          </div>
          ${hasAccess && d.audio_path ? renderAudioPlayer(d.id) : ''}
          ${!hasAccess && d.audio_path ? `
            <div class="audio-player">
              <div class="player-row">
                <button class="play-btn" disabled>🔒</button>
                <div class="player-progress"><div class="progress-track"></div>
                  <div class="player-times"><span>Claim access to listen</span></div>
                </div>
              </div>
            </div>` : ''}
        </div>
        <div>
          <div class="detail-title">${esc(d.title)}</div>
          <div class="detail-artist" onclick="toast('Artist profiles coming soon')">@${esc(d.artist_name || 'unknown')} · ${esc(d.artist_city || '')}</div>

          ${d.countdown_seconds > 0 ? `<div class="detail-countdown ${d.countdown_seconds < 3600 ? 'urgent' : ''}" data-cd="${d.countdown_seconds}">${fmtCountdown(d.countdown_seconds)}</div>` : ''}

          ${d.total_supply != null ? `
            <div class="detail-supply">
              <div class="detail-supply-hdr"><span>${claimed} of ${d.total_supply} claimed</span><span class="text-mono">${Math.round(sp)}%</span></div>
              <div class="detail-supply-track"><div class="detail-supply-fill" style="width:${sp}%"></div></div>
            </div>` : ''}

          <div class="action-btns">
            ${actionBtn}
            ${ownBtn}
          </div>

          <div class="engagement-row">
            <div class="eng-stat"><div class="num">${fmt(eng.plays)}</div><div class="lbl">PLAYS</div></div>
            <div class="eng-stat"><div class="num">${fmt(eng.saves)}</div><div class="lbl">SAVES</div></div>
            <div class="eng-stat"><div class="num">${fmt(eng.shares)}</div><div class="lbl">SHARES</div></div>
            <div class="eng-stat"><div class="num red">${fmt(d.velocity)}</div><div class="lbl">VELOCITY</div></div>
          </div>

          ${d.description ? `<p class="text-gray text-sm" style="margin-bottom:20px;line-height:1.7">${esc(d.description)}</p>` : ''}

          <div class="artist-card" onclick="toast('Artist profiles coming soon')">
            <div class="artist-avatar">${(d.artist_name || '?')[0].toUpperCase()}</div>
            <div>
              <div style="font-weight:600">@${esc(d.artist_name || 'unknown')}</div>
              <div class="text-gray text-sm">${esc(d.artist_city || '')}</div>
            </div>
          </div>

          <div class="divider"></div>
          <div class="flex gap-2">
            <button class="btn btn-ghost btn-sm" onclick="logEngage('${d.id}','save');toast('Saved','success')">♡ Save</button>
            <button class="btn btn-ghost btn-sm" onclick="logEngage('${d.id}','share');navigator.clipboard?.writeText(location.href);toast('Link copied','success')">↗ Share</button>
          </div>
        </div>
      </div>
    </div>`;
}

function renderAudioPlayer(dropId) {
  return `
    <div class="audio-player" id="audio-player">
      <div class="player-row">
        <button class="play-btn" id="play-btn" onclick="togglePlay('${dropId}')">▶</button>
        <div class="player-progress">
          <div class="progress-track" id="progress-track" onclick="seekAudio(event)">
            <div class="progress-fill" id="progress-fill"></div>
          </div>
          <div class="player-times">
            <span id="cur-time">0:00</span>
            <span id="tot-time">0:00</span>
          </div>
        </div>
      </div>
    </div>`;
}

window.openDrop = async function(dropId) {
  state.viewData = {};
  nav('drop-detail');
  try {
    const data = await API.json(`/drops/${dropId}`);
    state.viewData.drop = data.drop;
    render();
    if (data.drop.status === 'live') logEngage(dropId, 'play');
  } catch (e) {
    toast(e.error || 'Failed to load drop', 'error');
    nav('feed');
  }
};

window.claimDrop = async function(dropId, type) {
  try {
    await API.json(`/drops/${dropId}/access`, { method: 'POST', body: JSON.stringify({ access_type: type }) });
    toast('Access granted!', 'success');
    const data = await API.json(`/drops/${dropId}`);
    state.viewData.drop = data.drop;
    render();
  } catch (e) {
    toast(e.error || 'Failed to claim', 'error');
  }
};

// ============================================================
// AUDIO
// ============================================================
window.togglePlay = function(dropId) {
  if (!state.audio || state.playingDropId !== dropId) {
    state.audio?.pause();
    state.audio = new Audio(`/api/audio/${dropId}`);
    state.playingDropId = dropId;
    state.audio.addEventListener('timeupdate', () => {
      const fill = document.getElementById('progress-fill');
      const cur = document.getElementById('cur-time');
      if (fill && state.audio) fill.style.width = `${(state.audio.currentTime / state.audio.duration) * 100}%`;
      if (cur && state.audio) cur.textContent = fmtTime(state.audio.currentTime);
    });
    state.audio.addEventListener('loadedmetadata', () => {
      const tot = document.getElementById('tot-time');
      if (tot && state.audio) tot.textContent = fmtTime(state.audio.duration);
    });
    state.audio.addEventListener('ended', () => {
      state.playing = false;
      const btn = document.getElementById('play-btn');
      if (btn) btn.textContent = '▶';
      logEngage(dropId, 'replay');
    });
    state.audio.play();
    state.playing = true;
    logEngage(dropId, 'play');
  } else if (state.playing) {
    state.audio.pause(); state.playing = false;
  } else {
    state.audio.play(); state.playing = true;
  }
  const btn = document.getElementById('play-btn');
  if (btn) btn.textContent = state.playing ? '⏸' : '▶';
};

window.seekAudio = function(e) {
  if (!state.audio) return;
  const track = document.getElementById('progress-track');
  const rect = track.getBoundingClientRect();
  state.audio.currentTime = ((e.clientX - rect.left) / rect.width) * state.audio.duration;
};

async function logEngage(dropId, action, meta = {}) {
  try { await API.json(`/drops/${dropId}/engage`, { method: 'POST', body: JSON.stringify({ action, metadata: meta }) }); } catch (e) {}
}
window.logEngage = logEngage;

// ============================================================
// FAN VIEWS
// ============================================================
function renderCollection() {
  return pageWrap('COLLECTION', 'Your claimed drops', `<div id="coll-grid" class="collection-grid"><div class="loading"><div class="spinner"></div></div></div>`);
}

async function loadCollection() {
  const el = document.getElementById('coll-grid');
  if (!el) return;
  try {
    const data = await API.json(`/users/${state.user.id}/collection`);
    const items = data.collection || [];
    if (!items.length) { el.innerHTML = emptyState('📦', 'Empty collection', 'Claim some drops to build your collection'); return; }
    el.innerHTML = items.map(item => {
      const cv = item.cover_image_path ? `/api/covers/${item.cover_image_path.split('/').pop()}` : null;
      return `
        <div class="collection-item" onclick="openDrop('${item.drop_id}')">
          <div class="thumb">${cv ? `<img src="${cv}" alt="">` : '🎵'}</div>
          <div class="info">
            <div class="title">${esc(item.title)}</div>
            <div class="artist">@${esc(item.artist_name)}</div>
          </div>
        </div>`;
    }).join('');
  } catch (e) { el.innerHTML = emptyState('⚠️', 'Failed to load', ''); }
}

function renderFanProfile() {
  const u = state.user;
  return pageWrap('PROFILE', '@' + u.username, `
    <div class="profile-layout">
      <div class="profile-sidebar">
        <div class="profile-card">
          <div class="profile-avatar-xl">${(u.username || '?')[0].toUpperCase()}</div>
          <div class="profile-username">@${esc(u.username)}</div>
          <div style="margin-bottom:8px">${roleBadge(u.role)}</div>
          <div class="profile-city">${esc(u.city || '')}</div>
          ${u.bio ? `<div class="profile-bio">${esc(u.bio)}</div>` : ''}
          <div class="profile-stats" id="fan-stats">
            <div class="profile-stat"><div class="num">-</div><div class="lbl">CLAIMS</div></div>
            <div class="profile-stat"><div class="num">-</div><div class="lbl">SAVES</div></div>
            <div class="profile-stat"><div class="num">-</div><div class="lbl">SHARES</div></div>
          </div>
        </div>
        <div style="margin-top:14px">
          <button class="btn btn-danger btn-full" onclick="logout()">SIGN OUT</button>
        </div>
      </div>
      <div>
        <div class="section-hdr"><div class="section-title">MY COLLECTION</div></div>
        <div id="fan-coll" class="collection-grid"><div class="loading"><div class="spinner"></div></div></div>
      </div>
    </div>`);
}

async function loadFanProfile() {
  try {
    const [collData, profileData] = await Promise.all([
      API.json(`/users/${state.user.id}/collection`),
      API.json(`/users/${state.user.id}/profile`),
    ]);
    const items = collData.collection || [];
    const el = document.getElementById('fan-coll');
    if (el) {
      el.innerHTML = items.length
        ? items.slice(0, 12).map(item => {
            const cv = item.cover_image_path ? `/api/covers/${item.cover_image_path.split('/').pop()}` : null;
            return `<div class="collection-item" onclick="openDrop('${item.drop_id}')">
              <div class="thumb">${cv ? `<img src="${cv}" alt="">` : '🎵'}</div>
              <div class="info"><div class="title">${esc(item.title)}</div><div class="artist">@${esc(item.artist_name)}</div></div>
            </div>`;
          }).join('')
        : emptyState('📦', 'No collection yet', 'Start claiming drops');
    }
    const statsEl = document.getElementById('fan-stats');
    if (statsEl && profileData.profile?.stats) {
      const s = profileData.profile.stats;
      statsEl.innerHTML = `
        <div class="profile-stat"><div class="num">${fmt(items.length)}</div><div class="lbl">CLAIMS</div></div>
        <div class="profile-stat"><div class="num">${fmt(s.total_saves || 0)}</div><div class="lbl">SAVES</div></div>
        <div class="profile-stat"><div class="num">${fmt(s.total_shares || 0)}</div><div class="lbl">SHARES</div></div>`;
    }
  } catch (e) {}
}

// ============================================================
// ARTIST VIEWS
// ============================================================
function renderArtistDashboard() {
  return pageWrap('DASHBOARD', 'Your artist metrics', `
    <div class="stats-grid" id="a-stats">
      ${['Drops','Total Plays','Total Fans','Revenue'].map(l => `<div class="stat-card"><div class="stat-label">${l}</div><div class="stat-value">-</div></div>`).join('')}
    </div>
    <div class="section-hdr">
      <div class="section-title">RECENT DROPS</div>
      <button class="btn btn-sm btn-primary" onclick="nav('create-drop')">+ New Drop</button>
    </div>
    <div id="a-drops"><div class="loading"><div class="spinner"></div></div></div>`,
    '', `<button class="btn btn-primary" onclick="nav('create-drop')">+ New Drop</button>`);
}

async function loadArtistDashboard() {
  try {
    const data = await API.json(`/users/${state.user.id}/profile`);
    const p = data.profile;
    const s = p?.stats || {};
    const statsEl = document.getElementById('a-stats');
    if (statsEl) statsEl.innerHTML = `
      <div class="stat-card"><div class="stat-label">DROPS</div><div class="stat-value">${fmt(s.total_drops)}</div></div>
      <div class="stat-card"><div class="stat-label">TOTAL PLAYS</div><div class="stat-value">${fmt(s.total_plays)}</div></div>
      <div class="stat-card"><div class="stat-label">TOTAL FANS</div><div class="stat-value">${fmt(s.total_fans)}</div></div>
      <div class="stat-card"><div class="stat-label">REVENUE</div><div class="stat-value green">${fmtMoney(s.total_revenue)}</div></div>`;

    const dropsEl = document.getElementById('a-drops');
    if (dropsEl) {
      const drops = p?.drops || [];
      dropsEl.innerHTML = drops.length
        ? drops.slice(0, 8).map(d => renderDropRow(d, true)).join('')
        : emptyState('🎤', 'No drops yet', 'Create your first drop and go live', `<button class="btn btn-primary" onclick="nav('create-drop')">+ Create Drop</button>`);
    }
  } catch (e) {}
}

function renderDropRow(d, showActions = false) {
  const cv = d.cover_image_path ? `/api/covers/${d.cover_image_path.split('/').pop()}` : null;
  return `
    <div class="drop-row" onclick="openDrop('${d.id}')">
      <div class="thumb">${cv ? `<img src="${cv}" alt="">` : '🎵'}</div>
      <div class="info">
        <div class="title">${esc(d.title)}</div>
        <div class="sub">${statusBadge(d.status)} ${d.total_supply ? `• ${d.total_supply - (d.remaining_supply||0)}/${d.total_supply} claimed` : ''}</div>
      </div>
      <div class="meta">
        <div class="stat"><div class="num">${fmt(d.claim_count || 0)}</div><div class="lbl">CLAIMS</div></div>
        <div class="stat"><div class="num green">${fmtMoney(d.revenue || 0)}</div><div class="lbl">REV</div></div>
        ${showActions ? `<div onclick="event.stopPropagation()">
          <button class="btn btn-sm btn-ghost" onclick="nav('edit-drop',{drop:${JSON.stringify({id:d.id,title:d.title,status:d.status})}})">Edit</button>
        </div>` : ''}
      </div>
    </div>`;
}

function renderArtistDrops() {
  return pageWrap('MY DROPS', 'Manage your releases', `<div id="a-all-drops"><div class="loading"><div class="spinner"></div></div></div>`,
    '', `<button class="btn btn-primary" onclick="nav('create-drop')">+ New Drop</button>`);
}

async function loadArtistDropsList() {
  const el = document.getElementById('a-all-drops');
  if (!el) return;
  try {
    const data = await API.json(`/users/${state.user.id}/profile`);
    const drops = data.profile?.drops || [];
    el.innerHTML = drops.length
      ? drops.map(d => renderDropRow(d, true)).join('')
      : emptyState('🎤', 'No drops yet', 'Create your first drop', `<button class="btn btn-primary" onclick="nav('create-drop')">+ Create Drop</button>`);
  } catch (e) { el.innerHTML = emptyState('⚠️', 'Failed to load', ''); }
}

function renderCreateDrop() {
  return pageWrap('CREATE DROP', 'Release a new track', `
    <form id="create-form" onsubmit="handleCreateDrop(event)" style="max-width:640px">
      <div class="form-section-title">TRACK INFO</div>
      <div class="form-group"><label class="form-label">Title *</label><input type="text" name="title" placeholder="Drop title" required></div>
      <div class="form-group"><label class="form-label">Description</label><textarea name="description" placeholder="Tell the story behind this drop..."></textarea></div>
      <div class="form-row">
        <div class="form-group"><label class="form-label">Audio File (MP3/WAV)</label><input type="file" name="audio" accept=".mp3,.wav,.flac"></div>
        <div class="form-group"><label class="form-label">Cover Art</label><input type="file" name="cover" accept=".png,.jpg,.jpeg,.webp"></div>
      </div>

      <div class="form-section-title">DROP TYPE</div>
      <div class="form-group">
        <label class="form-label">Type</label>
        <select name="drop_type" onchange="toggleDropTypeFields(this.value)">
          <option value="open">Open — Unlimited, always available</option>
          <option value="timed">Timed — Available for a set window</option>
          <option value="limited">Limited — Capped supply</option>
          <option value="rare">Rare — Ultra limited edition</option>
        </select>
      </div>
      <div id="supply-fields" class="form-row hidden">
        <div class="form-group"><label class="form-label">Total Supply</label><input type="number" name="total_supply" min="1" placeholder="e.g. 100"></div>
        <div class="form-group"><label class="form-label">Own Price ($)</label><input type="number" name="own_price" min="0" step="0.01" placeholder="0 = not for sale"></div>
      </div>
      <div class="form-group"><label class="form-label">Stream Access Price ($)</label><input type="number" name="access_price" min="0" step="0.01" value="0" placeholder="0 = free to stream"></div>

      <div class="form-section-title">SCHEDULE</div>
      <div class="form-row">
        <div class="form-group"><label class="form-label">Starts At</label><input type="datetime-local" name="starts_at"></div>
        <div class="form-group" id="expires-field"><label class="form-label">Expires At</label><input type="datetime-local" name="expires_at"></div>
      </div>

      <div class="flex gap-2">
        <button type="submit" class="btn btn-primary btn-lg">DROP IT</button>
        <button type="button" class="btn btn-ghost" onclick="nav('artist-drops')">Cancel</button>
      </div>
    </form>`);
}

window.toggleDropTypeFields = function(type) {
  const el = document.getElementById('supply-fields');
  if (el) el.classList.toggle('hidden', !['limited','rare','tiered'].includes(type));
};

window.handleCreateDrop = async function(e) {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  const btn = form.querySelector('[type=submit]');
  btn.disabled = true; btn.textContent = 'DROPPING...';

  const startsAt = form.starts_at.value;
  const expiresAt = form.expires_at.value;
  if (startsAt) fd.set('starts_at', new Date(startsAt).toISOString().replace('.000Z','Z'));
  else fd.delete('starts_at');
  if (expiresAt) fd.set('expires_at', new Date(expiresAt).toISOString().replace('.000Z','Z'));
  else fd.delete('expires_at');
  if (!form.total_supply?.value) fd.delete('total_supply');
  if (!form.own_price?.value) fd.delete('own_price');

  try {
    await API.upload('/drops', fd);
    toast('Drop created! 🔥', 'success');
    nav('artist-drops');
  } catch (err) {
    toast(err.error || 'Failed to create drop', 'error');
    btn.disabled = false; btn.textContent = 'DROP IT';
  }
};

function renderEditDrop() {
  const d = state.viewData.drop || {};
  return pageWrap('EDIT DROP', esc(d.title), `
    <div style="max-width:480px">
      <div class="section-hdr">
        <div class="section-title">STATUS CONTROL</div>
      </div>
      <div class="card card-pad mb-4">
        <p class="text-gray text-sm" style="margin-bottom:16px">Force this drop into a specific state.</p>
        <div class="flex gap-2" style="flex-wrap:wrap">
          ${['scheduled','live','locked','expired'].map(s =>
            `<button class="btn btn-sm ${s === d.status ? 'btn-primary' : 'btn-secondary'}" onclick="forceDropStatus('${d.id}','${s}')">${s.toUpperCase()}</button>`
          ).join('')}
        </div>
      </div>
      <div class="divider"></div>
      <div class="flex gap-2">
        <button class="btn btn-ghost" onclick="nav('artist-drops')">← Back</button>
        <button class="btn btn-danger" onclick="confirmDeleteDrop('${d.id}')">Delete Drop</button>
      </div>
    </div>`);
}

window.forceDropStatus = async function(dropId, status) {
  try {
    await API.json(`/admin/drops/${dropId}/status`, { method: 'PATCH', body: JSON.stringify({ status }) });
    toast(`Status set to ${status}`, 'success');
    nav('artist-drops');
  } catch (e) {
    // Fall back — artist doesn't have admin access, so use a drop-level endpoint
    toast('Admin access required to force status', 'error');
  }
};

window.confirmDeleteDrop = function(dropId) {
  if (confirm('Delete this drop permanently? This cannot be undone.')) {
    deleteDrop(dropId);
  }
};

async function deleteDrop(dropId) {
  try {
    await API.json(`/admin/drops/${dropId}`, { method: 'DELETE' });
    toast('Drop deleted', 'success');
    nav('artist-drops');
  } catch (e) { toast(e.error || 'Delete failed', 'error'); }
}

function renderArtistRevenue() {
  return pageWrap('REVENUE', 'Earnings from your drops', `
    <div class="stats-grid" id="ar-stats">
      ${['Gross Revenue','Total Sales','Unique Buyers','Avg Sale'].map(l => `<div class="stat-card"><div class="stat-label">${l}</div><div class="stat-value">-</div></div>`).join('')}
    </div>
    <div class="section-hdr"><div class="section-title">PER DROP BREAKDOWN</div></div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Drop</th><th>Status</th><th>Claims</th><th>Revenue</th><th>Avg</th></tr></thead>
      <tbody id="ar-table"><tr><td colspan="5" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>`);
}

async function loadArtistRevenue() {
  try {
    const data = await API.json(`/users/${state.user.id}/profile`);
    const drops = data.profile?.drops || [];
    const stats = data.profile?.stats || {};

    const el = document.getElementById('ar-stats');
    if (el) el.innerHTML = `
      <div class="stat-card"><div class="stat-label">GROSS REVENUE</div><div class="stat-value green">${fmtMoney(stats.total_revenue)}</div></div>
      <div class="stat-card"><div class="stat-label">TOTAL SALES</div><div class="stat-value">${fmt(stats.total_sales)}</div></div>
      <div class="stat-card"><div class="stat-label">FANS</div><div class="stat-value">${fmt(stats.total_fans)}</div></div>
      <div class="stat-card"><div class="stat-label">DROPS</div><div class="stat-value">${fmt(stats.total_drops)}</div></div>`;

    const tbody = document.getElementById('ar-table');
    if (tbody) tbody.innerHTML = drops.length
      ? drops.map(d => `<tr>
          <td><strong>${esc(d.title)}</strong></td>
          <td>${statusBadge(d.status)}</td>
          <td class="mono">${fmt(d.claim_count || 0)}</td>
          <td class="mono text-green">${fmtMoney(d.revenue || 0)}</td>
          <td class="mono">${d.claim_count ? fmtMoney((d.revenue||0)/d.claim_count) : '-'}</td>
        </tr>`).join('')
      : `<tr><td colspan="5" style="text-align:center;color:var(--gray-mid);padding:30px">No drops yet</td></tr>`;
  } catch (e) {}
}

function renderArtistProfile() {
  const u = state.user;
  return pageWrap('PROFILE', 'Your public artist page', `
    <div class="profile-layout">
      <div class="profile-sidebar">
        <div class="profile-card">
          <div class="profile-avatar-xl">${(u.username||'?')[0].toUpperCase()}</div>
          <div class="profile-username">@${esc(u.username)}</div>
          <div style="margin-bottom:8px">${roleBadge(u.role)}</div>
          <div class="profile-city">${esc(u.city||'')}</div>
          ${u.bio ? `<div class="profile-bio" style="margin-top:10px">${esc(u.bio)}</div>` : ''}
        </div>
        <div style="margin-top:14px">
          <button class="btn btn-danger btn-full" onclick="logout()">SIGN OUT</button>
        </div>
      </div>
      <div>
        <div class="section-hdr"><div class="section-title">YOUR DROPS</div><button class="btn btn-sm btn-primary" onclick="nav('create-drop')">+ New</button></div>
        <div id="ap-drops"><div class="loading"><div class="spinner"></div></div></div>
      </div>
    </div>`);
}

// reuses loadArtistDropsList but targets different element id
async function loadArtistProfileDrops() {
  const el = document.getElementById('ap-drops');
  if (!el) return;
  try {
    const data = await API.json(`/users/${state.user.id}/profile`);
    const drops = data.profile?.drops || [];
    el.innerHTML = drops.length ? drops.map(d => renderDropRow(d)).join('') : emptyState('🎤','No drops','');
  } catch (e) {}
}

// ============================================================
// LABEL VIEWS
// ============================================================
function renderLabelSetup() {
  return pageWrap('SETUP YOUR LABEL', 'Create your label profile to get started', `
    <form id="label-form" onsubmit="handleCreateLabel(event)" style="max-width:560px">
      <div class="form-group"><label class="form-label">Label Name *</label><input type="text" name="name" placeholder="e.g. Forbidden Frequencies" required></div>
      <div class="form-group"><label class="form-label">City</label><input type="text" name="city" placeholder="New York, ATL, LA..."></div>
      <div class="form-group"><label class="form-label">About your label</label><textarea name="bio" placeholder="What's the label about? What's the sound?"></textarea></div>
      <button type="submit" class="btn btn-primary btn-lg">CREATE LABEL</button>
    </form>`);
}

window.handleCreateLabel = async function(e) {
  e.preventDefault();
  const form = e.target;
  const btn = form.querySelector('[type=submit]');
  btn.disabled = true;
  try {
    const data = await API.json('/labels', {
      method: 'POST',
      body: JSON.stringify({ name: form.name.value, city: form.city.value, bio: form.bio.value }),
    });
    state.label = data.label;
    toast('Label created!', 'success');
    nav('label-roster');
  } catch (e) {
    toast(e.error || 'Failed to create label', 'error');
    btn.disabled = false;
  }
};

function renderLabelRoster() {
  if (!state.label) return pageWrap('ROSTER', '', `<div class="loading"><div class="spinner"></div></div>`);
  return pageWrap('ROSTER', state.label.name, `
    <div id="l-roster"><div class="loading"><div class="spinner"></div></div></div>`,
    '', `<button class="btn btn-primary" onclick="showAddArtistModal()">+ Add Artist</button>
         <div id="add-modal"></div>`);
}

async function loadLabelRoster() {
  if (!state.label) return;
  const el = document.getElementById('l-roster');
  if (!el) return;
  try {
    const data = await API.json(`/labels/${state.label.id}/roster`);
    const roster = data.roster || [];
    el.innerHTML = roster.length
      ? `<div class="roster-grid">${roster.map(a => `
          <div class="roster-card">
            <div class="top">
              <div class="artist-avatar">${(a.username||'?')[0].toUpperCase()}</div>
              <div><div class="name">@${esc(a.username)}</div><div class="text-gray text-sm">${esc(a.city||'')}</div></div>
            </div>
            <div class="roster-stats">
              <div class="roster-stat"><div class="num">${fmt(a.total_drops)}</div><div class="lbl">DROPS</div></div>
              <div class="roster-stat"><div class="num">${fmt(a.total_sales)}</div><div class="lbl">SALES</div></div>
              <div class="roster-stat"><div class="num green">${fmtMoney(a.revenue)}</div><div class="lbl">REV</div></div>
            </div>
            <div class="flex gap-2" style="margin-top:12px;border-top:1px solid var(--black-4);padding-top:12px">
              <button class="btn btn-sm btn-danger" onclick="removeArtist('${a.id}')">Remove</button>
            </div>
          </div>`).join('')}</div>`
      : emptyState('🎤', 'Empty roster', 'Add artists to your label', `<button class="btn btn-primary" onclick="showAddArtistModal()">+ Add Artist</button>`);
  } catch (e) { el.innerHTML = emptyState('⚠️', 'Failed to load', ''); }
}

window.showAddArtistModal = function() {
  const existing = document.getElementById('add-modal');
  if (existing) {
    existing.innerHTML = `
      <div class="modal-overlay" onclick="if(event.target===this)closeAddModal()">
        <div class="modal">
          <div class="modal-title">ADD ARTIST TO ROSTER</div>
          <button class="modal-close" onclick="closeAddModal()">×</button>
          <p class="text-gray text-sm" style="margin-bottom:16px">Enter the artist's username or user ID to add them to your label roster.</p>
          <div class="form-group"><label class="form-label">Username</label><input type="text" id="add-artist-input" placeholder="@username"></div>
          <div class="flex gap-2">
            <button class="btn btn-primary" onclick="submitAddArtist()">ADD TO ROSTER</button>
            <button class="btn btn-ghost" onclick="closeAddModal()">Cancel</button>
          </div>
        </div>
      </div>`;
  }
};
window.closeAddModal = () => { const m = document.getElementById('add-modal'); if(m) m.innerHTML=''; };

window.submitAddArtist = async function() {
  const input = document.getElementById('add-artist-input');
  const username = (input?.value || '').trim().replace('@','');
  if (!username) return;
  try {
    await API.json(`/labels/${state.label.id}/roster`, { method: 'POST', body: JSON.stringify({ username }) });
    toast('Artist added to roster', 'success');
    closeAddModal();
    loadLabelRoster();
  } catch (e) { toast(e.error || 'Failed to add artist', 'error'); }
};

window.removeArtist = async function(artistId) {
  if (!confirm('Remove this artist from your roster?')) return;
  try {
    await API.json(`/labels/${state.label.id}/roster/${artistId}`, { method: 'DELETE' });
    toast('Artist removed', 'success');
    loadLabelRoster();
  } catch (e) { toast(e.error || 'Failed', 'error'); }
};

function renderLabelDrops() {
  if (!state.label) return pageWrap('DROPS', '', '<div class="loading"><div class="spinner"></div></div>');
  return pageWrap('LABEL DROPS', 'All drops from your roster', `
    <div class="tab-bar">
      <div class="tab active" id="ld-all" onclick="filterLabelDrops('')">ALL</div>
      <div class="tab" id="ld-live" onclick="filterLabelDrops('live')">LIVE</div>
      <div class="tab" id="ld-sched" onclick="filterLabelDrops('scheduled')">SCHEDULED</div>
      <div class="tab" id="ld-expired" onclick="filterLabelDrops('expired')">EXPIRED</div>
    </div>
    <div id="ld-content"><div class="loading"><div class="spinner"></div></div></div>`);
}

window.filterLabelDrops = async function(status) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const id = { '': 'ld-all', 'live': 'ld-live', 'scheduled': 'ld-sched', 'expired': 'ld-expired' }[status];
  document.getElementById(id)?.classList.add('active');
  await loadLabelDropsList(status);
};

async function loadLabelDropsList(status = '') {
  if (!state.label) return;
  const el = document.getElementById('ld-content');
  if (!el) return;
  try {
    const url = `/labels/${state.label.id}/drops${status ? `?status=${status}` : ''}`;
    const data = await API.json(url);
    const drops = data.drops || [];
    el.innerHTML = drops.length
      ? drops.map(d => `
        <div class="drop-row" onclick="openDrop('${d.id}')">
          <div class="thumb">🎵</div>
          <div class="info">
            <div class="title">${esc(d.title)}</div>
            <div class="sub">${statusBadge(d.status)} • @${esc(d.artist_username)}</div>
          </div>
          <div class="meta">
            <div class="stat"><div class="num">${fmt(d.claim_count||0)}</div><div class="lbl">CLAIMS</div></div>
            <div class="stat"><div class="num green">${fmtMoney(d.revenue||0)}</div><div class="lbl">REV</div></div>
          </div>
        </div>`).join('')
      : emptyState('💿', 'No drops', status ? `No ${status} drops` : 'Your roster hasn\'t dropped yet');
  } catch (e) { el.innerHTML = emptyState('⚠️', 'Failed to load', ''); }
}

function renderLabelRevenue() {
  if (!state.label) return pageWrap('REVENUE', '', '<div class="loading"><div class="spinner"></div></div>');
  return pageWrap('REVENUE', state.label.name + ' · Earnings', `
    <div class="stats-grid" id="lr-stats">
      ${['Gross Revenue','Total Sales','Unique Buyers','Monetized Drops'].map(l =>
        `<div class="stat-card"><div class="stat-label">${l}</div><div class="stat-value">-</div></div>`
      ).join('')}
    </div>
    <div class="section-hdr"><div class="section-title">BY ARTIST</div></div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Artist</th><th>Drops</th><th>Sales</th><th>Revenue</th><th>Avg Sale</th></tr></thead>
      <tbody id="lr-table"><tr><td colspan="5" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>`);
}

async function loadLabelRevenue() {
  if (!state.label) return;
  try {
    const data = await API.json(`/labels/${state.label.id}/revenue`);
    const t = data.totals || {};
    const el = document.getElementById('lr-stats');
    if (el) el.innerHTML = `
      <div class="stat-card"><div class="stat-label">GROSS REVENUE</div><div class="stat-value green">${fmtMoney(t.gross_revenue)}</div></div>
      <div class="stat-card"><div class="stat-label">TOTAL SALES</div><div class="stat-value">${fmt(t.total_sales)}</div></div>
      <div class="stat-card"><div class="stat-label">UNIQUE BUYERS</div><div class="stat-value">${fmt(t.unique_buyers)}</div></div>
      <div class="stat-card"><div class="stat-label">MONETIZED DROPS</div><div class="stat-value">${fmt(t.monetized_drops)}</div></div>`;

    const tbody = document.getElementById('lr-table');
    const artists = data.by_artist || [];
    if (tbody) tbody.innerHTML = artists.length
      ? artists.map(a => `<tr>
          <td>@${esc(a.username)}</td>
          <td class="mono">${fmt(a.drop_count)}</td>
          <td class="mono">${fmt(a.total_sales)}</td>
          <td class="mono text-green">${fmtMoney(a.revenue)}</td>
          <td class="mono">${fmtMoney(a.avg_sale)}</td>
        </tr>`).join('')
      : `<tr><td colspan="5" style="text-align:center;color:var(--gray-mid);padding:30px">No revenue yet</td></tr>`;
  } catch (e) {}
}

function renderLabelAnalytics() {
  if (!state.label) return pageWrap('ANALYTICS', '', '<div class="loading"><div class="spinner"></div></div>');
  return pageWrap('ANALYTICS', 'Velocity & engagement across your roster', `
    <div class="section-hdr"><div class="section-title">TOP DROPS BY VELOCITY</div></div>
    <div class="table-wrap" style="margin-bottom:28px"><table class="data-table">
      <thead><tr><th>Drop</th><th>Artist</th><th>Plays</th><th>Saves</th><th>Shares</th><th>Claims</th><th>Velocity</th></tr></thead>
      <tbody id="la-drops-table"><tr><td colspan="7" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>
    <div class="section-hdr"><div class="section-title">ROSTER ENGAGEMENT</div></div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Artist</th><th>Plays</th><th>Saves</th><th>Shares</th><th>Claims</th></tr></thead>
      <tbody id="la-roster-table"><tr><td colspan="5" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>`);
}

async function loadLabelAnalytics() {
  if (!state.label) return;
  try {
    const data = await API.json(`/labels/${state.label.id}/analytics`);
    const drops = data.top_drops || [];
    const roster = data.roster_engagement || [];
    const dt = document.getElementById('la-drops-table');
    if (dt) dt.innerHTML = drops.length
      ? drops.map(d => `<tr>
          <td>${esc(d.title)}</td>
          <td class="text-gray">@${esc(d.artist)}</td>
          <td class="mono">${fmt(d.plays)}</td>
          <td class="mono">${fmt(d.saves)}</td>
          <td class="mono">${fmt(d.shares)}</td>
          <td class="mono">${fmt(d.claims)}</td>
          <td class="mono text-red">${d.velocity_score}</td>
        </tr>`).join('')
      : `<tr><td colspan="7" style="text-align:center;color:var(--gray-mid);padding:30px">No data yet</td></tr>`;
    const rt = document.getElementById('la-roster-table');
    if (rt) rt.innerHTML = roster.length
      ? roster.map(a => `<tr>
          <td>@${esc(a.username)}</td>
          <td class="mono">${fmt(a.plays)}</td>
          <td class="mono">${fmt(a.saves)}</td>
          <td class="mono">${fmt(a.shares)}</td>
          <td class="mono">${fmt(a.claims)}</td>
        </tr>`).join('')
      : `<tr><td colspan="5" style="text-align:center;color:var(--gray-mid);padding:30px">No data yet</td></tr>`;
  } catch (e) {}
}

function renderLabelProfile() {
  const l = state.label;
  if (!l) return renderLabelSetup();
  return pageWrap('LABEL PROFILE', l.name, `
    <div class="profile-layout">
      <div class="profile-sidebar">
        <div class="profile-card">
          <div class="profile-avatar-xl">🏷️</div>
          <div class="profile-username">${esc(l.name)}</div>
          <div style="margin-bottom:8px">${roleBadge('label')}</div>
          <div class="profile-city">${esc(l.city||'')}</div>
          ${l.bio ? `<div class="profile-bio" style="margin-top:10px">${esc(l.bio)}</div>` : ''}
        </div>
        <div style="margin-top:14px">
          <button class="btn btn-secondary btn-full" onclick="showEditLabelModal()">Edit Label</button>
          <button class="btn btn-danger btn-full" style="margin-top:8px" onclick="logout()">SIGN OUT</button>
        </div>
      </div>
      <div>
        <div class="section-hdr"><div class="section-title">ACCOUNT</div></div>
        <div class="card card-pad">
          <div class="text-sm text-gray">Owner</div>
          <div style="margin-bottom:12px">@${esc(state.user?.username)}</div>
          <div class="text-sm text-gray">Email</div>
          <div>${esc(state.user?.email)}</div>
        </div>
      </div>
    </div>`,
    '', `<button class="btn btn-primary" onclick="nav('label-roster')">Manage Roster</button>`);
}

window.showEditLabelModal = function() {
  const l = state.label;
  const body = document.body;
  const div = document.createElement('div');
  div.id = 'edit-label-modal';
  div.innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)document.getElementById('edit-label-modal').remove()">
      <div class="modal">
        <div class="modal-title">EDIT LABEL</div>
        <button class="modal-close" onclick="document.getElementById('edit-label-modal').remove()">×</button>
        <form onsubmit="submitEditLabel(event)">
          <div class="form-group"><label class="form-label">Label Name</label><input type="text" name="name" value="${esc(l?.name||'')}"></div>
          <div class="form-group"><label class="form-label">City</label><input type="text" name="city" value="${esc(l?.city||'')}"></div>
          <div class="form-group"><label class="form-label">Bio</label><textarea name="bio">${esc(l?.bio||'')}</textarea></div>
          <button type="submit" class="btn btn-primary">SAVE</button>
        </form>
      </div>
    </div>`;
  body.appendChild(div);
};

window.submitEditLabel = async function(e) {
  e.preventDefault();
  const form = e.target;
  try {
    const data = await API.json(`/labels/${state.label.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name: form.name.value, city: form.city.value, bio: form.bio.value }),
    });
    state.label = data.label;
    document.getElementById('edit-label-modal')?.remove();
    toast('Label updated', 'success');
    nav('label-profile');
  } catch (e) { toast(e.error || 'Failed', 'error'); }
};

// ============================================================
// ADMIN VIEWS
// ============================================================
function renderAdminStats() {
  return pageWrap('PLATFORM', 'System-wide stats', `
    <div class="stats-grid" id="adm-stats">
      ${['Total Users','Artists','Fans','Labels','Total Drops','Live Drops','Total Revenue','Total Claims'].map(l =>
        `<div class="stat-card"><div class="stat-label">${l}</div><div class="stat-value" id="as-${l.toLowerCase().replace(/\s+/g,'-')}">-</div></div>`
      ).join('')}
    </div>
    <div class="section-hdr"><div class="section-title">QUICK LINKS</div></div>
    <div class="flex gap-2" style="flex-wrap:wrap">
      <button class="btn btn-secondary" onclick="nav('admin-users')">👥 Users</button>
      <button class="btn btn-secondary" onclick="nav('admin-drops')">💿 All Drops</button>
      <button class="btn btn-secondary" onclick="nav('admin-revenue')">💰 Revenue</button>
      <button class="btn btn-secondary" onclick="nav('admin-velocity')">🚀 Velocity</button>
    </div>`);
}

async function loadAdminStats() {
  try {
    const data = await API.json('/admin/stats');
    const s = data.stats || {};
    const map = {
      'total-users': s.total_users, 'artists': s.total_artists,
      'fans': s.total_fans, 'labels': '-',
      'total-drops': s.total_drops, 'live-drops': s.live_drops,
      'total-revenue': fmtMoney(s.total_revenue), 'total-claims': s.total_claims,
    };
    Object.entries(map).forEach(([key, val]) => {
      const el = document.getElementById(`as-${key}`);
      if (el) el.textContent = val ?? '-';
    });
  } catch (e) {}
}

function renderAdminUsers() {
  return pageWrap('USERS', 'All platform accounts', `
    <div class="flex gap-2" style="margin-bottom:16px;flex-wrap:wrap">
      <input type="text" placeholder="Search username or email..." id="user-search" style="flex:1;min-width:200px" oninput="debounceUserSearch()">
      ${['','fan','artist','label','admin'].map(r =>
        `<button class="btn btn-sm ${r===''?'btn-primary':'btn-secondary'}" id="uf-${r||'all'}" onclick="filterUsers('${r}')">${r||'ALL'}</button>`
      ).join('')}
    </div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>User</th><th>Role</th><th>City</th><th>Drops</th><th>Revenue</th><th>Joined</th><th>Actions</th></tr></thead>
      <tbody id="users-table"><tr><td colspan="7" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>`);
}

let _userFilter = '';
function filterUsers(role) {
  _userFilter = role;
  document.querySelectorAll('[id^="uf-"]').forEach(b => b.className = b.className.replace('btn-primary','btn-secondary'));
  document.getElementById(`uf-${role||'all'}`).classList.replace('btn-secondary','btn-primary');
  loadAdminUsers();
}

let _userSearchTimeout;
window.debounceUserSearch = function() {
  clearTimeout(_userSearchTimeout);
  _userSearchTimeout = setTimeout(loadAdminUsers, 400);
};

async function loadAdminUsers() {
  const tbody = document.getElementById('users-table');
  if (!tbody) return;
  const search = document.getElementById('user-search')?.value || '';
  const params = new URLSearchParams({ per_page: '50' });
  if (_userFilter) params.set('role', _userFilter);
  if (search) params.set('q', search);
  try {
    const data = await API.json(`/admin/users?${params}`);
    const users = data.users || [];
    tbody.innerHTML = users.length
      ? users.map(u => `<tr>
          <td><strong>@${esc(u.username)}</strong><br><span class="text-gray text-sm">${esc(u.email)}</span></td>
          <td>${roleBadge(u.role)}</td>
          <td class="text-gray text-sm">${esc(u.city||'-')}</td>
          <td class="mono">${fmt(u.drop_count||0)}</td>
          <td class="mono text-green">${fmtMoney(u.revenue_generated||0)}</td>
          <td class="text-sm text-gray">${u.created_at?.slice(0,10)||'-'}</td>
          <td>
            <div class="flex gap-2">
              <select class="btn btn-sm btn-secondary" style="padding:4px 8px" onchange="changeUserRole('${u.id}',this.value)">
                ${['fan','artist','label','curator','admin'].map(r => `<option value="${r}" ${u.role===r?'selected':''}>${r}</option>`).join('')}
              </select>
              <button class="btn btn-sm btn-danger" onclick="deleteUser('${u.id}','${esc(u.username)}')">Del</button>
            </div>
          </td>
        </tr>`).join('')
      : `<tr><td colspan="7" style="text-align:center;color:var(--gray-mid);padding:30px">No users found</td></tr>`;
  } catch (e) { tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--red);padding:30px">${e.error||'Failed'}</td></tr>`; }
}

window.changeUserRole = async function(userId, newRole) {
  try {
    await API.json(`/admin/users/${userId}/role`, { method: 'PATCH', body: JSON.stringify({ role: newRole }) });
    toast(`Role updated to ${newRole}`, 'success');
  } catch (e) { toast(e.error || 'Failed', 'error'); }
};

window.deleteUser = async function(userId, username) {
  if (!confirm(`Delete @${username} and all their content? This cannot be undone.`)) return;
  try {
    await API.json(`/admin/users/${userId}`, { method: 'DELETE' });
    toast('User deleted', 'success');
    loadAdminUsers();
  } catch (e) { toast(e.error || 'Failed', 'error'); }
};

function renderAdminDrops() {
  return pageWrap('ALL DROPS', 'Platform-wide drop management', `
    <div class="flex gap-2" style="margin-bottom:16px;flex-wrap:wrap">
      <input type="text" placeholder="Search title or artist..." id="drop-search" style="flex:1;min-width:200px" oninput="debounceDropSearch()">
      ${['','live','scheduled','expired'].map(s =>
        `<button class="btn btn-sm ${s===''?'btn-primary':'btn-secondary'}" id="df-${s||'all'}" onclick="filterAdminDrops('${s}')">${s||'ALL'}</button>`
      ).join('')}
    </div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Drop</th><th>Artist</th><th>Status</th><th>Claims</th><th>Revenue</th><th>Created</th><th>Actions</th></tr></thead>
      <tbody id="drops-table"><tr><td colspan="7" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>`);
}

let _dropFilter = '';
window.filterAdminDrops = function(status) {
  _dropFilter = status;
  document.querySelectorAll('[id^="df-"]').forEach(b => b.className = b.className.replace('btn-primary','btn-secondary'));
  document.getElementById(`df-${status||'all'}`).classList.replace('btn-secondary','btn-primary');
  loadAdminDrops();
};

let _dropSearchTimeout;
window.debounceDropSearch = function() {
  clearTimeout(_dropSearchTimeout);
  _dropSearchTimeout = setTimeout(loadAdminDrops, 400);
};

async function loadAdminDrops() {
  const tbody = document.getElementById('drops-table');
  if (!tbody) return;
  const search = document.getElementById('drop-search')?.value || '';
  const params = new URLSearchParams({ per_page: '50' });
  if (_dropFilter) params.set('status', _dropFilter);
  if (search) params.set('q', search);
  try {
    const data = await API.json(`/admin/drops?${params}`);
    const drops = data.drops || [];
    tbody.innerHTML = drops.length
      ? drops.map(d => `<tr>
          <td><strong>${esc(d.title)}</strong></td>
          <td class="text-gray">@${esc(d.artist_username)}</td>
          <td>${statusBadge(d.status)}</td>
          <td class="mono">${fmt(d.claim_count||0)}</td>
          <td class="mono text-green">${fmtMoney(d.revenue||0)}</td>
          <td class="text-sm text-gray">${d.created_at?.slice(0,10)||'-'}</td>
          <td>
            <div class="flex gap-2">
              <select class="btn btn-sm btn-secondary" style="padding:4px 8px" onchange="forceStatus('${d.id}',this.value)">
                ${['scheduled','live','locked','expired'].map(s => `<option value="${s}" ${d.status===s?'selected':''}>${s}</option>`).join('')}
              </select>
              <button class="btn btn-sm btn-danger" onclick="adminDeleteDrop('${d.id}','${esc(d.title)}')">Del</button>
            </div>
          </td>
        </tr>`).join('')
      : `<tr><td colspan="7" style="text-align:center;color:var(--gray-mid);padding:30px">No drops found</td></tr>`;
  } catch (e) {}
}

window.forceStatus = async function(dropId, status) {
  try {
    await API.json(`/admin/drops/${dropId}/status`, { method: 'PATCH', body: JSON.stringify({ status }) });
    toast(`Status → ${status}`, 'success');
  } catch (e) { toast(e.error||'Failed','error'); }
};

window.adminDeleteDrop = async function(dropId, title) {
  if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
  try {
    await API.json(`/admin/drops/${dropId}`, { method: 'DELETE' });
    toast('Drop deleted', 'success');
    loadAdminDrops();
  } catch (e) { toast(e.error||'Failed','error'); }
};

function renderAdminRevenue() {
  return pageWrap('REVENUE', 'Platform-wide financials', `
    <div class="stats-grid" id="rev-stats">
      ${['Gross Revenue','Ownership Sales','Stream Revenue','Transactions','Paying Users','Avg Transaction'].map(l =>
        `<div class="stat-card"><div class="stat-label">${l}</div><div class="stat-value">-</div></div>`
      ).join('')}
    </div>
    <div class="section-hdr"><div class="section-title">TOP EARNING ARTISTS</div></div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Artist</th><th>Drops</th><th>Sales</th><th>Revenue</th><th>Highest Sale</th><th>Avg</th></tr></thead>
      <tbody id="rev-table"><tr><td colspan="6" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>`);
}

async function loadAdminRevenue() {
  try {
    const data = await API.json('/admin/revenue');
    const t = data.totals || {};
    const statsEl = document.getElementById('rev-stats');
    if (statsEl) {
      const vals = [t.gross_revenue, t.ownership_revenue, t.stream_revenue, t.total_transactions, t.paying_users, t.avg_transaction];
      statsEl.querySelectorAll('.stat-value').forEach((el,i) => {
        el.textContent = i < 3 || i === 5 ? fmtMoney(vals[i]) : fmt(vals[i]);
        if (i < 3) el.classList.add('green');
      });
    }
    const tbody = document.getElementById('rev-table');
    const artists = data.by_artist || [];
    if (tbody) tbody.innerHTML = artists.length
      ? artists.map(a => `<tr>
          <td>@${esc(a.username)}</td>
          <td class="mono">${fmt(a.drop_count)}</td>
          <td class="mono">${fmt(a.total_sales)}</td>
          <td class="mono text-green">${fmtMoney(a.total_revenue)}</td>
          <td class="mono">${fmtMoney(a.highest_sale)}</td>
          <td class="mono">${fmtMoney(a.avg_sale)}</td>
        </tr>`).join('')
      : `<tr><td colspan="6" style="text-align:center;color:var(--gray-mid);padding:30px">No revenue yet</td></tr>`;
  } catch (e) {}
}

function renderAdminVelocity() {
  return pageWrap('VELOCITY', 'Top performing drops by engagement score', `
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Drop</th><th>Artist</th><th>Status</th><th>Plays</th><th>Saves</th><th>Shares</th><th>Claims</th><th>Revenue</th><th>🔥 Score</th></tr></thead>
      <tbody id="vel-table"><tr><td colspan="9" style="text-align:center;padding:30px"><div class="spinner" style="margin:auto"></div></td></tr></tbody>
    </table></div>`);
}

async function loadAdminVelocity() {
  const tbody = document.getElementById('vel-table');
  if (!tbody) return;
  try {
    const data = await API.json('/admin/velocity?limit=30');
    const drops = data.drops || [];
    tbody.innerHTML = drops.length
      ? drops.map(d => `<tr>
          <td><strong>${esc(d.title)}</strong></td>
          <td class="text-gray">@${esc(d.artist)}</td>
          <td>${statusBadge(d.status)}</td>
          <td class="mono">${fmt(d.plays)}</td>
          <td class="mono">${fmt(d.saves)}</td>
          <td class="mono">${fmt(d.shares)}</td>
          <td class="mono">${fmt(d.claims)}</td>
          <td class="mono text-green">${fmtMoney(d.revenue)}</td>
          <td class="mono text-red"><strong>${d.velocity_score}</strong></td>
        </tr>`).join('')
      : `<tr><td colspan="9" style="text-align:center;color:var(--gray-mid);padding:30px">No data yet</td></tr>`;
  } catch (e) {}
}

// ============================================================
// COUNTDOWN TIMERS
// ============================================================
let _cdInterval = null;

function startCountdowns() {
  if (_cdInterval) clearInterval(_cdInterval);
  _cdInterval = setInterval(() => {
    document.querySelectorAll('[data-cd]').forEach(el => {
      let s = parseInt(el.dataset.cd) - 1;
      if (s < 0) s = 0;
      el.dataset.cd = s;
      el.textContent = fmtCountdown(s);
      if (s < 3600) el.classList.add('urgent');
    });
  }, 1000);
}

// ============================================================
// EXPOSE GLOBALS
// ============================================================
window.nav = nav;
window.logout = logout;
window.openDrop = window.openDrop;
window.state = state;

// ============================================================
// INIT
// ============================================================
(async function init() {
  if (loadAuth()) {
    const u = state.user;
    if (u?.role === 'label') {
      try {
        const ld = await API.json('/labels/me');
        state.label = ld.label;
        nav(state.label ? 'label-roster' : 'label-setup');
      } catch (e) { nav('label-setup'); }
    } else if (u?.role === 'artist') {
      nav('artist-dashboard');
    } else if (u?.role === 'admin') {
      nav('admin-stats');
    } else {
      nav('feed');
    }
  } else {
    render();
  }
})();
