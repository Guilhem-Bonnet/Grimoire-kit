// Le chat de l'orchestrateur (ACP2) : l'onglet « Orchestrateur » du dock.
//
// Le cockpit n'est pas un harness. Le serveur tient le processus ACP de l'agent de l'hôte
// (Claude Code d'abord) et nous pousse son flux en Server-Sent Events
// (`/api/workspace/chat/stream`, même mécanique que le pouls) ; ce que la personne tape part
// en POST sous les gardes d'écriture du cockpit (loopback, jeton de session, Origin).
//
// Gouvernance (ADR-007) : un message tapé ici est un tour humain. Rien dans ce fichier
// n'écrit dans le projet — c'est l'agent qui agit, avec ses permissions, et chacune de ses
// demandes s'affiche dans le fil en trois boutons : « Autoriser une fois », « Toujours »,
// « Refuser ». Sans réponse, rien n'est accordé.
//
// Accessibilité : le fil est un `role="log"` (`aria-live="polite"`), la saisie est au
// clavier — Entrée envoie, Maj+Entrée saute une ligne — et les boutons de permission
// reçoivent le focus quand la demande apparaît.

import { api } from './api.js';
import { renderMarkdown } from './markdown.js';

const SAFE_HREF = /^(https?:|mailto:|#)/i;

function mk(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === 'text') node.textContent = value;
    else if (key === 'class') node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

/** Du markdown de l'agent vers le DOM : le texte est échappé d'abord, les liens non sûrs perdent leur cible. */
function markdownInto(node, source) {
  node.innerHTML = renderMarkdown(source);
  for (const link of node.querySelectorAll('a')) {
    if (!SAFE_HREF.test(link.getAttribute('href') || '')) link.removeAttribute('href');
    else link.setAttribute('target', '_blank');
  }
}

const STATUS_LABEL = {
  pending: 'en attente',
  in_progress: 'en cours',
  completed: 'terminé',
  failed: 'échec',
};

const STOP_LABEL = {
  end_turn: null,
  cancelled: 'Tour arrêté.',
  max_tokens: 'Tour interrompu : limite de longueur atteinte.',
  max_turn_requests: 'Tour interrompu : limite d’appels atteinte.',
  refusal: 'L’agent a refusé de poursuivre.',
  error: 'Tour interrompu par une erreur.',
};

const DECISION_LABEL = {
  allow_once: 'Autoriser une fois',
  allow_always: 'Toujours',
  reject: 'Refuser',
};

/**
 * Monte le chat. `readOnly()` dit si l'hôte n'est qu'un témoin du projet (cockpit en
 * lecture seule) : le fil reste lisible, la saisie est fermée et aucune conversation ne démarre.
 */
export function createChat({ readOnly = () => false } = {}) {
  const log = mk('div', {
    class: 'chat-log',
    role: 'log',
    'aria-live': 'polite',
    'aria-relevant': 'additions text',
    'aria-label': 'Conversation avec l’orchestrateur',
    tabindex: '0',
  });
  const status = mk('div', { class: 'chat-status', 'aria-live': 'off' });
  const input = mk('textarea', {
    class: 'chat-input',
    id: 'chat-input',
    rows: '2',
    enterkeyhint: 'send',
    placeholder: 'Parler à l’orchestrateur — Entrée envoie, Maj+Entrée saute une ligne',
    autocomplete: 'off',
    spellcheck: 'true',
  });
  const send = mk('button', { type: 'submit', class: 'btn pri chat-send', text: 'Envoyer' });
  const stop = mk('button', { type: 'button', class: 'btn chat-stop', text: 'Stop', 'aria-label': 'Arrêter le tour en cours' });
  stop.hidden = true;
  const form = mk('form', { class: 'chat-form', 'aria-label': 'Message à l’orchestrateur' },
    mk('label', { for: 'chat-input', class: 'sr-only', text: 'Message à l’orchestrateur' }),
    input, mk('div', { class: 'chat-actions' }, send, stop));
  const hostSelect = mk('select', { class: 'chat-host', id: 'chat-host', 'aria-label': 'Hôte de l’orchestrateur' });
  const hostBar = mk('div', { class: 'chat-hostbar' },
    mk('label', { for: 'chat-host', class: 'chat-host-label', text: 'Hôte' }), hostSelect);
  const element = mk('section', { class: 'chat', 'aria-label': 'Orchestrateur' }, hostBar, status, log, form);

  let stream = null;
  let lastSeq = 0;
  let busy = false;
  let opened = false;
  /** Ce que le fil est en train d'accumuler : un message d'agent / de la personne / une réflexion. */
  let current = null;
  const tools = new Map();
  const permissions = new Map();
  const questions = new Map();
  const gestures = new Set();
  let summaryShown = false;
  /** La question du pilote à laquelle la prochaine saisie répond (ACP4), ou null. */
  let target = null;

  const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  const append = (node) => {
    const follow = nearBottom();
    log.append(node);
    if (follow) log.scrollTop = log.scrollHeight;
  };
  const scrollDown = () => { log.scrollTop = log.scrollHeight; };

  function setBusy(value) {
    busy = value;
    stop.hidden = !value;
    send.disabled = value || readOnly();
    element.setAttribute('aria-busy', String(value));
  }

  function setStatus(text) {
    status.textContent = text;
  }

  function note(text, kind = 'info') {
    current = null;
    append(mk('div', { class: `chat-note is-${kind}`, role: kind === 'error' ? 'alert' : 'note', text }));
  }

  // ── Messages : le texte arrive par morceaux, le markdown se rend sur le tout ─
  function addText(role, text) {
    const follow = nearBottom();
    if (!current || current.role !== role) {
      const body = mk('div', { class: 'chat-body' });
      const label = { agent: 'Orchestrateur', user: 'Vous', thought: 'Réflexion' }[role];
      let box;
      if (role === 'thought') {
        box = mk('details', { class: 'chat-msg is-thought' }, mk('summary', { text: label }), body);
      } else {
        box = mk('article', { class: `chat-msg is-${role}` }, mk('div', { class: 'chat-who', text: label }), body);
      }
      log.append(box);
      current = { role, body, text: '' };
    }
    current.text += text;
    if (role === 'agent') current.text = current.text.replace(/\[\[pilote:(on|off)\]\]/g, '');
    if (role === 'user') current.body.textContent = current.text;
    else markdownInto(current.body, current.text);
    if (follow) scrollDown();
  }

  // ── Appels d'outils, diffs, plans ───────────────────────────────────────
  function renderContent(items) {
    const out = [];
    for (const item of items || []) {
      if (item.type === 'diff') {
        const pre = mk('pre', { class: 'chat-diff', 'aria-label': `Diff de ${item.path}` });
        pre.append(mk('div', { class: 'chat-diff-path', text: item.path }));
        for (const line of String(item.oldText ?? '').split('\n')) if (line || item.oldText) pre.append(mk('div', { class: 'is-del', text: `- ${line}` }));
        for (const line of String(item.newText ?? '').split('\n')) pre.append(mk('div', { class: 'is-add', text: `+ ${line}` }));
        out.push(pre);
      } else if (item.type === 'content' && item.content && item.content.type === 'text') {
        out.push(mk('pre', { class: 'chat-tool-text', text: item.content.text }));
      } else if (item.type === 'terminal') {
        out.push(mk('div', { class: 'chat-tool-text', text: `terminal ${item.terminalId || ''}` }));
      }
    }
    return out;
  }

  function toolCall(update) {
    current = null;
    let entry = tools.get(update.toolCallId);
    if (!entry) {
      const summary = mk('summary');
      const body = mk('div', { class: 'chat-tool-body' });
      const box = mk('details', { class: 'chat-tool' }, summary, body);
      entry = { box, summary, body };
      tools.set(update.toolCallId, entry);
      append(box);
    }
    if (update.title) entry.title = update.title;
    if (update.status) entry.status = update.status;
    if (update.kind) entry.kind = update.kind;
    entry.summary.replaceChildren(
      mk('span', { class: 'chat-tool-kind', text: entry.kind || 'outil' }),
      mk('span', { class: 'chat-tool-title', text: entry.title || update.toolCallId }),
      mk('span', { class: `chat-tool-status is-${entry.status || 'pending'}`, text: STATUS_LABEL[entry.status] || entry.status || '' }),
    );
    if (update.content && update.content.length) entry.body.replaceChildren(...renderContent(update.content));
    if (update.rawInput && !entry.body.childElementCount) {
      entry.body.append(mk('pre', { class: 'chat-tool-text', text: JSON.stringify(update.rawInput, null, 2) }));
    }
  }

  let planBox = null;
  function plan(entries) {
    current = null;
    const list = mk('ol', { class: 'chat-plan-list' });
    for (const entry of entries || []) {
      list.append(mk('li', { class: `is-${entry.status || 'pending'}` },
        mk('span', { class: 'chat-plan-mark', 'aria-hidden': 'true', text: entry.status === 'completed' ? '✓' : entry.status === 'in_progress' ? '…' : '○' }),
        mk('span', { text: entry.content }),
        mk('span', { class: 'sr-only', text: ` (${STATUS_LABEL[entry.status] || entry.status || 'en attente'})` })));
    }
    if (!planBox) {
      planBox = mk('section', { class: 'chat-plan', 'aria-label': 'Plan de l’agent' }, mk('div', { class: 'chat-who', text: 'Plan' }), list);
      append(planBox);
    } else {
      planBox.replaceChild(list, planBox.querySelector('.chat-plan-list'));
    }
  }

  // ── Permissions ─────────────────────────────────────────────────────────
  function permission(event) {
    current = null;
    const call = event.tool_call || {};
    const kinds = new Set(event.options.map((o) => o.kind));
    const decisions = [];
    if (kinds.has('allow_once')) decisions.push('allow_once');
    if (kinds.has('allow_always')) decisions.push('allow_always');
    decisions.push('reject');
    const result = mk('div', { class: 'chat-perm-result', role: 'status' });
    const buttons = decisions.map((decision) => {
      const button = mk('button', { type: 'button', class: `btn chat-perm-btn${decision === 'reject' ? '' : ' pri'}`, 'data-decision': decision, text: DECISION_LABEL[decision] });
      button.addEventListener('click', async () => {
        for (const b of buttons) b.disabled = true;
        try {
          await api.chatPermission(event.request_id, decision);
        } catch (error) {
          for (const b of buttons) b.disabled = false;
          result.textContent = `Réponse non transmise : ${error.message}`;
        }
      });
      return button;
    });
    const box = mk('section', { class: 'chat-perm', role: 'group', 'aria-label': 'Demande de permission', 'data-request': event.request_id },
      mk('div', { class: 'chat-who', text: 'Permission demandée' }),
      mk('div', { class: 'chat-perm-title', text: call.title || call.toolCallId || 'Action de l’agent' }),
      mk('div', { class: 'chat-perm-actions' }, ...buttons), result);
    permissions.set(String(event.request_id), { box, buttons, result });
    append(box);
    buttons[0].focus({ preventScroll: true });
  }

  function permissionResolved(event) {
    const entry = permissions.get(String(event.request_id));
    if (!entry) return;
    for (const b of entry.buttons) b.disabled = true;
    const chosen = event.option_id ? event.option_id : null;
    entry.result.textContent = event.outcome === 'cancelled' ? 'Annulée : rien n’a été accordé.' : `Réponse envoyée à l’agent (${chosen}).`;
    entry.box.classList.add('is-done');
  }

  // ── Le pilote automatique (ACP4) ─────────────────────────────────────────
  function pilotSummary(event) {
    if (summaryShown) return;
    summaryShown = true;
    current = null;
    const list = mk('ul', { class: 'chat-pilot-list' });
    for (const d of event.decisions || []) {
      list.append(mk('li', {}, mk('strong', { text: `${d.task_id} — ` }), mk('span', { text: `${d.question} → ${d.option}` })));
    }
    const since = event.since ? ` depuis votre dernière visite` : '';
    append(mk('section', { class: 'chat-pilot', role: 'note', 'aria-label': 'Décisions du pilote' },
      mk('div', { class: 'chat-who', text: 'Pilote automatique' }),
      mk('p', { text: `${event.count} décision${event.count > 1 ? 's' : ''} prise${event.count > 1 ? 's' : ''}${since}.` }),
      list,
      mk('a', { class: 'chat-pilot-link', href: event.journal || '#journal', text: 'Décidé par le pilote — voir le journal' })));
  }

  function setTarget(entry) {
    target = entry;
    for (const other of questions.values()) other.box.classList.toggle('is-target', other === entry);
    input.placeholder = entry
      ? `Votre réponse à ${entry.task_id} — Entrée tranche la question`
      : 'Parler à l’orchestrateur — Entrée envoie, Maj+Entrée saute une ligne';
    if (entry) input.focus({ preventScroll: true });
  }

  function question(event) {
    const key = `${event.task_id}/${event.question_id}`;
    if (questions.has(key)) return;
    current = null;
    const reply = mk('button', { type: 'button', class: 'btn pri chat-question-reply', text: 'Répondre ici' });
    const state = mk('div', { class: 'chat-question-state', role: 'status' });
    const parts = [
      mk('div', { class: 'chat-who', text: 'Question pour vous' }),
      mk('div', { class: 'chat-question-text', text: event.text }),
      mk('div', { class: 'chat-question-kept', text: event.kept }),
    ];
    if (event.options && event.options.length) parts.push(mk('div', { class: 'chat-question-options', text: `Options : ${event.options.join(' · ')}` }));
    if (event.recommended) parts.push(mk('div', { class: 'chat-question-reco', text: `Recommandée : ${event.recommended}` }));
    const box = mk('section', { class: 'chat-question', role: 'group', 'aria-label': `Question sur ${event.task_id}`, 'data-question': key },
      ...parts, mk('div', { class: 'chat-question-actions' }, reply), state);
    const entry = { box, reply, state, task_id: event.task_id, question_id: event.question_id };
    questions.set(key, entry);
    reply.addEventListener('click', () => setTarget(target === entry ? null : entry));
    append(box);
  }

  function questionResolved(event) {
    const entry = questions.get(`${event.task_id}/${event.question_id}`);
    if (!entry) return;
    entry.box.classList.add('is-done');
    entry.reply.disabled = true;
    entry.state.textContent = 'Tranchée.';
    if (target === entry) setTarget(null);
  }

  function gesture(event) {
    const key = event.on ? 'on' : 'off';
    if (gestures.has(key) && !event.seq) return;
    gestures.add(key);
    current = null;
    const label = event.on ? 'Activer le pilote' : 'Couper le pilote';
    const keyField = mk('input', { type: 'password', class: 'chat-gesture-key', autocomplete: 'off', 'aria-label': 'Clé de contrôle', placeholder: 'Clé de contrôle' });
    const button = mk('button', { type: 'button', class: 'btn pri chat-gesture-btn', 'data-pilot': key, text: label });
    const result = mk('div', { class: 'chat-gesture-result', role: 'status' });
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await api.autopilotSet(event.on, keyField.value);
        keyField.value = '';
        result.textContent = event.on ? 'Pilote activé.' : 'Pilote coupé.';
      } catch (error) {
        button.disabled = false;
        result.textContent = `Geste refusé : ${error.message}`;
      }
    });
    append(mk('section', { class: 'chat-gesture', role: 'group', 'aria-label': 'Geste proposé par l’orchestrateur' },
      mk('div', { class: 'chat-who', text: 'L’orchestrateur propose' }),
      mk('div', { text: `${label} ? Le geste reste le vôtre : saisissez la clé de contrôle et cliquez.` }),
      mk('div', { class: 'chat-gesture-actions' }, keyField, button), result));
  }

  // ── L'hôte (ACP3) : réglage du projet, hôte détecté par défaut ───────────
  async function loadHosts() {
    let state;
    try { state = await api.chatHosts(); } catch { hostBar.hidden = true; return; }
    hostBar.hidden = false;
    hostSelect.replaceChildren();
    for (const host of state.hosts) {
      const flags = [host.preview ? 'preview' : '', host.detected ? 'détecté' : ''].filter(Boolean);
      const label = host.display_name + (flags.length ? ` (${flags.join(', ')})` : '')
        + (host.available ? '' : ` — ${host.reason}`);
      const option = mk('option', { value: host.alias, text: label });
      option.disabled = !host.available;
      option.selected = host.selected;
      if (host.note) option.title = host.note;
      hostSelect.append(option);
    }
    if (state.mode === 'kanban') {
      setStatus(`Mode différé : ${state.reason || 'le chat écrit dans le kanban'}. Les réponses arrivent par les notes des sessions.`);
    }
  }

  hostSelect.addEventListener('change', async () => {
    hostSelect.disabled = true;
    try {
      await api.chatHost(hostSelect.value);
      note('Hôte changé : la prochaine conversation démarre sur ce nouvel hôte.');
    } catch (error) {
      note(`Hôte non changé : ${error.message}`, 'error');
    } finally {
      hostSelect.disabled = false;
      loadHosts();
    }
  });

  // ── Le flux ─────────────────────────────────────────────────────────────
  /** L'ouverture du pilote arrive hors journal (seq 0) : elle ne touche pas au compteur de rejeu. */
  function handle0(event) {
    if (event.type === 'pilot_summary') pilotSummary(event);
    else question(event);
  }

  function handle(event) {
    if (event.type === 'reply' || event.type === 'kanban_sent') {
      if (event.seq && event.seq <= lastSeq) return;
      if (event.seq) lastSeq = event.seq;
      if (event.type === 'kanban_sent') {
        note(`Consigne déposée sur ${event.task_id} : la session la lira à son prochain tour, sa réponse apparaîtra ici.`);
      } else {
        current = null;
        addText('agent', `**${event.actor || 'session'}** (${event.task_id}) — ${event.text}`);
        current = null;
      }
      return;
    }
    if (event.seq === 0 && ['pilot_summary', 'question'].includes(event.type)) { handle0(event); return; }
    if (event.seq && event.seq <= lastSeq) return; // un rejeu après reconnexion
    if (event.seq) lastSeq = event.seq;
    switch (event.type) {
      case 'hello':
        setBusy(Boolean(event.state && event.state.busy));
        setStatus(event.state && event.state.running ? 'Orchestrateur connecté.' : 'Orchestrateur au repos : le premier message le lance.');
        break;
      case 'status':
        setStatus(event.state === 'resuming' ? 'Reprise de la conversation…' : 'Démarrage de l’agent…');
        break;
      case 'ready':
        setStatus(event.resumed ? 'Conversation reprise.' : 'Nouvelle conversation.');
        if (!event.resumed) note('Nouvelle conversation : la continuité passe par la mémoire du projet et le kanban, pas par le transcript.');
        break;
      case 'user':
        current = null;
        addText('user', event.text);
        current = null;
        break;
      case 'turn_started':
        setBusy(true);
        break;
      case 'update': {
        const update = event.update || {};
        const kind = update.sessionUpdate;
        const text = update.content && update.content.type === 'text' ? update.content.text : null;
        if (kind === 'agent_message_chunk' && text != null) addText('agent', text);
        else if (kind === 'agent_thought_chunk' && text != null) addText('thought', text);
        else if (kind === 'user_message_chunk' && text != null) addText('user', text);
        else if (kind === 'tool_call' || kind === 'tool_call_update') toolCall(update);
        else if (kind === 'plan') plan(update.entries);
        break;
      }
      case 'pilot_summary':
        pilotSummary(event);
        break;
      case 'question':
        question(event);
        break;
      case 'question_resolved':
        questionResolved(event);
        break;
      case 'pilot_gesture':
        gesture(event);
        break;
      case 'permission':
        permission(event);
        break;
      case 'permission_resolved':
        permissionResolved(event);
        break;
      case 'turn_ended': {
        current = null;
        planBox = null;
        setBusy(false);
        const label = STOP_LABEL[event.stop_reason];
        if (label) note(label, event.stop_reason === 'error' ? 'error' : 'info');
        element.dispatchEvent(new CustomEvent('chat:turn-ended', { bubbles: true, detail: { stopReason: event.stop_reason } }));
        break;
      }
      case 'error':
        note(event.message || 'Erreur de l’agent.', 'error');
        break;
      default:
        break;
    }
  }

  function connect() {
    if (typeof EventSource !== 'function') {
      setStatus('Ce navigateur ne sait pas lire le flux : le chat est indisponible.');
      return;
    }
    stream = new EventSource(api.chatStreamUrl(true));
    stream.addEventListener('chat', (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch { return; }
      handle(event);
    });
    stream.onerror = () => {
      if (stream && stream.readyState === EventSource.CONNECTING) setStatus('Flux interrompu, reconnexion…');
    };
  }

  // ── Saisie ──────────────────────────────────────────────────────────────
  async function submit() {
    const text = input.value.trim();
    if (!text || busy || readOnly()) return;
    input.value = '';
    const answering = target;
    if (answering) {
      try {
        await api.chatSend(text, { task_id: answering.task_id, question_id: answering.question_id });
        answering.state.textContent = 'Réponse transmise : tranchée par vous.';
        setTarget(null);
      } catch (error) {
        input.value = text;
        note(`Réponse non transmise : ${error.message}`, 'error');
      }
      return;
    }
    setBusy(true);
    try {
      await api.chatSend(text);
    } catch (error) {
      setBusy(false);
      input.value = text;
      note(`Message non transmis : ${error.message}`, 'error');
    }
  }

  form.addEventListener('submit', (event) => { event.preventDefault(); submit(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });
  stop.addEventListener('click', async () => {
    stop.disabled = true;
    try { await api.chatCancel(); } catch (error) { note(`Arrêt non transmis : ${error.message}`, 'error'); } finally { stop.disabled = false; }
  });

  return {
    element,
    /** À l'affichage de l'onglet : ouvre le flux la première fois, remet le focus dans la saisie. */
    open() {
      if (!opened) {
        opened = true;
        if (readOnly()) {
          input.disabled = true;
          send.disabled = true;
          setStatus('Lecture seule : ce projet n’est que consulté. Ouvrez le cockpit depuis lui pour parler à son orchestrateur.');
        } else {
          setBusy(false);
          setStatus('Connexion à l’orchestrateur…');
          loadHosts();
          connect();
        }
      }
      if (!readOnly()) queueMicrotask(() => input.focus({ preventScroll: true }));
      scrollDown();
    },
    close() {
      if (stream) stream.close();
      stream = null;
      opened = false;
    },
  };
}
