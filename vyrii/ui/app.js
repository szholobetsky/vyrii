/* ═══════════════════════════════════════════════════════
   vyrii UI  —  app.js
   All logic: state, i18n, API calls, tab handlers, UI utils
═══════════════════════════════════════════════════════ */

// ── Auth ──────────────────────────────────────────────
// Patch window.fetch to inject Basic Auth header when credentials are stored.
// When the server returns 401 (auth mode is on), show the login overlay.
(function () {
  const _orig = window.fetch.bind(window);

  window.fetch = function (url, opts) {
    opts = Object.assign({}, opts || {});
    // Only inject stored creds if the caller didn't pass an explicit Authorization header
    const hdrs = opts.headers || {};
    if (!hdrs['Authorization'] && !hdrs['authorization']) {
      const creds = sessionStorage.getItem('vyrii_creds');
      if (creds) {
        opts.headers = Object.assign({}, hdrs, { 'Authorization': 'Basic ' + creds });
      }
    }
    return _orig(url, opts).then(function (res) {
      if (res.status === 401) { _showLoginOverlay(); }
      return res;
    });
  };
})();

function _showLoginOverlay() {
  const el = document.getElementById('login-overlay');
  if (el) { el.style.display = 'flex'; applyLang(state ? state.lang : 'en'); }
}

function _hideLoginOverlay() {
  const el = document.getElementById('login-overlay');
  if (el) { el.style.display = 'none'; }
}

function doLogout() {
  sessionStorage.removeItem('vyrii_creds');
  // Hit the logout endpoint with fake credentials so the browser clears its Basic Auth cache.
  // Without this the browser auto-sends old cached credentials on the next probe and bypasses the overlay.
  fetch('/vyrii/auth/logout', {
    headers: { 'Authorization': 'Basic ' + btoa('logout:logout') }
  }).catch(() => {}).finally(() => _showLoginOverlay());
}

async function doLogin() {
  const user = (document.getElementById('login-user').value || '').trim();
  const pass = document.getElementById('login-pass').value || '';
  const errEl = document.getElementById('login-error');
  errEl.style.display = 'none';
  if (!user || !pass) { errEl.style.display = 'block'; return; }
  const creds = btoa(unescape(encodeURIComponent(user + ':' + pass)));
  try {
    // Test credentials explicitly — bypasses stored creds injection
    const res = await fetch('/v1/models', { headers: { 'Authorization': 'Basic ' + creds } });
    if (res.status === 401) { errEl.style.display = 'block'; return; }
    sessionStorage.setItem('vyrii_creds', creds);
    _hideLoginOverlay();
    loadModels();
    loadSettings();
  } catch (e) {
    errEl.style.display = 'block';
  }
}

// ── i18n ──────────────────────────────────────────────
// I18N loaded from i18n.js


// ── STATE ─────────────────────────────────────────────
const state = {
  lang:      localStorage.getItem('lang')  || 'en',
  theme:     localStorage.getItem('theme') || 'ocean',
  model:     localStorage.getItem('model') || '',
  activeTab: 'chat',
  streaming: false,
  abortCtrl:   null,
  chatMessages: [],   // [{role, content}]
  chatId:      null,  // DB chat id (null = not yet saved)
  savedCount:  0,     // how many chatMessages are already persisted
  fileViewRaw: '',    // raw content of currently viewed file
  selectedFile: null,
  showThinking: false,
  smartCtx: true,
  fixedCtx: 4096,
  incognito: false,
};

// ── VIEWPORT HEIGHT (keeps --app-h = actual visible height on mobile) ─────
function _setAppH() {
  document.documentElement.style.setProperty('--app-h', window.innerHeight + 'px');
}
window.addEventListener('resize', _setAppH);
_setAppH();

// ── INIT ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  _setAppH();
  applyTheme(state.theme);
  applyLang(state.lang);
  loadThemes();
  setupTabNav();

  // Shift+Enter submits on all tabs (find nearest btn-primary in same panel)
  document.addEventListener('keydown', e => {
    if (!(e.key === 'Enter' && e.shiftKey)) return;
    const ta = e.target;
    if (!(ta.tagName === 'TEXTAREA' || ta.tagName === 'INPUT')) return;
    if (ta.id === 'chat-input') return; // chat has its own handler
    const panel = ta.closest('.tab-panel, .subtab-panel, .form-row')
               || ta.closest('.panel-body');
    if (!panel) return;
    const btn = panel.querySelector('.btn-primary');
    if (btn) { e.preventDefault(); btn.click(); }
  });
  // Probe auth: if 401 and no stored creds, show login overlay
  const probe = await fetch('/v1/models').catch(() => ({ status: 0 }));
  if (probe.status !== 401) {
    loadModels();
  }
  // If 401, fetch wrapper already showed the overlay
});

// ── THEME ─────────────────────────────────────────────
async function loadThemes() {
  try {
    const res    = await fetch('/vyrii/themes');
    const data   = await res.json();
    const themes = data.themes || ['ocean'];
    const opts   = themes
      .map(n => `<option value="${n}"${n === state.theme ? ' selected' : ''}>${n.charAt(0).toUpperCase() + n.slice(1)}</option>`)
      .join('');
    ['theme-select', 'cfg-theme'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = opts;
    });
  } catch { /* offline — keep default */ }
}

function setTheme(name) {
  state.theme = name;
  localStorage.setItem('theme', name);
  applyTheme(name);
}

function applyTheme(name) {
  const link = document.getElementById('theme-link');
  if (link) link.href = `themes/${name}.css`;
  ['theme-select', 'cfg-theme'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.value !== name) el.value = name;
  });
}

// ── LANGUAGE ──────────────────────────────────────────
function setLang(l) {
  state.lang = l;
  localStorage.setItem('lang', l);
  applyLang(l);
}

function applyLang(l) {
  const d = I18N[l] || I18N.en;

  // text content
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    if (d[key] !== undefined) el.textContent = d[key];
  });

  // placeholders
  document.querySelectorAll('[data-i18n-ph]').forEach(el => {
    const key = el.dataset.i18nPh;
    if (d[key] !== undefined) el.placeholder = d[key];
  });

  // sync language selects (sidebar + settings tab)
  ['lang-select', 'cfg-lang'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.value !== l) el.value = l;
  });
}

function t(key) {
  return (I18N[state.lang] || I18N.en)[key] || key;
}

// ── MODELS ────────────────────────────────────────────
async function loadModels() {
  try {
    const res = await fetch('/v1/models');
    const data = await res.json();
    const items = data.data || [];
    const sel = document.getElementById('g-model');

    if (!items.length) {
      sel.innerHTML = '<option value="">— no models —</option>';
      return;
    }

    const groups = {};
    for (const m of items) {
      const g = m.group || 'local';
      if (!groups[g]) groups[g] = [];
      const label = m.id.includes('@') ? m.id.split('@')[0] : m.id;
      groups[g].push({ id: m.id, label });
    }

    const keys = Object.keys(groups);
    if (keys.length === 1) {
      sel.innerHTML = groups[keys[0]]
        .map(m => `<option value="${m.id}">${m.label}</option>`).join('');
    } else {
      sel.innerHTML = keys.map(g =>
        `<optgroup label="${g}">${groups[g]
          .map(m => `<option value="${m.id}">${m.label}</option>`).join('')}</optgroup>`
      ).join('');
    }

    const allIds = items.map(m => m.id);
    if (state.model && allIds.includes(state.model)) {
      sel.value = state.model;
    } else {
      state.model = allIds[0];
      sel.value = allIds[0];
    }
  } catch (e) {
    document.getElementById('g-model').innerHTML = '<option value="">— offline —</option>';
  }
}

function onModelChange() {
  state.model = document.getElementById('g-model').value;
  localStorage.setItem('model', state.model);
}

function getModel() {
  return document.getElementById('g-model').value || state.model;
}

// ── STATS POPUP ──────────────────────────────────────
async function toggleStatsPopup() {
  const popup = document.getElementById('stats-popup');
  if (popup.style.display !== 'none') {
    popup.style.display = 'none';
    return;
  }
  try {
    const res = await fetch('/vyrii/stats');
    const data = await res.json();
    const rows = (data.stats || []);
    if (!rows.length) {
      popup.innerHTML = `<div class="stats-empty">${t('stats_title')}: —</div>`;
    } else {
      const hdr = `<tr><th>${t('stats_host')}</th><th>${t('stats_active')}</th><th>1m</th><th>5m</th><th>15m</th><th></th></tr>`;
      const body = rows.map(r => {
        const busy = r.active > 0;
        const badge = busy
          ? `<span class="stats-badge busy">${t('stats_busy')}</span>`
          : `<span class="stats-badge idle">${t('stats_idle')}</span>`;
        return `<tr><td>${r.host}</td><td>${r.active}</td><td>${r.req_1m}</td><td>${r.req_5m}</td><td>${r.req_15m}</td><td>${badge}</td></tr>`;
      }).join('');
      popup.innerHTML = `<div class="stats-header">${t('stats_title')}</div><table class="stats-table"><thead>${hdr}</thead><tbody>${body}</tbody></table>`;
    }
    popup.style.display = 'block';
  } catch (e) {
    popup.innerHTML = `<div class="stats-empty">${t('error_prefix')}${e.message}</div>`;
    popup.style.display = 'block';
  }
}

document.addEventListener('click', (e) => {
  const popup = document.getElementById('stats-popup');
  if (popup && popup.style.display !== 'none' &&
      !popup.contains(e.target) && !e.target.classList.contains('sf-stats-btn')) {
    popup.style.display = 'none';
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const popup = document.getElementById('stats-popup');
    if (popup) popup.style.display = 'none';
  }
});

// ── LOCK / RESERVE ───────────────────────────────────
function _currentHost() {
  const model = getModel();
  if (model.includes('@')) {
    const rest = model.split('@')[1];
    const m = rest.match(/(?:ollama|openai):\/\/(.+)/);
    return m ? m[1] : '';
  }
  return '';
}

async function toggleLock() {
  const host = _currentHost();
  if (!host) { showToast(t('lock_no_remote')); return; }
  try {
    const info = await (await fetch('/vyrii/lock')).json();
    const cur = (info.locks || {})[host];
    const action = cur ? 'release' : 'lock';
    const res = await fetch('/vyrii/lock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host, action }),
    });
    const data = await res.json();
    const btn = document.getElementById('lock-btn');
    if (action === 'lock') {
      if (data.ok) {
        if (btn) btn.innerHTML = '&#x1F512;';
        showToast(t('lock_btn_lock') + ': ' + host);
      } else {
        showToast(data.error || t('lock_busy'));
      }
    } else {
      if (btn) btn.innerHTML = '&#x1F513;';
      showToast(t('lock_btn_release') + ': ' + host);
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── ACTIVE PROFILE (settings) ────────────────────────
async function loadProfileOptions() {
  const sel = document.getElementById('cfg-active-profile');
  if (!sel) return;
  try {
    const res = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    const profiles = data.profiles || [];
    sel.innerHTML = `<option value="">${t('no_profile')}</option>` +
      profiles.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
  } catch { /* keep default option */ }
}

// ── TAB NAVIGATION ────────────────────────────────────
function setupTabNav() {
  document.querySelectorAll('.nav-item[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

  const navBtn = document.querySelector(`.nav-item[data-tab="${tab}"]`);
  const panel  = document.getElementById(`tab-${tab}`);
  if (navBtn) navBtn.classList.add('active');
  if (panel)  panel.classList.add('active');

  state.activeTab = tab;

  if (tab === 'files' && !state.filesLoaded) {
    state.filesLoaded = true;
    refreshFiles();
  }
  if (tab === 'rag')      ragRefreshProjects();
  if (tab === 'settings') loadSettings();
  if (tab === 'profile')   profileLoad();
  if (tab === 'team')      teamLoadProfiles();
  if (tab === 'scheduler') schRefresh();
  if (tab === 'projects')  projRefresh();
  if (tab === 'simargl')   loadProjectSelects();
  if (tab === 'svitovyd')  loadProjectSelects();
  if (tab === 'prompts')   prmRefresh();
}

// ── MARKDOWN RENDERER ─────────────────────────────────

function md(text) {
  if (!text) return '';

  // Handle <think>...</think> blocks (chain-of-thought from Qwen/DeepSeek)
  if (state.showThinking) {
    text = text.replace(/<think>([\s\S]*?)<\/think>/gi,
      (_, inner) => `\n<details class="thinking-block" open><summary>${t('thinking_label')}</summary>\n\n${inner.trim()}\n\n</details>\n`);
  } else if (state.streaming && /<think>(?![\s\S]*<\/think>)/i.test(text)) {
    text = text.replace(/<think>([\s\S]*)$/i,
      (_, inner) => `\n<details class="thinking-block" open><summary>${t('thinking_label')}</summary>\n\n${inner.trim()}\n\n</details>\n`);
  } else {
    text = text.replace(/<think>[\s\S]*?<\/think>/gi, '');
  }

  // Step 0 — extract math blocks before anything else (LaTeX / KaTeX)
  const mathBlocks = [];
  // display math: $$ ... $$ or \[ ... \]
  let s = text.replace(/\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]/g, (m, a, b) => {
    const idx = mathBlocks.length;
    mathBlocks.push({ display: true, tex: (a ?? b).trim() });
    return `\x00MB${idx}\x00`;
  });
  // inline math: $ ... $ (not $$) or \( ... \)
  s = s.replace(/\$([^\$\n]+?)\$|\\\((.+?)\\\)/g, (m, a, b) => {
    const idx = mathBlocks.length;
    mathBlocks.push({ display: false, tex: (a ?? b).trim() });
    return `\x00MB${idx}\x00`;
  });

  // Step 1a — extract mermaid blocks
  const mermaidBlocks = [];
  s = s.replace(/```mermaid\n?([\s\S]*?)```/g, (_, c) => {
    const idx = mermaidBlocks.length;
    mermaidBlocks.push(c.trim());
    return `\x00MM${idx}\x00`;
  });

  // Step 1b — extract pipe tables (| col | col | with --- separator)
  const tableBlocks = [];
  s = s.replace(/(^\|.+\|[ \t]*\n\|[ \t]*[-:]+[-| :\t]*\n(?:\|.+\|[ \t]*\n?)+)/gm, (m) => {
    const idx = tableBlocks.length;
    tableBlocks.push(m.trim());
    return `\x00TB${idx}\x00`;
  });

  // Step 1c — extract remaining code blocks before escaping
  const codeBlocks = [];
  s = s.replace(/```[\w]*\n?([\s\S]*?)```/g, (_, c) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code>${escHtml(c.trim())}</code></pre>`);
    return `\x00CB${idx}\x00`;
  });

  // Step 2 — escape ALL HTML entities in remaining text (prevents <tag> injection)
  s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Step 3 — inline code
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${escHtml(c)}</code>`);

  // Step 4 — bold / italic
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g,     '<em>$1</em>');

  // Step 5 — headings
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

  // Step 6 — lists
  s = s.replace(/^[*-] (.+)$/gm,    '<li>$1</li>');
  s = s.replace(/^\d+\. (.+)$/gm,   '<li>$1</li>');
  s = s.replace(/(<li>[\s\S]*?<\/li>\n?)+/g, m => `<ul>${m}</ul>`);

  // Step 7 — paragraphs (double newline)
  s = s.split(/\n{2,}/)
    .map(para => para.startsWith('<') ? para : `<p>${para.replace(/\n/g, '<br>')}</p>`)
    .join('');

  // Step 8 — restore code blocks
  s = s.replace(/\x00CB(\d+)\x00/g, (_, i) => codeBlocks[+i]);

  // Step 8b — restore table blocks as HTML tables
  s = s.replace(/\x00TB(\d+)\x00/g, (_, i) => {
    const raw = tableBlocks[+i];
    const lines = raw.split('\n').filter(l => l.trim());
    if (lines.length < 2) return escHtml(raw);
    const parseRow = (line) => line.split('|').slice(1, -1).map(c => c.trim());
    const headers = parseRow(lines[0]);
    const aligns = parseRow(lines[1]).map(c => {
      if (c.startsWith(':') && c.endsWith(':')) return 'center';
      if (c.endsWith(':')) return 'right';
      return 'left';
    });
    let h = '<table class="md-table"><thead><tr>';
    headers.forEach((hd, j) => {
      h += `<th style="text-align:${aligns[j] || 'left'}">${escHtml(hd)}</th>`;
    });
    h += '</tr></thead><tbody>';
    for (let r = 2; r < lines.length; r++) {
      const cells = parseRow(lines[r]);
      h += '<tr>';
      cells.forEach((c, j) => {
        h += `<td style="text-align:${aligns[j] || 'left'}">${escHtml(c)}</td>`;
      });
      h += '</tr>';
    }
    h += '</tbody></table>';
    return h;
  });

  // Step 9 — render math blocks via KaTeX
  s = s.replace(/\x00MB(\d+)\x00/g, (_, i) => {
    const mb = mathBlocks[+i];
    const encoded = mb.tex.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const tag = mb.display ? 'div' : 'span';
    try {
      const html = katex.renderToString(mb.tex, { displayMode: mb.display, throwOnError: false });
      return `<${tag} class="katex-wrap${mb.display ? ' katex-display-wrap' : ''}" data-tex="${encoded}">${html}<button class="katex-copy" onclick="copyKatexSrc(this)" title="Copy LaTeX">&#128203;</button></${tag}>`;
    } catch { return escHtml(mb.tex); }
  });

  // Step 10 — insert mermaid placeholders (rendered async after innerHTML)
  s = s.replace(/\x00MM(\d+)\x00/g, (_, i) => {
    const raw = mermaidBlocks[+i];
    const encoded = raw.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    return `<div class="mermaid-wrap"><button class="mermaid-copy" onclick="copyMermaidSrc(this)" title="Copy source">&#128203;</button><pre class="mermaid" data-src="${encoded}">${escHtml(raw)}</pre></div>`;
  });

  return s;
}
  
function copyMsgRaw(idx) {
  const msg = state.chatMessages[idx];
  if (!msg) return;
  navigator.clipboard.writeText(msg.content)
    .then(() => showToast(t('copied')))
    .catch(() => {
      const ta = document.createElement('textarea');
      ta.value = msg.content; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta);
      showToast(t('copied'));
    });
}

function copyMsgFmt(idx) {
  const el = document.querySelector(`#msg-${idx} .bubble`);
  if (!el) return;
  const html = el.innerHTML;
  const plain = el.innerText;
  if (navigator.clipboard && typeof ClipboardItem !== 'undefined') {
    const item = new ClipboardItem({
      'text/html':  new Blob([html],  { type: 'text/html' }),
      'text/plain': new Blob([plain], { type: 'text/plain' })
    });
    navigator.clipboard.write([item])
      .then(() => showToast(t('copied')))
      .catch(() => _fallbackCopyFmt(el));
  } else {
    _fallbackCopyFmt(el);
  }
}

function _fallbackCopyFmt(el) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges(); sel.addRange(range);
  document.execCommand('copy');
  sel.removeAllRanges();
  showToast(t('copied'));
}

function copyKatexSrc(btn) {
  const wrap = btn.parentElement;
  const src = wrap?.dataset.tex || '';
  navigator.clipboard.writeText(src).then(() => showToast(t('copied')));
}

function copyMermaidSrc(btn) {
  const pre = btn.parentElement.querySelector('pre.mermaid');
  const src = pre?.dataset.src || pre?.textContent || '';
  navigator.clipboard.writeText(src).then(() => showToast(t('copied')));
}

function retryMsg(idx) {
  const msg = state.chatMessages[idx];
  if (!msg || msg.role !== 'assistant' || state.streaming) return;
  state.chatMessages.splice(idx, 1);
  state.savedCount = Math.min(state.savedCount, idx);
  renderChatMessages();
  const prev = state.chatMessages[idx - 1];
  if (prev && prev.role === 'user') {
    document.getElementById('chat-input').value = prev.content;
    sendChat();
  }
}

function renderMermaid(container) {
  if (typeof mermaid === 'undefined') return;
  const nodes = (container || document).querySelectorAll('pre.mermaid:not([data-processed])');
  if (!nodes.length) return;
  try { mermaid.run({ nodes }); } catch {}
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── UI HELPERS ────────────────────────────────────────
function setResult(id, html, isHtml = false) {
  const el = document.getElementById(id);
  if (!el) return;
  if (isHtml) {
    el.innerHTML = html;
  } else {
    el.textContent = html;
  }
}

function setResultMd(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = md(text);
  renderMermaid(el);
}

function setResultLoading(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = `<span class="status-bar"><span class="status-dot"></span>${t('generating')}</span>`;
}

function showToast(msg, duration = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), duration);
}

function copyResult(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const text = el.innerText || el.textContent;
  navigator.clipboard.writeText(text).then(() => showToast(t('copied')));
}

function addToChat(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const text = (el.innerText || el.textContent).trim();
  if (!text) return;

  // derive label from the enclosing tab's title
  const panel = el.closest('.tab-panel');
  const title = panel ? (panel.querySelector('.ph-title')?.textContent.trim() || '') : '';
  const content = title ? `[${title}]\n${text}` : text;

  // inject as user + assistant pair directly into chat history (same as Gradio)
  state.chatMessages.push({ role: 'user',      content });
  state.chatMessages.push({ role: 'assistant', content: t('ctx_received') });

  switchTab('chat');
  renderChatMessages();
  const box = document.getElementById('chat-messages');
  if (box) box.scrollTop = box.scrollHeight;
  showToast(t('ctx_added'));
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

// ── CHAT ──────────────────────────────────────────────
function chatKeyDown(e) {
  if (e.key === 'Enter' && e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
  // Enter alone → new line (default textarea behaviour, no override needed)
}

function updateCtxIndicator() {
  const el = document.getElementById('ctx-indicator');
  if (!el) return;
  const total = state.chatMessages.reduce((s, m) => s + (m.content?.length || 0), 0);
  const tokens = Math.max(1, Math.round(total / 3));
  if (state.smartCtx) {
    let ctx = 2048;
    while (tokens >= ctx * 0.7) ctx += 2048;
    el.textContent = `~${tokens} / ${ctx} auto`;
  } else {
    el.textContent = `~${tokens} / ${state.fixedCtx} fixed`;
  }
  const inp = document.getElementById('ctx-fixed-input');
  if (inp) inp.style.display = state.smartCtx ? 'none' : '';
}

function toggleCtxMode() {
  state.smartCtx = !state.smartCtx;
  updateCtxIndicator();
}

function toggleIncognito(on) {
  state.incognito = on;
  const label = document.querySelector('.chk-incognito');
  if (label) label.style.color = on ? '#f5a623' : '';
  if (on && state.chatId) {
    fetch(`/vyrii/history/chats/${state.chatId}`, { method: 'DELETE' }).catch(() => {});
    state.chatId = null;
    state.savedCount = 0;
  }
}

function newChat() {
  state.chatMessages = [];
  state.chatId     = null;
  state.savedCount = 0;
  renderChatMessages();
  updateCtxIndicator();
}

function clearChat() { newChat(); }

async function compactChat() {
  if (state.chatMessages.length < 2) return;
  const model = getModel();
  if (!model) { showToast(t('no_model')); return; }
  const btn = document.getElementById('btn-compact');
  if (btn) btn.disabled = true;
  showToast(t('compacting'));
  try {
    const resp = await fetch('/vyrii/compact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: state.chatMessages, model }),
    });
    const data = await resp.json();
    if (!data.summary) { showToast(t('error_prefix') + (data.error || '?')); return; }
    // start new chat with compact summary
    state.chatMessages = [
      { role: 'user',      content: '[Compacted conversation summary]\n\n' + data.summary },
      { role: 'assistant', content: t('compacted_ok') },
    ];
    state.chatId     = null;
    state.savedCount = 0;
    renderChatMessages();
    updateCtxIndicator();
    showToast(t('compacted_ok'));
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── CHAT HISTORY ──────────────────────────────────────
function toggleHistory() {
  const panel = document.getElementById('chat-hist-panel');
  const opening = !panel.classList.contains('open');
  panel.classList.toggle('open');
  if (opening) {
    document.getElementById('chp-search').value = '';
    loadHistoryList('');
  }
}

let _histTimer = null;
function searchHistory(q) {
  clearTimeout(_histTimer);
  _histTimer = setTimeout(() => loadHistoryList(q), 280);
}

async function loadHistoryList(q = '') {
  const list = document.getElementById('chp-list');
  list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('loading')}</div>`;
  try {
    const url = q.trim()
      ? '/vyrii/history/search?q=' + encodeURIComponent(q.trim())
      : '/vyrii/history/chats';
    const data = await (await fetch(url)).json();
    if (!data.length) {
      list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('hist_empty')}</div>`;
      return;
    }
    list.innerHTML = data.map(ch => {
      const date = new Date(ch.created_at * 1000).toLocaleDateString();
      return `
        <div class="chp-item" id="chpi-${ch.id}">
          <div class="chp-item-body" onclick="loadHistoryChat(${ch.id})">
            <div class="chp-title">${escHtml(ch.title)}</div>
            <div class="chp-date">${date}</div>
          </div>
          <button class="btn btn-ghost btn-sm chp-del"
            onclick="deleteHistoryChat(${ch.id})" title="Delete">✕</button>
        </div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('error_prefix')}${e.message}</div>`;
  }
}

async function loadHistoryChat(chatId) {
  try {
    const data = await (await fetch('/vyrii/history/chats/' + chatId)).json();
    if (data.error) { showToast(data.error); return; }
    state.chatMessages = data.messages || [];
    state.chatId       = chatId;
    state.savedCount   = state.chatMessages.length;
    renderChatMessages();
    updateCtxIndicator();
    document.getElementById('chat-hist-panel').classList.remove('open');
    const box = document.getElementById('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function deleteHistoryChat(chatId) {
  try {
    await fetch('/vyrii/history/chats/' + chatId, { method: 'DELETE' });
    document.getElementById('chpi-' + chatId)?.remove();
    const list = document.getElementById('chp-list');
    if (list && !list.querySelector('.chp-item')) {
      list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('hist_empty')}</div>`;
    }
    if (state.chatId === chatId) newChat();
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

function renderChatMessages() {
  const container = document.getElementById('chat-messages');
  if (!state.chatMessages.length) {
    container.innerHTML = `<div class="placeholder-text" style="text-align:center;padding:40px 0">${t('chat_empty')}</div>`;
    return;
  }
  container.innerHTML = state.chatMessages.map((msg, i) => {
    const isUser = msg.role === 'user';
    const avatar  = isUser ? '👤' : '🤖';
    const cls     = isUser ? 'user' : 'asst';
    const name    = isUser ? 'You'  : 'Assistant';
    const content = md(msg.content);
    const cursor  = (!isUser && i === state.chatMessages.length - 1 && state.streaming)
      ? '<span class="cursor-blink"></span>' : '';
    return `
      <div class="msg-row ${cls}" id="msg-${i}">
        <div class="msg-avatar ${isUser ? 'usr' : 'bot'}">${avatar}</div>
        <div class="msg-wrap">
          <span class="msg-name">${name}</span>
          <div class="bubble">${content}${cursor}</div>
          <div class="msg-copy-group">
            <button class="msg-copy" onclick="copyMsgRaw(${i})" title="${t('copy_raw')}">MD</button>
            <button class="msg-copy" onclick="copyMsgFmt(${i})" title="${t('copy_fmt')}">&#128203;</button>${
              !isUser ? `<button class="msg-copy msg-retry" onclick="retryMsg(${i})" title="${t('retry_msg')}">&#x21bb;</button>` : ''}
          </div>
        </div>
      </div>`;
  }).join('');
  renderMermaid(container);
  container.scrollTop = container.scrollHeight;
}

function updateLastBubble() {
  const msgs = state.chatMessages;
  if (!msgs.length) return;
  const last = msgs[msgs.length - 1];
  const i    = msgs.length - 1;
  const el   = document.getElementById(`msg-${i}`);
  if (!el) { renderChatMessages(); return; }
  const bubble = el.querySelector('.bubble');
  if (!bubble) return;
  bubble.innerHTML = md(last.content) + (state.streaming ? '<span class="cursor-blink"></span>' : '');
  if (!state.streaming) renderMermaid(bubble);
  const container = document.getElementById('chat-messages');
  container.scrollTop = container.scrollHeight;
}

// ── HISTORY SAVE HELPERS ──────────────────────────────
async function _histEnsureChat(title) {
  if (state.chatId) return;
  try {
    const res = await fetch('/vyrii/history/chats', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title.slice(0, 50) }),
    });
    state.chatId = (await res.json()).id ?? null;
  } catch { /* offline or API-only mode without DB */ }
}

async function _histSaveMsg(role, content) {
  if (!state.chatId || !content) return;
  try {
    await fetch(`/vyrii/history/chats/${state.chatId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, content }),
    });
  } catch { /* best-effort */ }
}

function _setChatBusy(busy) {
  document.getElementById('chat-status').style.display = busy ? 'flex' : 'none';
  document.getElementById('chat-send').style.display   = busy ? 'none' : '';
  document.getElementById('chat-stop').style.display   = busy ? ''     : 'none';
}

function interruptChat() {
  if (state.abortCtrl) state.abortCtrl.abort();
}

async function sendChat() {
  if (state.streaming) return;
  const input = document.getElementById('chat-input');
  const text  = input.value.trim();
  if (!text) return;

  const model = getModel();
  if (!model) { showToast(t('no_model')); return; }

  input.value = '';
  input.style.height = 'auto';

  state.chatMessages.push({ role: 'user', content: text });
  state.chatMessages.push({ role: 'assistant', content: '' });
  state.streaming  = true;
  state.abortCtrl  = new AbortController();
  renderChatMessages();
  _setChatBusy(true);

  if (!state.incognito) {
    await _histEnsureChat(text);
    const saveUpTo = state.chatMessages.length - 1;
    for (let i = state.savedCount; i < saveUpTo; i++) {
      await _histSaveMsg(state.chatMessages[i].role, state.chatMessages[i].content);
    }
    state.savedCount = saveUpTo;
  }

  // messages to send: all except the last empty assistant placeholder
  const toSend = state.chatMessages.slice(0, -1);

  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, messages: toSend, stream: true,
        ...(state.smartCtx ? {} : { num_ctx: state.fixedCtx }) }),
      signal: state.abortCtrl.signal,
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') break;
        try {
          const obj   = JSON.parse(raw);
          if (obj.waiting) {
            state.chatMessages[state.chatMessages.length - 1].content =
              `⏳ ${t('queue_waiting')} (${obj.position})`;
            state._wasWaiting = true;
            updateLastBubble();
            continue;
          }
          const chunk = obj.choices?.[0]?.delta?.content ?? '';
          if (chunk) {
            if (state._wasWaiting) {
              state.chatMessages[state.chatMessages.length - 1].content = '';
              state._wasWaiting = false;
            }
            state.chatMessages[state.chatMessages.length - 1].content += chunk;
            updateLastBubble();
          }
        } catch { /* ignore malformed SSE line */ }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      state.chatMessages[state.chatMessages.length - 1].content = `${t('error_prefix')}${e.message}`;
    }
    // AbortError: keep whatever was generated so far
  } finally {
    state.streaming = false;
    state.abortCtrl = null;
    _setChatBusy(false);
    updateLastBubble();
    const last = state.chatMessages[state.chatMessages.length - 1];
    if (!state.incognito && last && last.role === 'assistant' && last.content) {
      await _histSaveMsg('assistant', last.content);
      state.savedCount = state.chatMessages.length;
    }
    updateCtxIndicator();
  }
}

// ── TRANSLATE ─────────────────────────────────────────
function swapLangs() {
  const from = document.getElementById('tr-from');
  const to   = document.getElementById('tr-to');
  [from.value, to.value] = [to.value, from.value];
}

async function runTranslate() {
  const text = document.getElementById('tr-input').value.trim();
  if (!text) return;
  setResultLoading('tr-result');
  try {
    const res = await fetch('/vyrii/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        from_lang: document.getElementById('tr-from').value,
        to_lang:   document.getElementById('tr-to').value,
        mode:      document.getElementById('tr-mode').value,
        model:     getModel(),
      }),
    });
    const data = await res.json();
    setResult('tr-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('tr-result', t('error_prefix') + e.message);
  }
}

// ── WEBASK ────────────────────────────────────────────
async function runWebAsk() {
  const question = document.getElementById('wa-question').value.trim();
  if (!question) return;
  setResultLoading('wa-result');
  try {
    const res = await fetch('/vyrii/webask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        url:   document.getElementById('wa-url').value.trim(),
        top_n: +document.getElementById('wa-n').value,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResultMd('wa-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wa-result', t('error_prefix') + e.message);
  }
}

// ── WEBCRAWL ──────────────────────────────────────────
function wcUpdateVisibility() {
  const mode   = document.getElementById('wc-mode').value;
  const filter = document.getElementById('wc-filter').value;
  const needTask    = mode === 'llm' || filter === 'llm';
  const needFormat  = mode === 'llm';
  const needColumns = mode === 'extract' || mode === 'llm';
  const needPrefix  = filter === 'url-prefix';
  document.getElementById('wc-task-wrap').style.display    = needTask    ? 'flex' : 'none';
  document.getElementById('wc-format-wrap').style.display  = needFormat  ? 'flex' : 'none';
  document.getElementById('wc-columns-wrap').style.display = needColumns ? 'flex' : 'none';
  document.getElementById('wc-prefix-wrap').style.display  = needPrefix  ? 'flex' : 'none';
}

async function runWebCrawl() {
  const url = document.getElementById('wc-url').value.trim();
  if (!url) return;
  setResultLoading('wc-result');
  const format = document.querySelector('input[name="wc-format"]:checked')?.value || 'log';
  try {
    const res = await fetch('/vyrii/webcrawl', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        mode:       document.getElementById('wc-mode').value,
        filter:     document.getElementById('wc-filter').value,
        url_prefix: document.getElementById('wc-prefix').value.trim(),
        path:       document.getElementById('wc-path').value.trim(),
        depth:      +document.getElementById('wc-depth').value,
        max_pages:  +document.getElementById('wc-pages').value,
        task:       document.getElementById('wc-task').value.trim(),
        format_out: format,
        ask:        document.getElementById('wc-ask').checked,
        columns:    document.getElementById('wc-columns').value.trim(),
        model:      getModel(),
      }),
    });
    const data = await res.json();
    setResultMd('wc-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wc-result', t('error_prefix') + e.message);
  }
}

// ── WEBANALYS ─────────────────────────────────────────
async function runWebAnalys() {
  const query = document.getElementById('wan-query').value.trim();
  if (!query) return;
  setResultLoading('wan-result');
  try {
    const res = await fetch('/vyrii/webanalys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        n:     +document.getElementById('wan-n').value,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResultMd('wan-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wan-result', t('error_prefix') + e.message);
  }
}

// ── DEEPAGENT ─────────────────────────────────────────
function daWebToggle() {
  const on = document.getElementById('da-web').checked;
  document.getElementById('da-web-n-wrap').style.display = on ? 'flex' : 'none';
}

function daRagToggle() {
  const on  = document.getElementById('da-rag-chk').checked;
  const wrap = document.getElementById('da-rag-wrap');
  wrap.style.display = on ? 'flex' : 'none';
  if (on) daRagRefresh();
}

async function daRagRefresh() {
  const sel = document.getElementById('da-rag-project');
  try {
    const res  = await fetch('/vyrii/rag/projects');
    const data = await res.json();
    const projects = data.projects || [];
    const cur = sel.value;
    sel.innerHTML = `<option value="">${t('rag_select_project')}</option>`
      + projects.map(p => `<option value="${p}"${p === cur ? ' selected' : ''}>${p}</option>`).join('');
  } catch { /* ignore */ }
}

async function runDeepAgent() {
  const task = document.getElementById('da-task').value.trim();
  if (!task) return;

  const useTeam    = document.getElementById('da-team-chk').checked;
  const teamProfile = useTeam ? document.getElementById('da-team-profile').value : '';

  if (useTeam && teamProfile) {
    setResultLoading('da-result');
    const resultEl = document.getElementById('da-result');
    try {
      const res = await fetch('/vyrii/team/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_name: teamProfile,
          query:        task,
          aspects:      [],
          combine:      document.getElementById('da-team-combine').value,
          ctx_mode:     'none',
          model:        getModel(),
          num_ctx:      4096,
          timeout:      300,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await _readTeamSSE(res, 'da-result');
    } catch (e) {
      setResult('da-result', t('error_prefix') + e.message);
    }
    return;
  }

  setResultLoading('da-result');
  const useWeb = document.getElementById('da-web').checked;
  const useRag = document.getElementById('da-rag-chk').checked;
  try {
    const res = await fetch('/vyrii/deepagent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task,
        ref_url:     document.getElementById('da-ref').value.trim(),
        sections:    +document.getElementById('da-sections').value,
        model:       getModel(),
        use_web:     useWeb,
        web_n:       useWeb ? +document.getElementById('da-web-n').value : 3,
        rag_project: useRag ? document.getElementById('da-rag-project').value : '',
      }),
    });
    const data = await res.json();
    setResultMd('da-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('da-result', t('error_prefix') + e.message);
  }
}

// ── INTERVIEW ─────────────────────────────────────────
let ivQuestions = [];
let ivAnswers   = [];

async function runInterview() {
  const task = (document.getElementById('iv-task')?.value || '').trim();
  const n    = +(document.getElementById('iv-n')?.value || 5);
  if (!task) { showToast(t('iv_no_task') || 'Enter a task description'); return; }

  ivQuestions = []; ivAnswers = [];
  const qbox = document.getElementById('iv-questions');
  const acts = document.getElementById('iv-actions');
  if (qbox) qbox.innerHTML = `<span class="placeholder-text">${t('generating') || 'Generating…'}</span>`;
  if (acts) acts.style.display = 'none';

  try {
    const res  = await fetch('/vyrii/interview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, n, model: getModel() }),
    });
    const data = await res.json();
    if (data.error) { if (qbox) qbox.innerHTML = `<span class="error">${data.error}</span>`; return; }
    ivQuestions = data.questions || [];
    renderInterviewQuestions();
  } catch (e) {
    if (qbox) qbox.innerHTML = `<span class="error">${e.message}</span>`;
  }
}

function renderInterviewQuestions() {
  const container = document.getElementById('iv-questions');
  if (!container) return;
  container.innerHTML = '';
  ivAnswers = new Array(ivQuestions.length).fill(null);

  ivQuestions.forEach((q, i) => {
    const div = document.createElement('div');
    div.style.cssText = 'margin:12px 0;padding:12px;border:1px solid var(--border-color,#e0e0e0);border-radius:8px';

    const label = document.createElement('p');
    label.style.cssText = 'margin:0 0 8px;font-weight:600';
    label.textContent = `${i + 1}. ${q.q}`;
    div.appendChild(label);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px';
    (q.options || []).forEach(opt => {
      const btn = document.createElement('button');
      btn.className = 'btn btn-ghost btn-sm iv-opt';
      btn.textContent = opt;
      btn.onclick = () => {
        div.querySelectorAll('.iv-opt').forEach(b => {
          b.classList.remove('btn-primary');
          b.classList.add('btn-ghost');
        });
        btn.classList.remove('btn-ghost');
        btn.classList.add('btn-primary');
        ivAnswers[i] = opt;
        checkAllAnswered();
      };
      btnRow.appendChild(btn);
    });
    div.appendChild(btnRow);

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'form-control';
    inp.placeholder = t('iv_other') || 'Other…';
    inp.style.marginTop = '4px';
    inp.oninput = () => {
      if (inp.value.trim()) {
        div.querySelectorAll('.iv-opt').forEach(b => {
          b.classList.remove('btn-primary');
          b.classList.add('btn-ghost');
        });
        ivAnswers[i] = inp.value.trim();
        checkAllAnswered();
      }
    };
    div.appendChild(inp);
    container.appendChild(div);
  });
}

function checkAllAnswered() {
  const acts = document.getElementById('iv-actions');
  if (!acts) return;
  if (ivAnswers.length > 0 && ivAnswers.every(a => a !== null && a !== ''))
    acts.style.display = '';
}

function _formatInterviewText() {
  const task = (document.getElementById('iv-task')?.value || '').trim();
  let text = `Task: ${task}\n\n`;
  ivQuestions.forEach((q, i) => {
    text += `Q${i + 1}: ${q.q}\nAnswer: ${ivAnswers[i] ?? ''}\n\n`;
  });
  return text.trim();
}

function addInterviewToChat() {
  const text = _formatInterviewText();
  if (!text) return;
  state.chatMessages.push({ role: 'user',      content: `[Interview]\n${text}` });
  state.chatMessages.push({ role: 'assistant', content: t('ctx_received') });
  switchTab('chat');
  renderChatMessages();
  const box = document.getElementById('chat-messages');
  if (box) box.scrollTop = box.scrollHeight;
  showToast(t('ctx_added'));
}

function copyInterview() {
  const text = _formatInterviewText();
  navigator.clipboard.writeText(text).then(() => showToast(t('copied')));
}

// ── SCAN ──────────────────────────────────────────────
async function runScan() {
  const path = document.getElementById('sc-path').value.trim();
  if (!path) return;
  setResultLoading('sc-result');
  try {
    const res = await fetch('/vyrii/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path,
        query:   document.getElementById('sc-query').value.trim(),
        chunk:   +document.getElementById('sc-chunk').value,
        summary: +document.getElementById('sc-summary').value,
        target:  +document.getElementById('sc-target').value,
        rounds:  +document.getElementById('sc-rounds').value,
        ext:     document.getElementById('sc-ext').value.trim(),
        model:   getModel(),
      }),
    });
    const data = await res.json();
    setResult('sc-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('sc-result', t('error_prefix') + e.message);
  }
}

// ── WEBINDEX ──────────────────────────────────────────
async function runWebIndex() {
  const url = document.getElementById('wi-url').value.trim();
  if (!url) return;
  setResultLoading('wi-result');
  try {
    const res = await fetch('/vyrii/webindex', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        project: document.getElementById('wi-project').value.trim(),
        path:    document.getElementById('wi-path').value.trim(),
        depth:   +document.getElementById('wi-depth').value,
        pages:   +document.getElementById('wi-pages').value,
        model:   getModel(),
      }),
    });
    const data = await res.json();
    setResult('wi-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wi-result', t('error_prefix') + e.message);
  }
}

// ── OBFUSCATE ─────────────────────────────────────────
async function runObfuscate() {
  const text     = document.getElementById('of-input').value.trim();
  const glossary = document.getElementById('of-glossary').value.trim();
  if (!text || !glossary) { showToast('Text and glossary name required'); return; }
  setResultLoading('of-result');
  try {
    const res = await fetch('/vyrii/obfuscate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text, glossary,
        force: document.getElementById('of-force').checked,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResult('of-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('of-result', t('error_prefix') + e.message);
  }
}

async function runDeobfuscate() {
  const text     = document.getElementById('dof-input').value.trim();
  const glossary = document.getElementById('dof-glossary').value.trim();
  if (!text || !glossary) { showToast('Text and glossary name required'); return; }
  setResultLoading('dof-result');
  try {
    const res = await fetch('/vyrii/deobfuscate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text, glossary,
        force: document.getElementById('dof-force').checked,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResult('dof-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('dof-result', t('error_prefix') + e.message);
  }
}

// ── FILES ─────────────────────────────────────────────
state.filesLoaded = false;
state.currentFilePath = '';

async function refreshFiles() {
  const tree = document.getElementById('files-tree');
  tree.innerHTML = `<div class="placeholder-text">${t('loading')}</div>`;
  try {
    const res  = await fetch('/vyrii/files/list');
    const data = await res.json();
    if (data.error) { tree.innerHTML = `<div class="placeholder-text">${data.error}</div>`; return; }
    tree.innerHTML = '';
    tree.appendChild(buildTree(data.tree || {}, ''));
  } catch (e) {
    tree.innerHTML = `<div class="placeholder-text">${t('error_prefix')}${e.message}</div>`;
  }
}

function buildTree(node, basePath) {
  const ul = document.createElement('ul');
  ul.className = 'tree-list';
  for (const [name, children] of Object.entries(node || {})) {
    const li   = document.createElement('li');
    const isDir = name.endsWith('/');
    const item  = document.createElement('div');
    item.className = 'tree-item';
    const path  = basePath + name;

    if (isDir) {
      item.innerHTML = `<span class="item-icon">📁</span><span class="item-name">${name.slice(0,-1)}/</span>`;
      const sub = buildTree(children, path);
      sub.style.display = 'none';
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
        item.querySelector('.item-icon').textContent = sub.style.display === 'none' ? '📁' : '📂';
        showFileInfo({ name: name.slice(0,-1), path, type: 'directory' });
      });
      li.appendChild(item);
      li.appendChild(sub);
    } else {
      item.innerHTML = `<span class="item-icon">${fileIcon(name)}</span><span class="item-name">${name}</span>`;
      item.addEventListener('click', () => {
        document.querySelectorAll('.tree-item').forEach(i => i.classList.remove('selected'));
        item.classList.add('selected');
        showFileInfo({ name, path, type: 'file' });
      });
      li.appendChild(item);
    }
    ul.appendChild(li);
  }
  return ul;
}

function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const icons = { py:'🐍', js:'📜', ts:'📜', html:'🌐', css:'🎨', md:'📝',
    txt:'📄', json:'📋', yaml:'📋', yml:'📋', sh:'⚙️', bat:'⚙️',
    png:'🖼️', jpg:'🖼️', jpeg:'🖼️', gif:'🖼️', svg:'🖼️',
    pdf:'📑', zip:'🗜️', gz:'🗜️', tar:'🗜️' };
  return icons[ext] || '📄';
}

function showFileInfo(info) {
  const preview = document.getElementById('files-preview');
  state.currentFilePath = info.path;
  const isFile = info.type === 'file';
  const escapedPath = info.path.replace(/'/g, "\\'");

  const actionBtns = isFile
    ? `<button class="btn btn-ghost btn-sm" onclick="viewFile('${escapedPath}')" data-i18n="view">View</button>
       <button class="btn btn-ghost btn-sm" onclick="fileScan('${escapedPath}')" data-i18n="scan_btn">Scan</button>`
    : `<button class="btn btn-ghost btn-sm" onclick="fileIndex('${escapedPath}')" data-i18n="index_btn">Index</button>
       <button class="btn btn-ghost btn-sm" onclick="fileScan('${escapedPath}')" data-i18n="scan_btn">Scan</button>`;

  preview.innerHTML = `
    <div class="file-info-box">
      <h3>${escHtml(info.name)}</h3>
      <div class="fi-row"><span class="fi-label">Type</span><span>${info.type}</span></div>
      <div class="fi-row"><span class="fi-label">Path</span>
        <span style="word-break:break-all;font-size:12px">${escHtml(info.path)}</span></div>
    </div>
    <div class="form-actions">
      ${actionBtns}
      <button class="btn btn-danger btn-sm" onclick="deleteFile('${escapedPath}')" data-i18n="delete_btn">Delete</button>
    </div>
    <div id="file-op-result" style="display:none" class="result-box" style="flex:1"></div>
    <div id="file-content-wrap" style="display:none">
      <div style="display:flex;align-items:center;gap:8px;margin-top:8px">
        <label class="form-label" style="margin:0" id="file-content-label"></label>
        <div id="file-view-toggle" style="display:none;margin-left:auto;gap:4px;display:none">
          <button id="fv-raw"      class="btn btn-ghost btn-sm fv-btn active" onclick="_fileViewMode('raw')">Raw</button>
          <button id="fv-rendered" class="btn btn-ghost btn-sm fv-btn"        onclick="_fileViewMode('rendered')">Rendered</button>
        </div>
      </div>
      <div id="file-content-box" class="file-raw"></div>
      <div id="file-truncated-note" style="display:none;font-size:11px;color:var(--text-muted);margin-top:4px">
        ⚠ File truncated at 64 KB
      </div>
    </div>`;
  applyLang(state.lang);
}

async function viewFile(path) {
  const wrap   = document.getElementById('file-content-wrap');
  const box    = document.getElementById('file-content-box');
  const lbl    = document.getElementById('file-content-label');
  const note   = document.getElementById('file-truncated-note');
  const toggle = document.getElementById('file-view-toggle');
  wrap.style.display  = 'block';
  box.className       = 'file-raw';
  box.textContent     = 'Loading…';
  lbl.textContent     = path.split('/').pop() || path;
  toggle.style.display = 'none';
  try {
    const res  = await fetch('/vyrii/files/read?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (data.error) { box.textContent = data.error; return; }
    const content = data.content || '';
    state.fileViewRaw  = content;
    note.style.display = data.truncated ? 'block' : 'none';
    const ext = path.split('.').pop().toLowerCase();
    const canRender = ['md', 'markdown', 'html', 'htm'].includes(ext);
    if (canRender) {
      toggle.style.display = 'flex';
      _fileViewMode('rendered');
    } else {
      toggle.style.display = 'none';
      _fileViewMode('raw');
    }
  } catch (e) {
    box.textContent = t('error_prefix') + e.message;
  }
}

const _HLJS_LANG = {
  py:'python', js:'javascript', ts:'typescript', jsx:'javascript', tsx:'typescript',
  java:'java', rs:'rust', go:'go', rb:'ruby', php:'php', cs:'csharp', kt:'kotlin',
  swift:'swift', cpp:'cpp', c:'c', h:'c', r:'r',
  sh:'bash', bash:'bash', zsh:'bash',
  css:'css', scss:'css', html:'html', htm:'html', xml:'xml',
  json:'json', yaml:'yaml', yml:'yaml', toml:'toml', sql:'sql', md:'markdown',
};

function _fileViewMode(mode) {
  const box = document.getElementById('file-content-box');
  if (!box) return;
  const raw  = state.fileViewRaw || '';
  const path = state.currentFilePath || '';
  const ext  = path.split('.').pop().toLowerCase();

  document.querySelectorAll('.fv-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('fv-' + mode)?.classList.add('active');

  if (mode === 'raw') {
    box.className  = 'file-raw';
    box.style.cssText = '';
    // syntax highlighting + line numbers via hljs
    if (typeof hljs !== 'undefined' && raw) {
      const lang = _HLJS_LANG[ext] || '';
      let highlighted;
      try {
        highlighted = (lang && hljs.getLanguage(lang))
          ? hljs.highlight(raw, { language: lang, ignoreIllegals: true }).value
          : hljs.highlightAuto(raw).value;
      } catch { highlighted = escHtml(raw); }
      const lines = highlighted.split('\n');
      const rows  = lines.map((line, i) =>
        `<span class="code-line"><span class="code-ln">${i + 1}</span><span>${line || ' '}</span></span>`
      ).join('\n');
      box.innerHTML = `<code class="hljs" style="display:table;width:100%">${rows}</code>`;
    } else {
      box.textContent = raw || '(empty)';
    }
  } else {
    if (ext === 'md' || ext === 'markdown') {
      box.className = 'result-box';
      box.style.maxHeight = '55vh';
      box.style.overflow  = 'auto';
      box.innerHTML = md(raw);
      renderMermaid(box);
    } else if (ext === 'html' || ext === 'htm') {
      box.className = '';
      box.style.cssText = 'width:100%;margin-top:6px';
      const iframe = document.createElement('iframe');
      iframe.setAttribute('sandbox', 'allow-forms');
      iframe.style.cssText = 'width:100%;height:55vh;border:1px solid var(--border);border-radius:8px;background:#fff;display:block';
      iframe.srcdoc = raw;
      box.innerHTML = '';
      box.appendChild(iframe);
    }
  }
}

async function fileIndex(path) {
  const result = document.getElementById('file-op-result');
  result.style.display = 'block';
  result.innerHTML = `<span class="status-bar"><span class="status-dot"></span>Indexing…</span>`;
  try {
    const res  = await fetch('/vyrii/files/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (data.ok) {
      result.textContent = `Index OK — project "${data.project}"`;
    } else {
      result.textContent = data.error || t('api_error');
    }
  } catch (e) {
    result.textContent = t('error_prefix') + e.message;
  }
}

async function fileScan(path) {
  const result = document.getElementById('file-op-result');
  result.style.display = 'block';
  result.innerHTML = `<span class="status-bar"><span class="status-dot"></span>Scanning…</span>`;
  try {
    const res  = await fetch('/vyrii/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path,
        query: '', chunk: 4000, summary: 400,
        target: 8000, rounds: 1, ext: '',
        model: getModel(),
      }),
    });
    const data = await res.json();
    result.textContent = data.result ?? data.error ?? t('api_error');
  } catch (e) {
    result.textContent = t('error_prefix') + e.message;
  }
}

async function deleteFile(path) {
  if (!confirm(`Delete: ${path}?`)) return;
  try {
    const res  = await fetch('/vyrii/files', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (data.ok) { showToast('Deleted'); refreshFiles(); }
    else showToast(data.error || t('api_error'));
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

function showMkdir() {
  document.getElementById('mkdir-dialog').style.display = 'flex';
  document.getElementById('mkdir-name').focus();
}
function closeMkdir() {
  document.getElementById('mkdir-dialog').style.display = 'none';
  document.getElementById('mkdir-name').value = '';
}

async function doMkdir() {
  const name = document.getElementById('mkdir-name').value.trim();
  if (!name) return;
  try {
    const res  = await fetch('/vyrii/files/mkdir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: name }),
    });
    const data = await res.json();
    if (data.ok) { showToast('Created'); closeMkdir(); refreshFiles(); }
    else showToast(data.error || t('api_error'));
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── RAG ───────────────────────────────────────────────
let _ragContext = '';

async function ragRefreshProjects() {
  const sel = document.getElementById('rag-project');
  try {
    const res  = await fetch('/vyrii/rag/projects');
    const data = await res.json();
    const projects = data.projects || [];
    const current  = sel.value;
    sel.innerHTML = `<option value="">${t('rag_select_project')}</option>`
      + projects.map(p => `<option value="${p}"${p === current ? ' selected' : ''}>${p}</option>`).join('');
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function runRagSearch() {
  const project = document.getElementById('rag-project').value.trim();
  const query   = document.getElementById('rag-query').value.trim();
  const topk    = +document.getElementById('rag-topk').value;
  if (!project || !query) { showToast('Select a project and enter a query'); return; }

  setResultLoading('rag-results');
  document.getElementById('rag-ask-btn').style.display  = 'none';
  document.getElementById('rag-llm-col').style.display  = 'none';
  document.getElementById('rag-sources').style.display  = 'none';
  _ragContext = '';

  try {
    const res  = await fetch('/vyrii/rag/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project, query, top_k: topk }),
    });
    const data = await res.json();
    if (data.error) { setResult('rag-results', data.error); return; }

    _ragContext = data.context || '';
    const results = data.results || [];
    if (!results.length) { setResult('rag-results', 'No results found.'); return; }

    // render results
    const html = results.map(r => `
      <div style="margin-bottom:14px">
        <div style="font-weight:600;margin-bottom:4px">
          ${r.rank}. ${escHtml(r.file)}
          <span style="color:var(--text-muted);font-weight:400;font-size:12px"> score: ${r.score}</span>
        </div>
        <pre style="white-space:pre-wrap;font-size:12px;max-height:200px;overflow-y:auto">${escHtml(r.text)}</pre>
      </div>`).join('');
    document.getElementById('rag-results').innerHTML = html;

    // sources strip
    document.getElementById('rag-sources').style.display = 'block';
    document.getElementById('rag-sources-list').textContent = (data.sources || []).join('  ·  ');

    // show Ask LLM button
    document.getElementById('rag-ask-btn').style.display = 'inline-flex';
  } catch (e) {
    setResult('rag-results', t('error_prefix') + e.message);
  }
}

async function runRagAsk() {
  const query = document.getElementById('rag-query').value.trim();
  if (!_ragContext || !query) return;
  document.getElementById('rag-llm-col').style.display = 'flex';
  setResultLoading('rag-llm-out');
  try {
    const res  = await fetch('/vyrii/rag/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, context: _ragContext, model: getModel() }),
    });
    const data = await res.json();
    setResultMd('rag-llm-out', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('rag-llm-out', t('error_prefix') + e.message);
  }
}

// ── SETTINGS ──────────────────────────────────────────
async function loadSettings() {
  try {
    const res  = await fetch('/vyrii/settings');
    const cfg  = await res.json();
    const set  = (id, val) => { if (val !== undefined && val !== null) document.getElementById(id).value = val; };
    set('cfg-url',            cfg.saved_url || 'http://localhost:11434');
    set('cfg-backend',        cfg.saved_backend || 'ollama');
    set('cfg-timeout',        cfg.timeout || 180);
    set('cfg-worker-timeout', cfg.worker_timeout || 300);
    set('cfg-model',          cfg.saved_model || '');
    set('cfg-lang',           cfg.lang || 'en');
    set('cfg-auth-user',      cfg.auth_user || 'admin');
    await loadProfileOptions();
    set('cfg-active-profile', cfg.active_profile || '');
    const rmode = cfg.reserve_mode || 'response';
    const rEl = document.getElementById(rmode === 'timer' ? 'cfg-reserve-timer' : 'cfg-reserve-response');
    if (rEl) rEl.checked = true;
    set('cfg-reserve-timeout', cfg.reserve_timeout || 600);
    const rcEl = document.getElementById('restart-cmd-input');
    if (rcEl && cfg.restart_cmd) rcEl.value = cfg.restart_cmd;
    set('cfg-ollama-kv-cache',   cfg.ollama_kv_cache    || '');
    set('cfg-ollama-keep-alive', cfg.ollama_keep_alive  || '');
    set('cfg-ollama-max-loaded', cfg.ollama_max_loaded_models || '');
    set('cfg-ollama-host',       cfg.ollama_host        || '');
    const faEl = document.getElementById('cfg-ollama-flash-attn');
    if (faEl) faEl.checked = !!cfg.ollama_flash_attention;
  } catch { /* offline — keep defaults */ }
}

async function saveAuth() {
  const username = document.getElementById('cfg-auth-user').value.trim();
  const password = document.getElementById('cfg-auth-pass').value;
  if (!username) { showToast('Username required'); return; }
  if (!password) { showToast('Password required'); return; }
  try {
    const res = await fetch('/vyrii/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('cfg-auth-pass').value = '';
      const s = document.getElementById('auth-status');
      s.style.display = 'inline';
      setTimeout(() => { s.style.display = 'none'; }, 1500);
      // reload so browser prompts for new credentials
      setTimeout(() => location.reload(), 1800);
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function saveSettings() {
  const payload = {
    saved_url:      document.getElementById('cfg-url').value.trim()           || null,
    saved_backend:  document.getElementById('cfg-backend').value              || null,
    timeout:        +document.getElementById('cfg-timeout').value             || null,
    worker_timeout: +document.getElementById('cfg-worker-timeout').value      || null,
    saved_model:    document.getElementById('cfg-model').value.trim()         || null,
    lang:           document.getElementById('cfg-lang').value                 || null,
    active_profile: document.getElementById('cfg-active-profile').value       || '',
    reserve_mode:   document.querySelector('input[name="reserve-mode"]:checked')?.value || 'response',
    reserve_timeout: +document.getElementById('cfg-reserve-timeout').value   || 600,
    ollama_kv_cache:          document.getElementById('cfg-ollama-kv-cache')?.value || null,
    ollama_flash_attention:   document.getElementById('cfg-ollama-flash-attn')?.checked ? 1 : 0,
    ollama_keep_alive:        document.getElementById('cfg-ollama-keep-alive')?.value.trim() || null,
    ollama_max_loaded_models: document.getElementById('cfg-ollama-max-loaded')?.value.trim() || null,
    ollama_host:              document.getElementById('cfg-ollama-host')?.value.trim() || null,
  };
  try {
    const res  = await fetch('/vyrii/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      const status = document.getElementById('settings-status');
      status.style.display = 'inline';
      setTimeout(() => { status.style.display = 'none'; }, 2500);
      // apply lang change immediately if changed
      if (payload.lang) setLang(payload.lang);
      loadModels();
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── SYSTEM CONTROL ────────────────────────────────────
function _sysStatus(msg, isError) {
  const el = document.getElementById('sys-status');
  el.style.display = 'block';
  el.style.color = isError ? '#ef4444' : 'var(--accent)';
  el.textContent = msg;
}

async function sysRestart() {
  _sysStatus(t('sys_restarting'), false);
  try {
    await fetch('/vyrii/system/restart-cmd', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({}) });
    _sysStatus(t('sys_restarting_wait'), false);
    setTimeout(() => location.reload(), 5000);
  } catch {
    _sysStatus(t('sys_restart_sent'), false);
    setTimeout(() => location.reload(), 5000);
  }
}

async function sysRestartCmd() {
  const cmd = (document.getElementById('restart-cmd-input')?.value || '').trim();
  if (!cmd) { _sysStatus('Enter a command first.', true); return; }
  try {
    await fetch('/vyrii/settings', { method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ restart_cmd: cmd }) });
  } catch { /* save failed — proceed anyway */ }
  _sysStatus(t('sys_restarting'), false);
  try {
    await fetch('/vyrii/system/restart-cmd', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ cmd }) });
    _sysStatus(t('sys_restarting_wait'), false);
    setTimeout(() => location.reload(), 5000);
  } catch {
    _sysStatus(t('sys_restart_sent'), false);
    setTimeout(() => location.reload(), 5000);
  }
}

async function ollamaRestart() {
  const st = document.getElementById('ollama-status');
  if (st) st.textContent = 'Saving…';
  await saveSettings();
  if (st) st.textContent = 'Restarting Ollama…';
  try {
    await fetch('/vyrii/system/ollama-restart', { method: 'POST' });
    if (st) st.textContent = 'Ollama restarting…';
  } catch {
    if (st) st.textContent = 'Signal sent (check Ollama logs).';
  }
  setTimeout(() => { if (st) st.textContent = ''; }, 5000);
}

async function sysReboot() {
  const confirmed = document.getElementById('sys-confirm').checked;
  if (!confirmed) { _sysStatus('Check the confirmation box first.', true); return; }
  try {
    const res  = await fetch('/vyrii/system/reboot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed }),
    });
    const data = await res.json();
    _sysStatus(data.message ?? data.error ?? 'Done.', !!data.error);
  } catch (e) {
    _sysStatus(t('error_prefix') + e.message, true);
  }
}

async function sysShutdown() {
  const confirmed = document.getElementById('sys-confirm').checked;
  if (!confirmed) { _sysStatus('Check the confirmation box first.', true); return; }
  try {
    const res  = await fetch('/vyrii/system/shutdown', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed }),
    });
    const data = await res.json();
    _sysStatus(data.message ?? data.error ?? 'Done.', !!data.error);
  } catch (e) {
    _sysStatus(t('error_prefix') + e.message, true);
  }
}

async function uploadFiles(input) {
  if (!input.files.length) return;
  const form = new FormData();
  for (const f of input.files) form.append('files', f);
  try {
    const res  = await fetch('/vyrii/files/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (data.ok) {
      showToast(`Uploaded: ${(data.saved || []).join(', ')}`);
      refreshFiles();
    } else {
      showToast(JSON.stringify(data.error || t('api_error')));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
  input.value = '';
}

// ── SSE HELPER (team/run) ─────────────────────────────
// Reads the SSE stream from /vyrii/team/run into a result box.
// Shows progress inline; writes final result when done.
async function _readTeamSSE(res, resultId, progressLogId) {
  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop() ?? '';
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const item = JSON.parse(line.slice(6));
        if (item.type === 'progress') {
          if (progressLogId) {
            const log = document.getElementById(progressLogId);
            if (log) {
              const d = document.createElement('div');
              d.innerHTML = md(item.text);
              log.appendChild(d);
            }
          } else {
            const el = document.getElementById(resultId);
            if (el) el.innerHTML = `<span class="status-bar"><span class="status-dot"></span>${escHtml(item.text)}</span>`;
          }
        } else if (item.type === 'done') {
          setResultMd(resultId, item.text);
          if (progressLogId) {
            const wrap = document.getElementById(progressLogId)?.parentElement;
            if (wrap) wrap.style.display = 'none';
          }
        } else if (item.type === 'error') {
          setResult(resultId, 'Error: ' + item.text);
          if (progressLogId) {
            const wrap = document.getElementById(progressLogId)?.parentElement;
            if (wrap) wrap.style.display = 'none';
          }
        }
      } catch { /* ignore malformed SSE */ }
    }
  }
}

// ── DEEPAGENT TEAM HELPERS ────────────────────────────
function daTeamToggle() {
  const on = document.getElementById('da-team-chk').checked;
  document.getElementById('da-team-wrap').style.display = on ? 'flex' : 'none';
  if (on) daTeamRefresh();
}

async function daTeamRefresh() {
  const sel = document.getElementById('da-team-profile');
  try {
    const res  = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    const cur  = sel.value;
    sel.innerHTML = '<option value="">— select profile —</option>'
      + (data.profiles || []).map(p =>
          `<option value="${escHtml(p.name)}"${p.name === cur ? ' selected' : ''}>${escHtml(p.name)}</option>`
        ).join('');
  } catch { /* ignore */ }
}

// ── PROFILE ───────────────────────────────────────────
let _profileList = [];

async function profileLoad() {
  try {
    const res  = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    _profileList = data.profiles || [];
    _renderProfileList();
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

function _renderProfileList() {
  const el = document.getElementById('profile-list');
  if (!_profileList.length) {
    el.innerHTML = '<div class="placeholder-text" style="font-size:12px">No profiles</div>';
    return;
  }
  el.innerHTML = '';
  _profileList.forEach(p => {
    const btn = document.createElement('button');
    btn.className = 'btn btn-ghost btn-sm';
    btn.style.cssText = 'text-align:left;justify-content:flex-start;width:100%';
    btn.textContent = p.name;
    btn.addEventListener('click', () => profileSelect(p.name));
    el.appendChild(btn);
  });
}

function profileNew() {
  document.getElementById('prof-name').value    = '';
  document.getElementById('prof-comment').value = '';
  document.getElementById('prof-workers').innerHTML = '';
  profileAddWorker();
}

function profileSelect(name) {
  const p = _profileList.find(x => x.name === name);
  if (!p) return;
  document.getElementById('prof-name').value    = p.name    || '';
  document.getElementById('prof-comment').value = p.comment || '';
  const container = document.getElementById('prof-workers');
  container.innerHTML = '';
  (p.workers || []).forEach(w => profileAddWorker(w.host || '', w.model || '', w.provider || 'ollama'));
}

function profileAddWorker(host = '', model = '', provider = 'ollama') {
  const container = document.getElementById('prof-workers');
  const div = document.createElement('div');
  div.className = 'form-row worker-row';
  div.style.gap = '6px';
  div.innerHTML = `
    <input type="text" class="form-control" placeholder="localhost:11434"
           value="${escHtml(host)}" style="flex:2">
    <input type="text" class="form-control" placeholder="gemma3:1b"
           value="${escHtml(model)}" style="flex:2">
    <select class="form-control" style="flex:1">
      <option value="ollama"${provider === 'ollama' ? ' selected' : ''}>Ollama</option>
      <option value="openai"${provider === 'openai' ? ' selected' : ''}>OpenAI</option>
    </select>
    <button class="btn btn-danger btn-sm"
            onclick="this.closest('.worker-row').remove()" style="flex-shrink:0">✕</button>
  `;
  container.appendChild(div);
}

function _profileGetWorkers() {
  return Array.from(document.querySelectorAll('#prof-workers .worker-row')).map(row => {
    const inputs = row.querySelectorAll('input');
    const sel    = row.querySelector('select');
    return { host: inputs[0]?.value.trim() || '', model: inputs[1]?.value.trim() || '', provider: sel?.value || 'ollama' };
  }).filter(w => w.host && w.model);
}

async function profileSave() {
  const name = document.getElementById('prof-name').value.trim();
  if (!name) { showToast('Name is required'); return; }
  try {
    const res = await fetch('/vyrii/team/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        comment: document.getElementById('prof-comment').value.trim(),
        workers: _profileGetWorkers(),
      }),
    });
    const data = await res.json();
    if (data.ok) {
      const s = document.getElementById('profile-status');
      s.style.display = 'inline';
      setTimeout(() => { s.style.display = 'none'; }, 2000);
      profileLoad();
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function profileDelete() {
  const name = document.getElementById('prof-name').value.trim();
  if (!name) return;
  if (!confirm(`Delete profile "${name}"?`)) return;
  try {
    const res  = await fetch(`/vyrii/team/profile/${encodeURIComponent(name)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('prof-name').value    = '';
      document.getElementById('prof-comment').value = '';
      document.getElementById('prof-workers').innerHTML = '';
      profileLoad();
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── TEAM ──────────────────────────────────────────────
async function teamLoadProfiles() {
  const sel = document.getElementById('team-profile');
  try {
    const res  = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    const cur  = sel.value;
    sel.innerHTML = '<option value="">— select profile —</option>'
      + (data.profiles || []).map(p =>
          `<option value="${escHtml(p.name)}"${p.name === cur ? ' selected' : ''}>${escHtml(p.name)}</option>`
        ).join('');
  } catch { /* ignore */ }
}

async function teamLoadProfile() {
  const name      = document.getElementById('team-profile').value;
  const wrap      = document.getElementById('team-aspects-wrap');
  const container = document.getElementById('team-aspects');
  if (!name) { wrap.style.display = 'none'; return; }
  try {
    const res  = await fetch(`/vyrii/team/profile/${encodeURIComponent(name)}`);
    const prof = await res.json();
    const workers = prof.workers || [];
    container.innerHTML = '';
    workers.forEach(w => {
      const div = document.createElement('div');
      div.style.cssText = 'display:flex;align-items:center;gap:8px';
      div.innerHTML = `
        <span style="font-size:12px;color:var(--text-muted);width:180px;flex-shrink:0">
          ${escHtml(w.model || '')} @ ${escHtml(w.host || '')}
        </span>
        <input type="text" class="form-control aspect-input"
               placeholder="Aspect (optional)…" style="flex:1">
      `;
      container.appendChild(div);
    });
    wrap.style.display = workers.length ? 'block' : 'none';
  } catch { /* ignore */ }
}

async function runTeam() {
  const profile = document.getElementById('team-profile').value;
  const query   = document.getElementById('team-query').value.trim();
  if (!profile) { showToast('Select a profile'); return; }
  if (!query)   { showToast('Enter a query'); return; }

  const aspects      = Array.from(document.querySelectorAll('#team-aspects .aspect-input')).map(i => i.value.trim());
  const progressLog  = document.getElementById('team-progress-log');
  const progressWrap = document.getElementById('team-progress');
  progressLog.innerHTML = '';
  progressWrap.style.display = 'block';
  setResultLoading('team-result');

  try {
    const res = await fetch('/vyrii/team/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profile_name: profile,
        query,
        aspects,
        combine:  document.getElementById('team-combine').value,
        ctx_mode: document.getElementById('team-ctx').value,
        model:    getModel(),
        num_ctx:  4096,
        timeout:  300,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await _readTeamSSE(res, 'team-result', 'team-progress-log');
  } catch (e) {
    setResult('team-result', t('error_prefix') + e.message);
    progressWrap.style.display = 'none';
  }
}

// ── PROJECTS ──────────────────────────────────────────
async function projRefresh() {
  try {
    const data = await (await fetch('/vyrii/projects')).json();
    const list = document.getElementById('proj-list');
    if (!list) return;
    const projects = data.projects || [];
    if (!projects.length) {
      list.innerHTML = `<div style="font-size:13px;color:var(--text-muted)">${t('loading').replace('…','') + ' — none yet'}</div>`;
      return;
    }
    list.innerHTML = projects.map(p => `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--input-bg);
                  border:1px solid var(--border);border-radius:6px">
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px">${esc(p.name)}</div>
          <div style="font-size:11px;color:var(--text-muted);word-break:break-all">${esc(p.path)}</div>
          ${p.description ? `<div style="font-size:11px;color:var(--text-muted)">${esc(p.description)}</div>` : ''}
        </div>
        <button class="btn btn-danger btn-sm" onclick="projDelete('${esc(p.name)}')"
                style="flex-shrink:0" data-i18n="proj_delete_confirm">✕</button>
      </div>`).join('');
  } catch (e) { /* ignore */ }
}

async function projAdd() {
  const name = document.getElementById('proj-name').value.trim();
  const path = document.getElementById('proj-path').value.trim();
  const desc = document.getElementById('proj-desc').value.trim();
  if (!name || !path) { showToast('Name and path are required'); return; }
  await fetch('/vyrii/projects', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name, path, description: desc }) });
  document.getElementById('proj-name').value = '';
  document.getElementById('proj-path').value = '';
  document.getElementById('proj-desc').value = '';
  projRefresh();
  loadProjectSelects();
}

async function projDelete(name) {
  await fetch(`/vyrii/projects/${encodeURIComponent(name)}`, { method: 'DELETE' });
  projRefresh();
  loadProjectSelects();
}

async function loadProjectSelects() {
  try {
    const data = await (await fetch('/vyrii/projects')).json();
    const projects = data.projects || [];
    const opts = `<option value="">— select project —</option>` +
      projects.map(p => `<option value="${esc(p.name)}">${esc(p.name)} — ${esc(p.path)}</option>`).join('');
    ['sim-project', 'svy-project'].forEach(id => {
      const el = document.getElementById(id);
      if (el) { const cur = el.value; el.innerHTML = opts; if (cur) el.value = cur; }
    });
  } catch { /* offline */ }
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ── GENERIC CLI RUN HELPER ────────────────────────────
async function _runCmd(command, cwd, resultId, busyId, showStderr = true) {
  if (busyId) { const b = document.getElementById(busyId); if (b) b.style.display = ''; }
  const box = document.getElementById(resultId);
  if (box) box.innerHTML = `<span style="color:var(--text-muted)">${t('loading')}</span>`;
  try {
    const data = await (await fetch('/vyrii/run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ command, cwd: cwd || '' }),
    })).json();
    if (busyId) { const b = document.getElementById(busyId); if (b) b.style.display = 'none'; }
    if (data.error) { if (box) box.textContent = 'Error: ' + data.error; return; }
    const out = (data.stdout || '') + (showStderr && data.stderr ? '\n[stderr]\n' + data.stderr : '');
    const status = data.returncode === 0
      ? t('run_ok').replace('{code}', data.returncode).replace('{dur}', data.duration_s)
      : t('run_error').replace('{code}', data.returncode);
    if (box) box.textContent = status + '\n\n' + out.trim();
  } catch (e) {
    if (busyId) { const b = document.getElementById(busyId); if (b) b.style.display = 'none'; }
    if (box) box.textContent = t('error_prefix') + e.message;
  }
}

function _getProject(selectId, infoId) {
  const sel = document.getElementById(selectId);
  if (!sel || !sel.value) { showToast('Select a project first'); return null; }
  return sel.value;
}

function _getProjectPath(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel || !sel.value) return null;
  const opt = sel.options[sel.selectedIndex];
  // path is encoded in the option text after ' — '
  const text = opt ? opt.textContent : '';
  const idx = text.indexOf(' — ');
  return idx >= 0 ? text.slice(idx + 3) : null;
}

// ── SIMARGL ───────────────────────────────────────────
function simSubtab(name) {
  ['index','search','rrf'].forEach(n => {
    document.getElementById(`sim-pane-${n}`).style.display = n === name ? '' : 'none';
    document.getElementById(`sim-tab-${n}`).classList.toggle('subtab-active', n === name);
  });
}

function simProjectChanged() {
  const path = _getProjectPath('sim-project');
  const info = document.getElementById('sim-path-info');
  if (info) info.textContent = path ? path : '';
}

async function simRunRrf() {
  const path = _getProjectPath('sim-project');
  if (!path) { showToast('Select a project first'); return; }
  const query   = document.getElementById('rrf-query').value.trim();
  if (!query) { showToast('Enter a task description'); return; }
  const sources = document.getElementById('rrf-sources').value.trim() || 'task:default,file:default';
  const topn    = document.getElementById('rrf-topn').value    || '10';
  const topk    = document.getElementById('rrf-topk').value    || '10';
  const k       = document.getElementById('rrf-k').value       || '60';
  const sort    = document.getElementById('rrf-sort').value    || 'freq';
  const format  = document.getElementById('rrf-format').value  || 'text';
  const blend   = document.getElementById('rrf-blend').value   || '1.0';
  const showStderr = document.getElementById('rrf-stderr').checked;

  let cmd = `simargl rrf ${JSON.stringify(query)}`
    + ` --sources ${sources} --store-dir .simargl`
    + ` --top-n ${topn} --top-k ${topk} --k ${k}`
    + ` --sort ${sort} --format ${format}`;
  if (parseFloat(blend) !== 1.0) cmd += ` --score-blend ${blend}`;

  await _runCmd(cmd, path, 'sim-result', null, showStderr);
}

async function simRunIndex() {
  const name = _getProject('sim-project', 'sim-path-info');
  if (!name) return;
  const path = _getProjectPath('sim-project');
  const cmd = `simargl index files . --project ${name} --store .simargl`;
  await _runCmd(cmd, path, 'sim-result', null);
}

function simModeChanged() {
  const mode = document.getElementById('sim-mode').value;
  const isTask   = mode === 'task';
  const needsTopK = mode !== 'file';
  const el = (id) => document.getElementById(id);
  if (el('sim-sort-wrap'))     el('sim-sort-wrap').style.display     = isTask ? '' : 'none';
  if (el('sim-topk-wrap'))     el('sim-topk-wrap').style.display     = needsTopK ? '' : 'none';
}

async function simRunSearch() {
  const name = _getProject('sim-project', 'sim-path-info');
  if (!name) return;
  const path = _getProjectPath('sim-project');
  const query = document.getElementById('sim-query').value.trim();
  if (!query) { showToast('Enter a task description'); return; }

  const mode    = document.getElementById('sim-mode').value    || 'file';
  const format  = document.getElementById('sim-format').value  || 'text';
  const topn    = document.getElementById('sim-topn').value    || '10';
  const topk    = document.getElementById('sim-topk').value    || '10';
  const sort    = document.getElementById('sim-sort').value    || 'rank';
  const diff       = document.getElementById('sim-diff').checked;
  const noBH       = document.getElementById('sim-noblackholes').checked;
  const showStderr = document.getElementById('sim-stderr').checked;

  let cmd = `simargl search ${JSON.stringify(query)}`
    + ` --project ${name} --store-dir .simargl`
    + ` --mode ${mode} --format ${format} --top-n ${topn}`;
  if (mode !== 'file') cmd += ` --top-k ${topk}`;
  if (mode === 'task') cmd += ` --sort ${sort}`;
  if (diff)  cmd += ' --diff';
  if (noBH)  cmd += ' --no-blackholes';

  await _runCmd(cmd, path, 'sim-result', null, showStderr);
}

function _homeDir() {
  // best-effort: resolve ~ based on known API paths (not needed server-side)
  return '';
}

// ── SVITOVYD ──────────────────────────────────────────
function svySubtab(name) {
  ['index','find','trace','deps','sym','kw','idiff'].forEach(n => {
    document.getElementById(`svy-pane-${n}`).style.display = n === name ? '' : 'none';
    document.getElementById(`svy-tab-${n}`).classList.toggle('subtab-active', n === name);
  });
}

function svyProjectChanged() {
  const path = _getProjectPath('svy-project');
  const info = document.getElementById('svy-path-info');
  if (info) info.textContent = path ? path : '';
}

async function svyRun(op) {
  const name = _getProject('svy-project', 'svy-path-info');
  if (!name) return;
  const path = _getProjectPath('svy-project');
  let cmd = '';

  if (op === 'index') {
    const depth = document.getElementById('svy-depth').value || '2';
    cmd = `svitovyd index . ${depth} --stdout`;
  } else if (op === 'find') {
    const q = document.getElementById('svy-find-q').value.trim();
    if (!q) { showToast('Enter query tokens'); return; }
    cmd = `svitovyd find ${q}`;
  } else if (op === 'trace') {
    const id = document.getElementById('svy-trace-id').value.trim();
    if (!id) { showToast('Enter identifier'); return; }
    const depth = document.getElementById('svy-trace-depth').value || '8';
    cmd = `svitovyd trace ${id} --depth ${depth}`;
  } else if (op === 'deps') {
    const id = document.getElementById('svy-deps-id').value.trim();
    if (!id) { showToast('Enter identifier'); return; }
    const depth = document.getElementById('svy-deps-depth').value || '8';
    cmd = `svitovyd deps ${id} --depth ${depth}`;
  } else if (op === 'sym') {
    const k = document.getElementById('svy-sym-k').value || '5';
    cmd = `svitovyd sym --k ${k}`;
  } else if (op === 'kw') {
    const taskText = document.getElementById('svy-kw-task').value.trim();
    const k = document.getElementById('svy-kw-k').value || '50';
    const fuzzy = document.getElementById('svy-kw-fuzzy').checked ? ' -f' : '';
    if (taskText) {
      cmd = `svitovyd keywords extract ${JSON.stringify(taskText)}${fuzzy}`;
    } else {
      cmd = `svitovyd keywords --k ${k}`;
    }
  } else if (op === 'idiff') {
    const prev = document.getElementById('svy-idiff-prev').value.trim();
    if (!prev) { showToast('Enter previous map file path'); return; }
    cmd = `svitovyd idiff --prev ${prev}`;
  }

  await _runCmd(cmd, path, 'svy-result', 'svy-running');
}

// ── SCHEDULER ─────────────────────────────────────────
async function _schFetch(url, opts) {
  const res = await fetch(url, opts);
  return res.json();
}

function schTypeChanged() {
  const stype = document.getElementById('sch-stype').value;
  const timeRow = document.getElementById('sch-time-row');
  const dowWrap = document.getElementById('sch-dow-wrap');
  const intWrap = document.getElementById('sch-interval-wrap');
  const isInterval = stype.startsWith('interval_');
  if (timeRow) timeRow.style.display = isInterval ? 'none' : '';
  if (dowWrap) dowWrap.style.display = stype === 'weekly' ? '' : 'none';
  if (intWrap) intWrap.style.display = isInterval ? '' : 'none';
}

async function schRefresh() {
  try {
    const data = await _schFetch('/vyrii/scheduler/tasks');
    const tasks = data.tasks || [];
    const box = document.getElementById('sch-table');
    if (!box) return;
    if (!tasks.length) { box.textContent = 'No scheduled tasks yet.'; return; }
    const rows = tasks.map((task, i) => {
      const stype = task.schedule_type || 'daily';
      const h = String(task.hour || 9).padStart(2,'0');
      const m = String(task.minute || 0).padStart(2,'0');
      let sched = stype === 'daily' ? `Daily ${h}:${m}`
        : stype === 'weekly' ? `Weekly ${task.day_of_week || 'mon'} ${h}:${m}`
        : stype === 'monthly' ? `Monthly day ${task.interval_value||1} ${h}:${m}`
        : `Every ${task.interval_value||'?'} ${stype.split('_')[1]}`;
      const status = task.last_status || '—';
      const on = task.enabled !== false ? '✅' : '⏸';
      return `<div style="display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border)">
        <span style="font-size:11px;font-family:monospace;color:var(--text-muted);width:70px">${task.id.slice(0,8)}</span>
        <span style="flex:1;font-size:13px">${esc(task.name)}</span>
        <span style="font-size:11px;color:var(--text-muted);width:140px">${sched}</span>
        <span style="font-size:11px;width:60px">${status}</span>
        <span style="width:20px">${on}</span>
      </div>`;
    }).join('');
    box.innerHTML = rows;
  } catch (e) {
    const box = document.getElementById('sch-table');
    if (box) box.textContent = t('error_prefix') + e.message;
  }
}

async function schCreate() {
  const name    = document.getElementById('sch-name').value.trim();
  const command = document.getElementById('sch-command').value.trim();
  const stype   = document.getElementById('sch-stype').value;
  if (!name || !command) { showToast('Name and command required'); return; }
  const timeVal = document.getElementById('sch-time').value || '09:00';
  const [hh, mm] = timeVal.split(':').map(Number);
  const body = {
    name, command, schedule_type: stype,
    hour: hh || 9, minute: mm || 0,
    day_of_week: document.getElementById('sch-dow').value || 'mon',
    interval_value: parseInt(document.getElementById('sch-interval').value || '60'),
  };
  const data = await _schFetch('/vyrii/scheduler/tasks', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (data.error) { showToast(data.error); return; }
  showToast('Task created');
  document.getElementById('sch-name').value = '';
  document.getElementById('sch-command').value = '';
  schRefresh();
}

function _schId() {
  const v = (document.getElementById('sch-task-id').value || '').trim();
  if (!v) { showToast('Enter task ID prefix'); return null; }
  return v;
}

async function schToggle() {
  const prefix = _schId(); if (!prefix) return;
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  const data = await _schFetch(`/vyrii/scheduler/tasks/${task.id}/toggle`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
  showToast(data.enabled ? 'Enabled' : 'Disabled');
  schRefresh();
}

async function schRunNow() {
  const prefix = _schId(); if (!prefix) return;
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  await _schFetch(`/vyrii/scheduler/tasks/${task.id}/run`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
  showToast('Running in background');
}

async function schDelete() {
  const prefix = _schId(); if (!prefix) return;
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  await _schFetch(`/vyrii/scheduler/tasks/${task.id}`, { method: 'DELETE' });
  showToast('Deleted');
  document.getElementById('sch-task-id').value = '';
  schRefresh();
}

async function schLoadLogs() {
  const prefix = (document.getElementById('sch-log-id').value || '').trim();
  if (!prefix) { showToast('Enter task ID prefix'); return; }
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  const data = await _schFetch(`/vyrii/scheduler/tasks/${task.id}/logs`);
  const sel = document.getElementById('sch-log-sel');
  if (!sel) return;
  sel.innerHTML = (data.logs || []).map(l =>
    `<option value="${esc(l.filename)}">${esc(l.filename)}</option>`
  ).join('');
  if (sel.options.length) schReadLog(sel.options[0].value);
}

async function schReadLog(filename) {
  if (!filename) return;
  const box = document.getElementById('sch-log-content');
  if (box) box.textContent = t('loading');
  try {
    const data = await _schFetch(`/vyrii/scheduler/log?filename=${encodeURIComponent(filename)}`);
    if (box) box.textContent = data.content || '(empty)';
  } catch (e) {
    if (box) box.textContent = t('error_prefix') + e.message;
  }
}

// ── PROMPT LIBRARY ────────────────────────────────────
let _prmAll = [];

async function prmRefresh() {
  try {
    const data = await (await fetch('/vyrii/prompts')).json();
    _prmAll = data.prompts || [];
    prmRender(document.getElementById('prm-filter')?.value || '');
  } catch { /* offline */ }
}

function prmRender(filter) {
  const list = document.getElementById('prm-list');
  if (!list) return;
  const q = (filter || '').toLowerCase();
  const items = q
    ? _prmAll.filter(p =>
        (p.name||'').toLowerCase().includes(q) ||
        (p.description||'').toLowerCase().includes(q) ||
        (p.model||'').toLowerCase().includes(q) ||
        (p.area||'').toLowerCase().includes(q) ||
        (p.prompt||'').toLowerCase().includes(q)
      )
    : _prmAll;
  if (!items.length) {
    list.innerHTML = `<div style="font-size:13px;color:var(--text-muted)">${t('prm_none')}</div>`;
    return;
  }
  list.innerHTML = items.map(p => `
    <div style="padding:10px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:8px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
        <span style="font-weight:600;font-size:13px;flex:1;min-width:80px">${esc(p.name)}</span>
        ${p.model ? `<span style="font-size:11px;padding:2px 8px;background:var(--accent-dim);color:var(--accent);border-radius:10px">${esc(p.model)}</span>` : ''}
        ${p.area  ? `<span style="font-size:11px;padding:2px 8px;background:var(--surface);color:var(--text-muted);border-radius:10px;border:1px solid var(--border)">${esc(p.area)}</span>` : ''}
      </div>
      ${p.description ? `<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">${esc(p.description)}</div>` : ''}
      <div style="font-size:12px;font-family:monospace;background:var(--code-bg);border-radius:4px;padding:8px;white-space:pre-wrap;word-break:break-word;max-height:140px;overflow-y:auto">${esc(p.prompt)}</div>
      <div style="display:flex;gap:6px;margin-top:8px;align-items:center">
        <button class="btn btn-primary btn-sm" onclick="prmAddToChat('${esc(p.id)}')" data-i18n="add_to_chat">Add to chat</button>
        <button class="btn btn-ghost btn-sm" onclick="prmCopy('${esc(p.id)}')" data-i18n="copy">Copy</button>
        <button class="btn btn-danger btn-sm" onclick="prmDelete('${esc(p.id)}')" style="margin-left:auto">✕</button>
      </div>
    </div>`).join('');
}

function prmFilter() {
  prmRender(document.getElementById('prm-filter')?.value || '');
}

async function prmAdd() {
  const name   = document.getElementById('prm-name').value.trim();
  const prompt = document.getElementById('prm-prompt').value.trim();
  if (!name || !prompt) { showToast('Name and prompt text are required'); return; }
  await fetch('/vyrii/prompts', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      name,
      prompt,
      description: document.getElementById('prm-desc').value.trim(),
      model:       document.getElementById('prm-model').value.trim(),
      area:        document.getElementById('prm-area').value.trim(),
    }),
  });
  ['prm-name','prm-desc','prm-model','prm-area','prm-prompt'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  prmRefresh();
}

async function prmDelete(id) {
  await fetch(`/vyrii/prompts/${encodeURIComponent(id)}`, { method: 'DELETE' });
  prmRefresh();
}

function prmAddToChat(id) {
  const p = _prmAll.find(x => x.id === id);
  if (!p) return;
  const inp = document.getElementById('chat-input');
  if (!inp) return;
  inp.value = inp.value ? inp.value + '\n\n' + p.prompt : p.prompt;
  autoResize(inp);
  switchTab('chat');
  inp.focus();
}

function prmCopy(id) {
  const p = _prmAll.find(x => x.id === id);
  if (!p) return;
  navigator.clipboard.writeText(p.prompt).then(() => showToast(t('copied')));
}
