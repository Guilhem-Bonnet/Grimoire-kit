/**
 * Grimoire Switchboard shim — remplace la partie "VS Code extension" par un
 * backend 100% client-side (IndexedDB + clipboard + localStorage) pour le
 * cockpit web. Permet à l'UI Switchboard upstream de tourner telle quelle.
 *
 * Lorsque l'extension VS Code sera intégrée (scope 3), ce shim sera contourné
 * car `acquireVsCodeApi` sera fourni nativement.
 */
(function () {
  if (typeof window.acquireVsCodeApi === 'function') return;

  const STORAGE_KEY = 'grimoire.switchboard.state.v1';
  const loadState = () => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? {}; }
    catch { return {}; }
  };
  const saveState = (s) => localStorage.setItem(STORAGE_KEY, JSON.stringify(s));

  // --- Grimoire seed data ---
  const GRIMOIRE_AGENTS = {
    planner: 'Opus Planner (Architect)',
    lead: 'Claude Sonnet (Lead Dev)',
    coder: 'Gemini Flash (Coder)',
    intern: 'Haiku (Intern)',
    reviewer: 'Grumpy Principal (Reviewer)',
    tester: 'Playwright TEA (Tester)',
    analyst: 'Kimi Analyst (Research)',
    'team-lead': 'Grimoire Master (Orchestrator)',
    jules: 'Jules (Overflow)'
  };

  const GRIMOIRE_COLUMNS = [
    { id: 'CREATED', label: 'New', role: null, autobanEnabled: true },
    { id: 'PLAN REVIEWED', label: 'Planned', role: 'planner', autobanEnabled: true },
    { id: 'LEAD CODED', label: 'Lead Coder', role: 'lead', autobanEnabled: true },
    { id: 'CODER CODED', label: 'Coder', role: 'coder', autobanEnabled: true },
    { id: 'INTERN CODED', label: 'Intern', role: 'intern', autobanEnabled: true },
    { id: 'CODE REVIEWED', label: 'Reviewed', role: 'reviewer', autobanEnabled: true },
    { id: 'ACCEPTANCE TESTED', label: 'Acceptance', role: 'tester', autobanEnabled: true },
    { id: 'SHIP GATE', label: 'Ship Gate', role: null, autobanEnabled: false, kind: 'gate' },
    { id: 'COMPLETED', label: 'Completed', kind: 'completed', autobanEnabled: false }
  ];

  const seedCards = () => {
    const now = Date.now();
    const mk = (i, topic, column, complexity, opts = {}) => ({
      sessionId: 'grm-' + i,
      column,
      topic,
      planFile: '.switchboard/plans/' + topic.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.md',
      complexity,
      workspaceRoot: '/grimoire-forge',
      lastActivity: new Date(now - i * 60000).toISOString(),
      assignedAgent: opts.agent,
      // Grimoire extensions
      provenance: opts.provenance || 'clean', // clean | missing-attribution | missing-license | missing-source
      trustStatus: opts.trust || 'trusted',    // trusted | blocked | diverged
      roomId: opts.room,                        // intake-desk | war-room | workshop | branch-finisher | seance-archive | watchtower
      grimoireAgent: opts.grmAgent              // dev | qa | pm | architect | tea | tech-writer | analyst
    });
    return [
      mk(1, 'Add cockpit telemetry exporter', 'CREATED', 'Unknown', { room: 'intake-desk' }),
      mk(2, 'Migrate skill registry to YAML v2', 'CREATED', 'Unknown', { room: 'intake-desk' }),
      mk(3, 'Refactor trust scoring to plugin arch', 'PLAN REVIEWED', 'High', { agent: 'planner', grmAgent: 'architect', room: 'war-room' }),
      mk(4, 'Add room-kit pixel preview panel', 'PLAN REVIEWED', 'Medium', { agent: 'planner', grmAgent: 'ux-designer', room: 'workshop' }),
      mk(5, 'Ship provenance compliance view', 'LEAD CODED', 'High', { agent: 'lead', grmAgent: 'dev', provenance: 'missing-attribution', room: 'branch-finisher' }),
      mk(6, 'Rename power-card persistence fields', 'CODER CODED', 'Low', { agent: 'coder', grmAgent: 'dev', trust: 'diverged', room: 'watchtower' }),
      mk(7, 'Fix timeline overflow on narrow viewport', 'CODER CODED', 'Low', { agent: 'coder', grmAgent: 'dev' }),
      mk(8, 'Doc: refresh agent-frameworks reference', 'INTERN CODED', 'Low', { agent: 'intern', grmAgent: 'tech-writer' }),
      mk(9, 'Add branch finisher smoke test', 'CODE REVIEWED', 'Medium', { agent: 'reviewer', grmAgent: 'tea', room: 'branch-finisher' }),
      mk(10, 'Verify acceptance gate for release-ready', 'ACCEPTANCE TESTED', 'Medium', { agent: 'tester', grmAgent: 'qa' }),
      mk(11, 'Attribution bundle seeding blocked', 'SHIP GATE', 'High', { provenance: 'missing-attribution', trust: 'blocked' }),
      mk(12, 'Seed attribution bundles in release-ready', 'COMPLETED', 'Medium'),
      mk(13, 'Harden hooks gateway registry', 'COMPLETED', 'High')
    ];
  };

  const state = loadState();
  if (!state.cards) { state.cards = seedCards(); saveState(state); }
  if (!state.columns) { state.columns = GRIMOIRE_COLUMNS; saveState(state); }
  if (!state.agentNames) { state.agentNames = GRIMOIRE_AGENTS; saveState(state); }

  // --- Toast layer ---
  const ensureToastLayer = () => {
    if (document.getElementById('grm-toast-layer')) return;
    const layer = document.createElement('div');
    layer.id = 'grm-toast-layer';
    layer.style.cssText = 'position:fixed;bottom:20px;right:20px;display:flex;flex-direction:column;gap:8px;z-index:99999;pointer-events:none';
    document.body.appendChild(layer);
  };
  const toast = (msg, kind = 'info') => {
    ensureToastLayer();
    const color = { info: '#3ddbd9', success: '#2ecc71', warn: '#d29922', error: '#da3633' }[kind] ?? '#3ddbd9';
    const el = document.createElement('div');
    el.textContent = msg;
    el.style.cssText = `background:#0a0a0a;border:1px solid ${color};border-left:3px solid ${color};color:${color};font-family:var(--vscode-editor-font-family,'Consolas',monospace);font-size:11px;letter-spacing:0.5px;padding:10px 14px;box-shadow:0 0 20px rgba(0,0,0,.6);pointer-events:auto;max-width:420px`;
    document.getElementById('grm-toast-layer').appendChild(el);
    setTimeout(() => { el.style.transition = 'opacity .4s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 400); }, 3400);
  };
  window.grimoireToast = toast;

  // --- Virtual "extension" backend ---
  const listeners = [];
  const postToWebview = (msg) => {
    listeners.forEach((fn) => { try { fn({ data: msg }); } catch (e) { console.error(e); } });
    window.dispatchEvent(new MessageEvent('message', { data: msg }));
  };

  const handle = async (msg) => {
    const t = msg?.type;
    if (!t) return;

    if (t === 'ready') {
      // Boot payload
      postToWebview({ type: 'updateWorkspaceSelection', workspaceRoot: '/grimoire-forge', workspaces: [{ root: '/grimoire-forge', name: 'Grimoire Forge' }] });
      postToWebview({ type: 'updateColumns', columns: state.columns });
      postToWebview({ type: 'updateAgentNames', agentNames: state.agentNames });
      postToWebview({ type: 'updateBoard', cards: state.cards, dbUnavailable: false, showingBacklog: false });
      postToWebview({ type: 'cliTriggersState', enabled: true });
      postToWebview({ type: 'dynamicComplexityRoutingState', enabled: true });
      toast('Grimoire Switchboard online — ' + state.cards.length + ' plans loaded', 'success');
      return;
    }

    if (t === 'moveCard' || t === 'moveCards') {
      const ids = msg.sessionIds ?? [msg.sessionId];
      const target = msg.target || msg.column;
      state.cards = state.cards.map((c) => ids.includes(c.sessionId) ? { ...c, column: target, lastActivity: new Date().toISOString() } : c);
      saveState(state);
      postToWebview({ type: 'updateBoard', cards: state.cards });
      toast(`Moved ${ids.length} card(s) → ${target}`, 'info');
      return;
    }

    if (t === 'dispatchPrompt' || t === 'sendPromptToAgent' || t === 'copyPromptToClipboard' || t === 'copyPromptSelected' || t === 'copyPromptAll') {
      const text = msg.prompt || msg.text || `# Grimoire Switchboard dispatch\nAgent: ${msg.agent || 'auto'}\nColumn: ${msg.column || 'n/a'}\n${(msg.sessionIds || []).join('\n')}`;
      try { await navigator.clipboard.writeText(text); toast(`Prompt copied (${text.length} chars). Paste into agent terminal.`, 'success'); }
      catch { toast('Clipboard unavailable. Prompt logged to console.', 'warn'); console.log('[GRM DISPATCH]\n' + text); }
      return;
    }

    if (t === 'createCard' || t === 'createPlan' || t === 'newPlan') {
      const now = Date.now();
      const card = {
        sessionId: 'grm-' + (state.cards.length + 1) + '-' + now,
        column: 'CREATED',
        topic: msg.topic || 'New plan',
        planFile: msg.planFile || '.switchboard/plans/new-plan-' + now + '.md',
        complexity: 'Unknown',
        workspaceRoot: '/grimoire-forge',
        lastActivity: new Date().toISOString(),
        provenance: 'clean',
        trustStatus: 'trusted'
      };
      state.cards.push(card);
      saveState(state);
      postToWebview({ type: 'updateBoard', cards: state.cards });
      toast('New plan created: ' + card.topic, 'success');
      return;
    }

    if (t === 'deleteCard' || t === 'archiveSelected' || t === 'archiveCards') {
      const ids = msg.sessionIds ?? [msg.sessionId];
      state.cards = state.cards.filter((c) => !ids.includes(c.sessionId));
      saveState(state);
      postToWebview({ type: 'updateBoard', cards: state.cards });
      toast(`Archived ${ids.length} card(s)`, 'info');
      return;
    }

    if (t === 'showInfo' || t === 'showMessage') { toast(msg.message || 'info', 'info'); return; }
    if (t === 'showError') { toast(msg.message || 'error', 'error'); return; }
    if (t === 'showWarn' || t === 'showWarning') { toast(msg.message || 'warning', 'warn'); return; }

    // Navigation entre vues
    if (t === 'openSetup') { location.href = './setup.html'; return; }
    if (t === 'openImplementation') { location.href = './implementation.html?session=' + encodeURIComponent(msg.sessionId || ''); return; }
    if (t === 'openReview') { location.href = './review.html?session=' + encodeURIComponent(msg.sessionId || ''); return; }
    if (t === 'openKanban') { location.href = './kanban.html'; return; }

    // Default: log unknown
    console.log('[grimoire-shim] unhandled msg', t, msg);
  };

  window.acquireVsCodeApi = () => ({
    postMessage: handle,
    getState: () => state.ui ?? {},
    setState: (ui) => { state.ui = ui; saveState(state); }
  });

  // --- Grimoire DA injection (charte premium orange + Geist + FX) ---
  // Injecté ASAP pour éviter FOUC, avant DOMContentLoaded.
  const daLink = document.createElement('link');
  daLink.rel = 'stylesheet';
  daLink.href = './grimoire-da.css';
  (document.head || document.documentElement).appendChild(daLink);

  // --- Grimoire extension badges (provenance / trust / room / agent) ---
  // Observer qui décore les cards avec les metadata Grimoire dès qu'elles apparaissent.
  const PROV_LABELS = {
    clean: ['✓', 'CLEAN'],
    'missing-attribution': ['⚠', 'ATTRIB?'],
    'missing-license': ['✕', 'LICENSE?'],
    'missing-source': ['⚠', 'SOURCE?']
  };
  const TRUST_LABELS = {
    trusted: ['●', 'TRUSTED'],
    blocked: ['■', 'BLOCKED'],
    diverged: ['◆', 'DIVERGED']
  };
  const ROOM_LABELS = {
    'intake-desk': 'INTAKE',
    'war-room': 'WAR-ROOM',
    workshop: 'WORKSHOP',
    'branch-finisher': 'FINISHER',
    'seance-archive': 'ARCHIVE',
    watchtower: 'WATCHTOWER'
  };

  const decorateCard = (cardEl) => {
    if (cardEl.dataset.grmDecorated === '1') return;
    const sid = cardEl.dataset.session || cardEl.dataset.sessionId ||
                cardEl.getAttribute('data-session') || cardEl.getAttribute('data-session-id');
    if (!sid) return;
    const data = (state.cards || []).find((c) => c.sessionId === sid);
    if (!data) return;
    const host = cardEl.querySelector('.card-footer, .card-meta, .kanban-card-footer') || cardEl;
    const strip = document.createElement('div');
    strip.className = 'grm-badge-strip';
    strip.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,.04)';
    const add = (cls, modifier, icon, label, title) => {
      const b = document.createElement('span');
      b.className = `${cls} is-${modifier}`;
      b.title = title;
      b.innerHTML = `<span>${icon}</span>${label}`;
      strip.appendChild(b);
    };
    if (data.provenance && data.provenance !== 'clean') {
      const [icon, label] = PROV_LABELS[data.provenance] || ['?', data.provenance.toUpperCase()];
      add('grm-provenance-badge', data.provenance, icon, label, `Provenance: ${data.provenance}`);
    }
    if (data.trustStatus && data.trustStatus !== 'trusted') {
      const [icon, label] = TRUST_LABELS[data.trustStatus] || ['?', data.trustStatus.toUpperCase()];
      add('grm-trust-badge', data.trustStatus, icon, label, `Trust: ${data.trustStatus}`);
    }
    if (data.roomId) {
      const label = ROOM_LABELS[data.roomId] || data.roomId.toUpperCase();
      add('grm-room-badge', 'room', '▣', label, `Room: ${data.roomId}`);
    }
    if (data.grimoireAgent) {
      add('grm-agent-badge', 'agent', '◆', data.grimoireAgent.toUpperCase(), `Grimoire agent: ${data.grimoireAgent}`);
    }
    if (strip.childElementCount > 0) host.appendChild(strip);
    cardEl.dataset.grmDecorated = '1';
  };

  const decoratePass = () => {
    document.querySelectorAll('.kanban-card[data-session], [data-session-id], [class*="card"][data-session]')
      .forEach(decorateCard);
  };

  // --- Branding overlay + view switcher (DA Grimoire) ---
  window.addEventListener('DOMContentLoaded', () => {
    const badge = document.createElement('div');
    badge.innerHTML = '<span style="color:#FF6B3D">◆</span> GRIMOIRE FORGE &middot; SWITCHBOARD';
    badge.style.cssText = 'position:fixed;bottom:8px;left:14px;font-family:var(--vscode-editor-font-family,"Geist Mono",monospace);font-size:9px;letter-spacing:1.6px;color:#9BA0A8;opacity:.82;pointer-events:none;z-index:9999;text-transform:uppercase;font-weight:500';
    document.body.appendChild(badge);

    const switcher = document.createElement('nav');
    const current = location.pathname.split('/').pop() || 'kanban.html';
    const views = [
      { id: 'kanban.html', label: 'KANBAN' },
      { id: 'setup.html', label: 'SETUP' },
      { id: 'implementation.html', label: 'IMPL' },
      { id: 'review.html', label: 'REVIEW' }
    ];
    switcher.style.cssText = 'position:fixed;top:8px;right:14px;display:flex;gap:5px;font-family:var(--vscode-editor-font-family,"Geist Mono",monospace);font-size:10px;letter-spacing:1.4px;z-index:9999';
    views.forEach((v) => {
      const a = document.createElement('a');
      a.href = './' + v.id;
      a.textContent = v.label;
      const active = current === v.id;
      a.style.cssText = `padding:5px 10px;border:1px solid ${active ? '#FF6B3D' : 'rgba(255,255,255,.08)'};color:${active ? '#FF6B3D' : '#9BA0A8'};text-decoration:none;background:${active ? 'rgba(255,107,61,.08)' : '#0B0C0E'};text-shadow:${active ? '0 0 10px rgba(255,107,61,.35)' : 'none'};border-radius:3px;font-weight:500`;
      switcher.appendChild(a);
    });
    document.body.appendChild(switcher);

    // Décoration initiale + watcher (MutationObserver pour nouvelles cards)
    decoratePass();
    const mo = new MutationObserver(() => decoratePass());
    mo.observe(document.body, { childList: true, subtree: true });
  });
})();
