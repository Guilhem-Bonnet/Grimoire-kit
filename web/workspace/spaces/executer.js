// Espace Exécuter — LOT 4.
//
// Remplace `kanban.html`. Cible : tâches en Board 4 ou 8 colonnes, Liste,
// Timeline ; la porte de chaque colonne en une ligne sous son titre ; carte de
// tâche à trois niveaux. Inspecteur : tâche — critères, preuves, prochaine
// porte avec bouton, timeline.
//
// Corrections dues par la revue §4.3 : huit états annoncés, huit accessibles
// (Board 8, ou la mention « colonnes repliées » sur Board 4) ; la transition
// suivante est l'information la plus grande, pas la plus petite (8,8 px) ;
// colonnes vides en pointillé.
//
// API consommées : api.tasks(), api.task(id), api.taskTrace(id),
// api.taskRecall(id), api.taskAction(id, 'claim'|'move'|'block'|'close', body).
// Le refus d'un gate revient en 200 avec `blocked: true` et la preuve
// manquante nommée — c'est une réponse à afficher, pas une erreur à avaler.
//
// Pilotage humain (issue #638, lot B) : les mêmes `api.taskAction` avec
// 'prioritize' | 'comment' | 'cancel' | 'ack' — jamais une écriture directe
// depuis la webview (ADR-007), toujours `TaskService`. Sélecteur de priorité
// et zone de consigne dans l'inspecteur, « Annuler » avec raison exigée, tri
// par priorité dans chaque colonne, badge « consigne non lue » sur la carte.
//
// Timeline (#139) : `api.taskTrace(id)` porte maintenant jusqu'à cinq sources
// (ledger, hooks, gate, runtime, evidence, otel) triées dans le temps —
// filtrables par source et par gravité, chaque ligne s'ouvrant en accordéon
// sur son détail brut. Drill-down depuis l'inspecteur d'une carte (« Voir la
// timeline ») ou depuis `ctx.params = { task, view: 'timeline' }` qu'un
// `goto('executer', …)` externe (Observer) peut poser.
//
// Portefeuille (#638, lot C) : la portée « Tous les projets » lit
// `api.portfolioTasks()` — toutes les tâches de tous les projets du registre
// cockpit, agrégées côté serveur — avec une puce projet par carte, la session
// qui la porte (point + mot : vivante / inactive), sa priorité et ses
// consignes non lues quand le champ existe. Les cartes se clé-ent par
// (projet, id) : deux ledgers peuvent dériver le même identifiant d'un même
// titre. Une action depuis le portefeuille passe par
// `api.portfolioTaskAction(id, action, body, slug)` — le `TaskService` du
// projet propriétaire, gate compris — et « Reprendre la session » ne fait
// que copier `claude --resume <id>` (hôte Claude Code) ou afficher
// l'identifiant : jamais d'exécution depuis la webview (ADR-007).

const STYLE_ID = 'ex-styles';

// Portée et filtres du portefeuille — au niveau du module, comme
// `timelineFilter` : revenir sur l'espace garde le dernier réglage.
const scope = { mode: 'project', state: '', slug: '', live: false };

// Colonnes du standard, dans l'ordre normatif (grimoire.missions.board.BOARD_LIFECYCLE).
const LIFECYCLE = ['proposed', 'ready', 'in_progress', 'blocked', 'review', 'accepted', 'released', 'archived'];
const COLUMN_LABEL = {
  proposed: 'Proposée', ready: 'Prête', in_progress: 'En cours', blocked: 'Bloquée',
  review: 'Revue', accepted: 'Acceptée', released: 'Publiée', archived: 'Archivée',
};
// Board à 4 colonnes : regroupement des 8 états du standard. Les colonnes
// repliées sont nommées sous le titre, jamais tues (revue §4.3).
const GROUPS4 = [
  { id: 'todo', label: 'À faire', cols: ['proposed', 'ready'] },
  { id: 'doing', label: 'En cours', cols: ['in_progress', 'blocked'] },
  { id: 'review', label: 'Revue', cols: ['review'] },
  { id: 'done', label: 'Fait', cols: ['accepted', 'released', 'archived'] },
];
// Board → état ledger, pour appeler `move` (grimoire.missions.board._FROM_BOARD).
const BOARD_TO_STATE = {
  proposed: 'proposed', ready: 'ready', in_progress: 'running', blocked: 'blocked',
  review: 'needs_verification', accepted: 'closed', released: 'closed', archived: 'cancelled',
};
// `finition` (grimoire.missions.schemas.MissionTask, lot 4.3, issue #561), en
// clair — lecture seule ici, aucune valeur pour "" (le champ n'est envoyé par
// `to_dict()` que quand il est posé).
const FINITION_LABEL = { maquette: 'Maquette', peaufine: 'Peaufinée' };
// Nature d'une dépendance (grimoire.missions.schemas.DependencyKind), en clair.
const DEP_LABEL = {
  blocks: 'bloque', relates: 'lié à', parent_child: 'parent / enfant',
  discovered_from: 'découverte depuis', supersedes: 'remplace',
};
// `risk_profile` (grimoire.missions.schemas.RiskProfile) rendait en texte nu
// dans le méta de carte (revue 2026-09) — aucune couleur alors que c'est une
// vraie échelle de sévérité (grimoire.missions.board._PRIORITY_BY_RISK :
// light → low, standard → medium, le reste → high). Point + mot, comme tout
// le reste des états — jamais la couleur seule.
const RISK_DOT = {
  light: 'ok', standard: 'warn', strict: 'bad', security_critical: 'bad', release: 'bad',
};
// Échelle de priorité (grimoire.missions.board.PRIORITIES), la plus pressante
// en dernier ; `tasks_view()` la renvoie aussi (`priorities`) — la constante
// locale n'est que le repli si le serveur ne la donne pas.
const PRIORITIES = ['low', 'medium', 'high', 'critical'];
const PRIORITY_LABEL = { low: 'Basse', medium: 'Moyenne', high: 'Haute', critical: 'Critique' };
const PRIORITY_DOT = { low: '', medium: '', high: 'warn', critical: 'bad' };
const priorityRank = (p) => PRIORITIES.indexOf(p);
// Dans une colonne, la plus pressante d'abord ; à égalité, l'identifiant
// garde un ordre stable (même règle que `build_board`).
const byPriority = (a, b) =>
  (priorityRank(b.effective_priority || 'medium') - priorityRank(a.effective_priority || 'medium')) ||
  String(a.id).localeCompare(String(b.id));

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ex-wrap { padding: var(--sp-4); height: 100%; display: flex; flex-direction: column; gap: var(--sp-3); }
    .ex-board { display: flex; gap: var(--sp-3); align-items: flex-start; overflow-x: auto; flex-grow: 1; }
    .ex-col { width: 260px; flex: none; display: flex; flex-direction: column; gap: var(--sp-2); }
    .ex-col-head { padding: 6px 2px; }
    .ex-col-title { font-size: var(--t-s); font-weight: 500; color: var(--ink); display: flex; justify-content: space-between; }
    .ex-col-gate { font-size: var(--t-min); color: var(--ink3); margin-top: 2px; }
    .ex-col-body { display: flex; flex-direction: column; gap: 6px; min-height: 60px; }
    .ex-col-body.empty { border: 1px dashed var(--line); border-radius: var(--r); align-items: center; justify-content: center; color: var(--ink3); font-size: var(--t-min); padding: var(--sp-4) 0; }
    .ex-card { border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); padding: var(--sp-2) var(--sp-3); cursor: pointer; display: flex; flex-direction: column; gap: 6px; }
    .ex-card:hover { background: var(--e2); }
    .ex-card[aria-current="true"] { outline: 2px solid var(--acc); outline-offset: -2px; }
    .ex-card-title { font-size: var(--t-s); font-weight: 500; color: var(--ink); }
    .ex-card-meta { font-size: var(--t-s); color: var(--ink2); display: flex; align-items: center; gap: 6px; }
    .ex-card-chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .ex-card-next { font-size: var(--t-s); color: var(--ink2); display: flex; justify-content: space-between; align-items: center; gap: 6px; border-top: 1px solid var(--line); padding-top: 6px; }
    .ex-list-wrap { overflow: auto; border: 1px solid var(--line); border-radius: var(--r); }
    .ex-list { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    .ex-list th { text-align: left; font-size: var(--t-min); color: var(--ink3); font-weight: 500; padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); background: var(--bar); }
    .ex-list td { padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); }
    .ex-list tbody tr { cursor: pointer; }
    .ex-list tbody tr:hover { background: var(--e2); }
    .ex-timeline { display: flex; flex-direction: column; gap: 6px; padding: var(--sp-2) 0; }
    .ex-tl-filters { display: flex; gap: var(--sp-2); align-items: center; padding-bottom: var(--sp-2); }
    .ex-tl-filters select { font-size: var(--t-min); }
    .ex-tl-count { font-size: var(--t-min); color: var(--ink3); margin-left: auto; }
    .ex-tl-entry { display: flex; flex-direction: column; gap: 4px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); cursor: pointer; }
    .ex-tl-entry:hover { background: var(--e2); }
    .ex-tl-entry.fail { border-color: var(--bad); }
    .ex-tl-row { display: flex; gap: var(--sp-3); align-items: flex-start; }
    .ex-tl-at { font-family: var(--mono); font-size: var(--t-min); color: var(--ink3); width: 150px; flex: none; }
    .ex-tl-detail { margin: 0; padding: var(--sp-2) var(--sp-3); border-top: 1px solid var(--line); background: var(--e2); font-family: var(--mono); font-size: var(--t-min); white-space: pre-wrap; word-break: break-word; }
    .ex-insp-block { margin-bottom: var(--sp-4); }
    .ex-insp-block h4 { font-size: var(--t-min); color: var(--ink3); margin: 0 0 6px; font-weight: 500; }
    .ex-insp-block ul { margin: 0; padding-left: 18px; font-size: var(--t-s); }
    .ex-recall { margin: 0; padding: var(--sp-2) var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); font-family: var(--mono); font-size: var(--t-min); white-space: pre-wrap; word-break: break-word; }
    .ex-gate-row { display: flex; flex-direction: column; gap: 4px; padding: 8px; border: 1px solid var(--line); border-radius: var(--r); margin-bottom: 6px; }
    .ex-gate-req { font-size: var(--t-min); color: var(--ink3); }
    .ex-session { font-family: var(--mono); font-size: var(--t-min); color: var(--ink2); display: flex; align-items: center; gap: 6px; }
    .ex-session code { font-family: var(--mono); font-size: var(--t-min); padding: 1px 4px; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); word-break: break-all; }
    .ex-refusal { color: var(--bad); font-size: var(--t-s); margin-top: 6px; }
    .ex-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-3); font-size: var(--t-s); color: var(--ink2); }
    .ex-toolbar label { display: inline-flex; align-items: center; gap: 6px; }
    .ex-toolbar select, .ex-toolbar input[type="checkbox"] { font-size: var(--t-s); }
    .ex-toolbar .ex-count { margin-left: auto; color: var(--ink3); font-size: var(--t-min); }
    .ex-notice { display: flex; flex-direction: column; gap: 4px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); font-size: var(--t-s); }
    .ex-card-project { font-size: var(--t-s); color: var(--ink2); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .ex-card-project .chip { font-size: var(--t-s); }
    .ex-session-cmd { margin: 6px 0 0; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e2); font-family: var(--mono); font-size: var(--t-s); white-space: pre-wrap; word-break: break-all; }
    .ex-badge { font-size: var(--t-min); border: 1px solid var(--acc); color: var(--acc); border-radius: var(--r); padding: 0 6px; }
    .ex-directive { display: flex; flex-direction: column; gap: 2px; padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); margin-bottom: 6px; font-size: var(--t-s); }
    .ex-directive.unread { border-color: var(--acc); }
    .ex-directive-meta { font-size: var(--t-min); color: var(--ink3); display: flex; gap: 6px; align-items: center; }
    .ex-steer-form { display: flex; flex-direction: column; gap: 6px; }
    .ex-steer-form textarea { min-height: 56px; font: inherit; font-size: var(--t-s); }
    .ex-steer-row { display: flex; gap: 6px; align-items: center; }
  `;
  document.head.append(style);
}

const dot = (cls) => Object.assign(document.createElement('span'), { className: 'dot' + (cls ? ' ' + cls : '') });
function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}
function row(...children) { const d = document.createElement('div'); d.className = 'row'; d.append(...children); return d; }
function chip(label, present) {
  const c = document.createElement('span');
  c.className = 'chip';
  c.append(dot(present ? 'ok' : ''), text('span', null, label));
  return c;
}

// Le nom court d'un hôte (grimoire.bridges.schemas.HostId) — jamais deviné :
// une valeur inconnue s'affiche telle quelle.
const HOST_LABEL = {
  'host-claude-code-cli': 'Claude Code', 'host-github-copilot': 'Copilot',
  'host-codex': 'Codex', 'host-cursor': 'Cursor', 'host-gemini-cli': 'Gemini CLI',
};

// Une ligne « session » : identifiant (tronqué sur la carte, entier dans
// l'inspecteur) + hôte. `resume_command` n'existe que pour un hôte dont le
// kit connaît la commande (`grimoire.missions.session_link.RESUME_COMMANDS`) ;
// pour les autres, l'identifiant seul — rien n'est inventé côté vue.
function sessionLine(task, { full = false } = {}) {
  const line = document.createElement('div');
  line.className = 'ex-session';
  const id = full ? task.session_id : `${task.session_id.slice(0, 8)}…`;
  line.append(text('span', 'lbl', 'session'), Object.assign(document.createElement('code'), { textContent: id, title: task.session_id }));
  if (task.host) line.append(text('span', 'lbl', HOST_LABEL[task.host] || task.host));
  return line;
}

function nextColumn(column) {
  const index = LIFECYCLE.indexOf(column);
  return index >= 0 && index < LIFECYCLE.length - 1 ? LIFECYCLE[index + 1] : null;
}

// Priorité effective (lot B la calcule côté serveur : `effective_priority`) — point + mot.
const PORTFOLIO_PRIORITY_LABEL = { low: 'priorité basse', medium: 'priorité moyenne', high: 'priorité haute', critical: 'priorité critique' };
const PORTFOLIO_PRIORITY_DOT = { low: '', medium: '', high: 'warn', critical: 'bad' };

// La ligne « portefeuille » d'une carte : projet, session (point + mot),
// priorité, consignes non lues — chaque état est un point ET un mot, jamais
// la couleur seule (revue 2026-09).
function portfolioRow(task) {
  const line = document.createElement('div');
  line.className = 'ex-card-project';
  const project = document.createElement('span');
  project.className = 'chip';
  project.append(dot('acc'), text('span', null, task.project?.name || task.project?.slug || 'projet'));
  line.append(project);
  const session = task.session;
  if (session && session.id) {
    line.append(row(dot(session.live ? 'ok' : ''), text('span', null, session.live ? 'session vivante' : 'session inactive')));
  } else {
    line.append(text('span', 'lbl', 'aucune session'));
  }
  if (task.priority) {
    line.append(row(dot(PORTFOLIO_PRIORITY_DOT[task.priority] || ''), text('span', null, PORTFOLIO_PRIORITY_LABEL[task.priority] || `priorité ${task.priority}`)));
  }
  if (task.unread_directives) {
    line.append(row(dot('warn'), text('span', null, `${task.unread_directives} consigne(s) non lue(s)`)));
  }
  return line;
}

function taskCard(task, onSelect, portfolio = false) {
  const card = document.createElement('div');
  card.className = 'ex-card';
  card.tabIndex = 0;
  card.dataset.project = task.project?.slug || '';
  card.dataset.task = task.id;
  card.addEventListener('click', () => onSelect(task));
  card.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(task); });

  card.append(text('div', 'ex-card-title', task.title || task.id));
  if (portfolio) card.append(portfolioRow(task));
  const meta = text('div', 'ex-card-meta lbl', '');
  if (task.risk_profile) meta.append(dot(RISK_DOT[task.risk_profile] || ''));
  meta.append(text('span', null, [task.owner || 'sans owner', task.type, task.risk_profile].filter(Boolean).join(' · ')));
  // Priorité effective (issue #638) : point + mot, comme le risque — jamais la
  // couleur seule ; et le badge « consigne non lue » tant qu'une consigne de
  // l'orchestrateur n'a pas été livrée à la session.
  const priority = task.effective_priority || 'medium';
  meta.append(dot(PRIORITY_DOT[priority] || ''), text('span', null, PRIORITY_LABEL[priority] || priority));
  if (task.directives_pending > 0) {
    const badge = text('span', 'ex-badge', `consigne non lue (${task.directives_pending})`);
    badge.dataset.role = 'directive-badge';
    meta.append(badge);
  } else if (task.directives_unacknowledged > 0) {
    meta.append(text('span', 'ex-badge', `consigne non accusée (${task.directives_unacknowledged})`));
  }
  card.append(meta);

  const chips = document.createElement('div');
  chips.className = 'ex-card-chips';
  for (const evidence of (task.expected_evidence || []).slice(0, 4)) chips.append(chip(evidence, false));
  if (!(task.expected_evidence || []).length) chips.append(text('span', 'lbl', 'aucune preuve déclarée'));
  card.append(chips);

  // Session qui porte la carte (issue #638, lot A) : `session_id` et `host`
  // viennent du claim, posés par le hook UserPromptSubmit de la session —
  // absents tant qu'aucune session d'hôte n'a travaillé sous ce claim.
  if (task.session_id) card.append(sessionLine(task));

  const next = nextColumn(task.board);
  const nextRow = document.createElement('div');
  nextRow.className = 'ex-card-next';
  nextRow.append(text('span', null, next ? `→ ${COLUMN_LABEL[next]}` : 'colonne terminale'));
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn';
  btn.textContent = 'Voir la porte';
  btn.addEventListener('click', (e) => { e.stopPropagation(); onSelect(task); });
  nextRow.append(btn);
  card.append(nextRow);
  return card;
}

function renderBoard(root, ctx, tasks, groups, onSelect, portfolio = false) {
  const board = document.createElement('div');
  board.className = 'ex-board';
  const byColumn = new Map();
  for (const t of tasks) {
    if (!byColumn.has(t.board)) byColumn.set(t.board, []);
    byColumn.get(t.board).push(t);
  }
  for (const group of groups) {
    const col = document.createElement('div');
    col.className = 'ex-col';
    const inColumn = group.cols.flatMap((c) => byColumn.get(c) || []).sort(byPriority);

    const head = document.createElement('div');
    head.className = 'ex-col-head';
    const title = document.createElement('div');
    title.className = 'ex-col-title';
    title.append(text('span', null, group.label), text('span', 'mono lbl', String(inColumn.length)));
    if (group.cols.length > 1) title.dataset.term = 'porte-de-preuve';
    head.append(title);
    if (group.cols.length > 1) {
      head.append(text('div', 'ex-col-gate', 'colonnes repliées : ' + group.cols.map((c) => COLUMN_LABEL[c]).join(', ')));
    } else {
      const evidences = [...new Set(inColumn.flatMap((t) => t.expected_evidence || []))];
      head.append(text('div', 'ex-col-gate', evidences.length ? `porte : requiert ${evidences.slice(0, 3).join(', ')}` : 'porte : aucune preuve déclarée'));
    }
    col.append(head);

    const body = document.createElement('div');
    body.className = 'ex-col-body' + (inColumn.length ? '' : ' empty');
    if (!inColumn.length) {
      body.append(text('span', null, 'vide'));
    } else {
      for (const task of inColumn) body.append(taskCard(task, onSelect, portfolio));
    }
    col.append(body);
    board.append(col);
  }
  root.append(board);
}

function renderList(root, ctx, tasks, onSelect, portfolio = false) {
  const wrap = document.createElement('div');
  wrap.className = 'ex-list-wrap';
  const table = document.createElement('table');
  table.className = 'ex-list';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const columns = portfolio
    ? ['Projet', 'Tâche', 'État', 'Session', 'Priorité', 'Owner', 'Prochaine porte']
    : ['Tâche', 'État', 'Priorité', 'Owner', 'Session', 'Preuves', 'Prochaine porte'];
  for (const label of columns) headRow.append(text('th', null, label));
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  const ordered = [...tasks].sort((a, b) => (LIFECYCLE.indexOf(a.board) - LIFECYCLE.indexOf(b.board)) || byPriority(a, b));
  for (const task of ordered) {
    const tr = document.createElement('tr');
    tr.dataset.project = task.project?.slug || '';
    tr.dataset.task = task.id;
    tr.addEventListener('click', () => onSelect(task));
    if (portfolio) tr.append(text('td', null, task.project?.name || task.project?.slug || '—'));
    tr.append(text('td', null, task.title || task.id));
    const stateCell = document.createElement('td');
    stateCell.append(row(dot(task.board === 'blocked' ? 'bad' : (task.board === 'accepted' || task.board === 'released' ? 'ok' : '')), text('span', null, COLUMN_LABEL[task.board] || task.board)));
    tr.append(stateCell);
    if (portfolio) {
      const sessionCell = document.createElement('td');
      const session = task.session;
      if (session && session.id) {
        sessionCell.append(row(dot(session.live ? 'ok' : ''), text('span', null, session.live ? 'vivante' : 'inactive')));
      } else {
        sessionCell.append(text('span', 'lbl', '—'));
      }
      tr.append(sessionCell);
      const prioCell = document.createElement('td');
      if (task.priority) prioCell.append(row(dot(PORTFOLIO_PRIORITY_DOT[task.priority] || ''), text('span', null, task.priority)));
      else prioCell.append(text('span', 'lbl', '—'));
      tr.append(prioCell);
    } else {
      const prio = task.effective_priority || 'medium';
      const prioCell = document.createElement('td');
      prioCell.append(row(dot(PRIORITY_DOT[prio] || ''), text('span', null, PRIORITY_LABEL[prio] || prio)));
      tr.append(prioCell);
    }
    tr.append(text('td', null, task.owner || '—'));
    if (!portfolio) {
      const sessionCell = document.createElement('td');
      if (task.session_id) sessionCell.append(sessionLine(task));
      else sessionCell.append(text('span', 'lbl', '—'));
      tr.append(sessionCell);
      const evCell = document.createElement('td');
      evCell.append(row(...(task.expected_evidence || []).slice(0, 3).map((e) => chip(e, false))));
      if (!(task.expected_evidence || []).length) evCell.append(text('span', 'lbl', '—'));
      tr.append(evCell);
    }
    const next = nextColumn(task.board);
    tr.append(text('td', 'lbl', next ? `→ ${COLUMN_LABEL[next]}` : '—'));
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  root.append(wrap);
}

// État des filtres de la Timeline — vit au niveau du module : changer de
// tâche ou revenir sur la vue garde le dernier filtre choisi, comme les
// autres réglages d'affichage de cet espace (vue, sélection).
const timelineFilter = { source: 'tous', gravite: 'tous' };

function timelineEntryNode(entry) {
  const node = document.createElement('div');
  node.className = 'ex-tl-entry' + (entry.failure ? ' fail' : '');
  const head = document.createElement('div');
  head.className = 'ex-tl-row';
  head.append(text('div', 'ex-tl-at mono', entry.at || '—'));
  const body = document.createElement('div');
  body.append(row(dot(entry.failure ? 'bad' : 'ok'), text('span', null, entry.summary || entry.kind)));
  body.append(text('div', 'lbl', `${entry.source} · ${entry.kind}`));
  head.append(body);
  node.append(head);

  // Le détail brut (identifiants de trace, tags, span OTel…) s'ouvre en
  // accordéon sous la ligne : le panneau d'inspecteur de cet espace reste
  // occupé par la tâche sélectionnée (transitions, preuves, porte suivante),
  // quelle que soit la vue active — ouvrir le détail d'une ligne ne doit pas
  // le lui retirer. Jamais le contenu d'un prompt : `entry.detail` ne porte
  // que des identifiants, des tags et des résumés déjà affichés ailleurs.
  let expanded = false;
  node.addEventListener('click', () => {
    expanded = !expanded;
    const already = node.querySelector('.ex-tl-detail');
    if (already) { already.remove(); if (!expanded) return; }
    if (!expanded) return;
    const hasDetail = entry.detail && Object.keys(entry.detail).length;
    const pre = document.createElement('pre');
    pre.className = 'ex-tl-detail';
    pre.textContent = hasDetail ? JSON.stringify(entry.detail, null, 2) : 'Aucun détail supplémentaire.';
    node.append(pre);
  });
  return node;
}

function timelineFilterBar(sources, onChange) {
  const bar = document.createElement('div');
  bar.className = 'ex-tl-filters';

  const sourceSelect = document.createElement('select');
  sourceSelect.className = 'input';
  for (const value of ['tous', ...sources]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value === 'tous' ? 'Toutes les sources' : value;
    if (value === timelineFilter.source) option.selected = true;
    sourceSelect.append(option);
  }
  sourceSelect.addEventListener('change', () => { timelineFilter.source = sourceSelect.value; onChange(); });

  const graviteSelect = document.createElement('select');
  graviteSelect.className = 'input';
  for (const [value, label] of [['tous', 'Toutes les gravités'], ['causes', 'Causes seulement']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (value === timelineFilter.gravite) option.selected = true;
    graviteSelect.append(option);
  }
  graviteSelect.addEventListener('change', () => { timelineFilter.gravite = graviteSelect.value; onChange(); });

  bar.append(sourceSelect, graviteSelect);
  return bar;
}

async function renderTimeline(root, ctx, task) {
  const trace = await ctx.api.taskTrace(task.id, task.project?.slug).catch(() => null);
  const wrap = document.createElement('div');
  wrap.className = 'ex-timeline';
  const allEntries = trace?.entries || [];
  if (!trace || !allEntries.length) {
    const sourcesLues = trace ? Object.entries(trace.sources || {}) : [];
    const lues = sourcesLues.filter(([, path]) => path).map(([name]) => name);
    const absentes = sourcesLues.filter(([, path]) => !path).map(([name]) => name);
    wrap.append(ctx.empty(
      'Timeline',
      lues.length || absentes.length
        ? `Aucun événement pour cette tâche. Sources lues : ${lues.join(', ') || 'aucune'}` +
          (absentes.length ? ` — absentes : ${absentes.join(', ')}.` : '.')
        : "Aucun journal ne mentionne cette tâche : ni le Mission Ledger, ni le TraceLedger, ni le runtime.",
      `grimoire task trace ${task.id}`,
    ));
  } else {
    const sources = [...new Set(allEntries.map((e) => e.source))].sort();
    const list = document.createElement('div');
    const draw = () => {
      list.replaceChildren();
      const filtered = allEntries.filter((e) => (
        (timelineFilter.source === 'tous' || e.source === timelineFilter.source) &&
        (timelineFilter.gravite === 'tous' || e.failure)
      ));
      for (const entry of filtered) list.append(timelineEntryNode(entry));
      if (!filtered.length) list.append(text('p', 'lbl', 'Aucun événement pour ce filtre.'));
      count.textContent = `${filtered.length}/${allEntries.length}`;
    };
    const bar = timelineFilterBar(sources, draw);
    const count = text('span', 'ex-tl-count', '');
    bar.append(count);
    wrap.append(bar, list);
    draw();
  }
  root.append(wrap);
  ctx.dock.log('traces', ...allEntries.map((e) => `${e.at} · ${e.source} · ${e.summary}`));
}

// ── Inspecteur ────────────────────────────────────────────────────────────

// « Reprendre la session » (#638) : copie la commande sur l'hôte Claude Code,
// affiche l'identifiant ailleurs — jamais d'exécution depuis la webview.
function sessionBlock(session) {
  const block = document.createElement('div');
  block.className = 'ex-insp-block ex-session';
  block.append(text('h4', null, 'Session'));
  block.append(row(
    dot(session.live ? 'ok' : ''),
    text('span', null, `${session.live ? 'vivante' : 'inactive'} · ${session.id}` + (session.host ? ` · ${session.host}` : '')),
  ));
  if (session.updated_at) block.append(text('div', 'lbl', `dernier signe : ${session.updated_at}`));
  const feedback = text('div', 'lbl', '');
  if (session.resume_command) {
    const cmd = document.createElement('pre');
    cmd.className = 'ex-session-cmd';
    cmd.textContent = session.resume_command;
    block.append(cmd);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Reprendre la session (copier la commande)';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(session.resume_command);
        feedback.textContent = 'commande copiée — à coller dans un terminal';
      } catch {
        feedback.textContent = 'copie impossible ici : recopiez la commande ci-dessus';
      }
    });
    block.append(btn);
  } else {
    block.append(text('p', 'lbl', "cet hôte n'a pas de commande de reprise connue : reprenez la session par son identifiant"));
  }
  block.append(feedback);
  return block;
}

// `task` porte l'id et, en portefeuille, `project.slug` (cible des lectures et
// des actions) et `session` (bloc « Session »). `portfolio` choisit la porte
// d'écriture : `portfolioTaskAction` (dérogation nommée, tout projet du
// registre) plutôt que `taskAction` (projet de lancement seulement). Le
// vidage attend la fin des trois lectures : reconstruire tout l'inspecteur en
// un seul geste synchrone une fois les données prêtes, plutôt que de le
// laisser vide pendant l'aller-retour réseau — utile pour un changement de
// tâche sélectionnée ou la transition d'état (« Réaliser » ci-dessous). Le
// pilotage humain (`renderSteering`, issue #638 lot B) ne passe plus par ce
// chemin pour ses propres écritures : il réécrit son conteneur en place et ne
// redéclenche qu'un rafraîchissement « board seul » (`draw({ skipInspector:
// true })`), donc ne revient jamais vider ce panneau sous un geste qui suit.
async function renderInspector(ctx, task, onWritten, onTimeline, portfolio = false) {
  const taskId = task.id;
  const project = portfolio ? task.project?.slug : undefined;
  const [detail, trace, recall] = await Promise.all([
    ctx.api.task(taskId, project).catch(() => null),
    ctx.api.taskTrace(taskId, project).catch(() => null),
    ctx.api.taskRecall(taskId, project).catch(() => null),
  ]);
  ctx.inspector.replaceChildren();
  if (!detail) {
    ctx.inspector.append(text('p', 'lbl', 'Tâche indisponible.'));
    return;
  }

  ctx.inspector.append(text('h3', null, detail.title || detail.id));
  ctx.inspector.append(text('div', 'lbl mono', detail.id));
  if (portfolio) {
    ctx.inspector.append(text('div', 'lbl', `Projet : ${task.project?.name || task.project?.slug || '—'}`));
    if (task.session && task.session.id) ctx.inspector.append(sessionBlock(task.session));
  }

  // Finition (lot 4.3, issue #561) : lecture seule ici — le champ existe déjà
  // au ledger et à la projection board (ADR-007 point 6) mais aucune
  // interface ne l'édite encore, ce lot-ci compris. Absent (`""`) tant que
  // personne ne l'a posé : pas de ligne « Finition » plutôt qu'une valeur
  // vide qui laisserait croire à un état déclaré.
  if (detail.finition) {
    ctx.inspector.append(text('div', 'lbl', `Finition : ${FINITION_LABEL[detail.finition] || detail.finition}`));
  }

  // Drill-down (#139) : depuis la carte d'une tâche, ouvrir sa timeline sans
  // passer par l'onglet Timeline du docbar — le nombre d'événements déjà lus
  // rend le geste visible même quand l'utilisateur ne sait pas que la vue
  // existe.
  const timelineBtn = document.createElement('button');
  timelineBtn.type = 'button';
  timelineBtn.className = 'btn';
  const eventCount = (trace?.entries || []).length;
  timelineBtn.textContent = eventCount ? `Voir la timeline (${eventCount})` : 'Voir la timeline';
  timelineBtn.addEventListener('click', () => onTimeline(taskId));
  ctx.inspector.append(timelineBtn);

  // Corps réel de la tâche (#140) : de quoi elle parle, avant les critères et
  // les preuves — un board qui ne montre qu'un titre et un owner ne dit rien
  // à lire pour comprendre la tâche.
  if (detail.description) {
    const descBlock = document.createElement('div');
    descBlock.className = 'ex-insp-block';
    descBlock.append(text('h4', null, 'Description'));
    descBlock.append(text('p', null, detail.description));
    ctx.inspector.append(descBlock);
  }

  // Session (issue #638, lot A) : la session d'hôte qui porte le claim, et la
  // commande qui la reprend quand l'hôte en a une — `resume_command` vient
  // du serveur, cette vue ne compose jamais une commande elle-même. Le
  // bouton la pose dans le dock, comme les commandes d'intention des portes.
  if (detail.session_id) {
    const sessionBlock = document.createElement('div');
    sessionBlock.className = 'ex-insp-block';
    sessionBlock.append(text('h4', null, 'Session'));
    sessionBlock.append(sessionLine(detail, { full: true }));
    if (detail.resume_command) {
      const resumeRow = row();
      resumeRow.append(Object.assign(document.createElement('code'), { textContent: detail.resume_command }));
      const resumeBtn = document.createElement('button');
      resumeBtn.type = 'button';
      resumeBtn.className = 'btn';
      resumeBtn.textContent = 'Reprendre';
      resumeBtn.title = 'Copier la commande de reprise dans le dock';
      resumeBtn.addEventListener('click', () => ctx.dock.echo(detail.resume_command));
      resumeRow.append(resumeBtn);
      sessionBlock.append(resumeRow);
    } else {
      sessionBlock.append(text('p', 'lbl', 'aucune commande de reprise connue pour cet hôte'));
    }
    ctx.inspector.append(sessionBlock);
  }

  // Rappel (#141) : avec parcimonie — le bloc n'existe pas quand il n'a rien
  // à dire, plutôt que d'afficher « rien en mémoire » à chaque tâche neuve.
  if (recall && recall.has_content) {
    const recallBlock = document.createElement('div');
    recallBlock.className = 'ex-insp-block';
    recallBlock.append(text('h4', null, 'Rappel'));
    const pre = document.createElement('pre');
    pre.className = 'ex-recall';
    pre.textContent = recall.text;
    recallBlock.append(pre);
    ctx.inspector.append(recallBlock);
  }

  renderSteering(ctx, detail, onWritten);

  const acceptance = document.createElement('div');
  acceptance.className = 'ex-insp-block';
  acceptance.append(text('h4', null, "Critères d'acceptation"));
  const ul = document.createElement('ul');
  for (const item of detail.acceptance || []) ul.append(text('li', null, item));
  if (!(detail.acceptance || []).length) acceptance.append(text('p', 'lbl', 'aucun critère déclaré'));
  else acceptance.append(ul);
  ctx.inspector.append(acceptance);

  if ((detail.guardrails || []).length) {
    const guardrailsBlock = document.createElement('div');
    guardrailsBlock.className = 'ex-insp-block';
    guardrailsBlock.append(text('h4', null, 'Garde-fous'));
    const gUl = document.createElement('ul');
    for (const item of detail.guardrails) gUl.append(text('li', null, item));
    guardrailsBlock.append(gUl);
    ctx.inspector.append(guardrailsBlock);
  }

  // Dépendances (#140) : ce que le ledger sait au-delà des blockers dérivés
  // du board — une tâche `blocks` une autre est le cas qui compte le plus,
  // affiché en premier, mais les autres natures (relates, parent_child…)
  // ne sont pas tues.
  if ((detail.dependencies || []).length) {
    const depsBlock = document.createElement('div');
    depsBlock.className = 'ex-insp-block';
    depsBlock.append(text('h4', null, 'Dépendances'));
    const dUl = document.createElement('ul');
    const deps = [...detail.dependencies].sort((a, b) => (a.kind === 'blocks' ? -1 : b.kind === 'blocks' ? 1 : 0));
    for (const dep of deps) {
      const label = DEP_LABEL[dep.kind] || dep.kind;
      const li = text('li', dep.kind === 'blocks' ? 'ex-refusal' : null, `${label} ${dep.target}`);
      dUl.append(li);
    }
    depsBlock.append(dUl);
    ctx.inspector.append(depsBlock);
  }

  const hasEvidencePack = (trace?.entries || []).some((e) => e.kind === 'evidence.pack');
  const evidenceBlock = document.createElement('div');
  evidenceBlock.className = 'ex-insp-block';
  const evHead = text('h4', null, 'Preuves');
  evHead.dataset.term = 'evidence-pack';
  evidenceBlock.append(evHead);
  const chips = document.createElement('div');
  chips.className = 'ex-card-chips';
  for (const evidence of detail.expected_evidence || []) chips.append(chip(evidence, hasEvidencePack));
  if (!(detail.expected_evidence || []).length) chips.append(text('span', 'lbl', 'aucune preuve déclarée'));
  evidenceBlock.append(chips);
  ctx.inspector.append(evidenceBlock);

  const gateBlock = document.createElement('div');
  gateBlock.className = 'ex-insp-block';
  const gateHead = text('h4', null, 'Prochaine porte');
  gateHead.dataset.term = 'porte-de-preuve';
  gateBlock.append(gateHead);
  const requirements = detail.next_moves_require || {};
  const targets = Object.keys(requirements);
  if (!targets.length) {
    gateBlock.append(text('p', 'lbl', 'colonne terminale : aucune transition déclarée depuis ici'));
  }
  const feedback = document.createElement('div');
  for (const target of targets) {
    const gateRow = document.createElement('div');
    gateRow.className = 'ex-gate-row';
    gateRow.append(row(text('span', null, `→ ${COLUMN_LABEL[target] || target}`)));
    const required = requirements[target] || [];
    gateRow.append(text('div', 'ex-gate-req', required.length ? `requiert : ${required.join(', ')}` : 'aucune preuve requise'));

    let reasonInput = null;
    if (target === 'blocked') {
      reasonInput = document.createElement('input');
      reasonInput.className = 'input';
      reasonInput.placeholder = 'raison du blocage';
      gateRow.append(reasonInput);
    }

    // En portefeuille, le verrou `readOnly` général ne s'applique pas : l'action
    // suit la dérogation nommée du serveur (`is_registry_scoped_write`), qui
    // route vers le projet propriétaire — même précédent que « Migrer les
    // tâches » (#560) et que les boutons Accepter/Refuser de Piloter.
    const locked = !portfolio && ctx.host.readOnly;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn pri';
    btn.textContent = locked ? 'Écriture désactivée (cockpit)' : `Réaliser : → ${COLUMN_LABEL[target] || target}`;
    btn.disabled = locked;
    if (locked) {
      gateRow.append(text('div', 'lbl', "le cockpit est en lecture seule : ouvrez l'atelier de ce projet, ou la portée « Tous les projets »"));
    }
    btn.addEventListener('click', async () => {
      const [action, body, command] = actionFor(target, detail, reasonInput?.value || '');
      ctx.dock.echo(portfolio && project ? `${command}  # projet ${project}` : command);
      try {
        const result = portfolio
          ? await ctx.api.portfolioTaskAction(taskId, action, body, project)
          : await ctx.api.taskAction(taskId, action, body);
        feedback.replaceChildren();
        if (result && result.blocked) {
          const refusals = (result.refusals || []).map((r) => `${r.evidence} — ${r.reason} (${r.remedy})`);
          feedback.append(text('div', 'ex-refusal', 'refusé : ' + refusals.join(' ; ')));
        } else {
          feedback.append(text('div', 'lbl', `transition : ${result.transition}`));
          onWritten();
        }
      } catch (error) {
        feedback.replaceChildren(text('div', 'ex-refusal', 'échec : ' + error.message));
      }
    });
    gateRow.append(btn);
    gateBlock.append(gateRow);
  }
  gateBlock.append(feedback);
  ctx.inspector.append(gateBlock);
}

// ── Pilotage humain (issue #638, lot B) ───────────────────────────────────
//
// Trois blocs dans l'inspecteur : la priorité (sélecteur), les consignes
// (journal + zone de saisie), l'annulation (raison exigée, `force` proposé
// seulement quand le service a refusé pour un claim tenu ailleurs). Chaque
// geste est un `api.taskAction` ; le refus revient en 200 `blocked: true`.

function renderSteering(ctx, detail, onWritten) {
  // Conteneur dédié (issue #638, lot B, correctif de course résiduelle) :
  // priorité, consignes et annulation vivent dans LEUR PROPRE bloc, que
  // `send()` réécrit en place avec la réponse déjà en main plutôt que
  // d'attendre le rafraîchissement externe de l'inspecteur (`onWritten`, qui
  // refait un aller-retour réseau puis remplace tout l'inspecteur). Sans ce
  // conteneur, un second geste (poser une consigne juste après avoir changé
  // la priorité, ou annuler juste après une consigne) pouvait tomber dans la
  // fenêtre où l'inspecteur venait d'être vidé pour ce second rendu externe :
  // le clic ne trouvait plus de bouton où se poser, et rien n'était écrit
  // côté serveur, sans erreur visible (`test_workspace_executer_steering.py`,
  // course reproduite en CI sur `cancel` puis sur `comment`).
  const container = document.createElement('div');
  container.className = 'ex-steering-root';
  renderSteeringInto(container, ctx, detail, onWritten);
  ctx.inspector.append(container);
}

function renderSteeringInto(container, ctx, detail, onWritten) {
  const readOnly = ctx.host.readOnly;
  const feedback = document.createElement('div');
  const send = async (action, body, command) => {
    ctx.dock.echo(command);
    feedback.replaceChildren();
    try {
      const result = await ctx.api.taskAction(detail.id, action, body);
      if (result && result.blocked) {
        const refusals = (result.refusals || []).map((r) => `${r.evidence} — ${r.reason} (${r.remedy})`);
        feedback.append(text('div', 'ex-refusal', 'refusé : ' + refusals.join(' ; ')));
        return result;
      }
      // Réécrit ce bloc en place avec `result` — la réponse porte déjà la
      // tâche à jour (priorité, consignes, board) — au lieu d'attendre le
      // rafraîchissement externe. Celui-ci reste déclenché (badges et tri du
      // board), mais en mode « board seul » : il ne touche plus jamais ce
      // conteneur, donc ne peut plus le vider sous un geste qui suit.
      container.replaceChildren();
      renderSteeringInto(container, ctx, result, onWritten);
      onWritten({ skipInspector: true });
      return result;
    } catch (error) {
      feedback.replaceChildren(text('div', 'ex-refusal', 'échec : ' + error.message));
      return null;
    }
  };

  // Priorité.
  const prioBlock = document.createElement('div');
  prioBlock.className = 'ex-insp-block';
  prioBlock.append(text('h4', null, 'Priorité'));
  const select = document.createElement('select');
  select.className = 'input';
  select.dataset.role = 'priority';
  select.disabled = readOnly;
  const current = detail.effective_priority || 'medium';
  for (const value of PRIORITIES) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = PRIORITY_LABEL[value] || value;
    if (value === current) option.selected = true;
    select.append(option);
  }
  select.addEventListener('change', () => {
    send('prioritize', { to: select.value }, `grimoire task prioritize ${detail.id} --to ${select.value}`);
  });
  prioBlock.append(row(select, text('span', 'lbl', detail.priority ? 'déclarée' : 'dérivée du profil de risque')));
  container.append(prioBlock);

  // Consignes.
  const dirBlock = document.createElement('div');
  dirBlock.className = 'ex-insp-block';
  dirBlock.append(text('h4', null, "Consignes de l'orchestrateur"));
  const directives = detail.directives || [];
  if (!directives.length) dirBlock.append(text('p', 'lbl', 'aucune consigne posée'));
  for (const d of directives) {
    const node = document.createElement('div');
    node.className = 'ex-directive' + (d.delivered_at ? '' : ' unread');
    node.dataset.role = 'directive';
    const status = d.acknowledged_at ? 'accusée' : (d.delivered_at ? 'livrée' : 'non lue');
    node.append(text('div', null, d.text));
    node.append(text('div', 'ex-directive-meta', `${d.kind} · ${d.author} · ${(d.created_at || '').slice(0, 16)} · ${status}`));
    dirBlock.append(node);
  }
  if (!readOnly) {
    const form = document.createElement('div');
    form.className = 'ex-steer-form';
    const area = document.createElement('textarea');
    area.className = 'input';
    area.placeholder = 'Commentaire ou consigne pour la session qui tient cette tâche';
    area.dataset.role = 'directive-text';
    const kind = document.createElement('select');
    kind.className = 'input';
    for (const [value, label] of [['comment', 'Commentaire'], ['directive', 'Consigne']]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      kind.append(option);
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Poser';
    btn.dataset.role = 'directive-submit';
    btn.addEventListener('click', async () => {
      const value = area.value.trim();
      if (!value) { feedback.replaceChildren(text('div', 'ex-refusal', 'une consigne vide n\'a rien à dire')); return; }
      const result = await send('comment', { text: value, kind: kind.value }, `grimoire task comment ${detail.id} "${value}" --kind ${kind.value}`);
      if (result && !result.blocked) area.value = '';
    });
    form.append(area, row(kind, btn));
    dirBlock.append(form);
  }
  container.append(dirBlock);

  // Annulation — sauf colonne terminale, et jamais proposée en lecture seule :
  // le cockpit ne montre pas un formulaire qu'il ne laisserait pas partir.
  if (!readOnly && detail.board !== 'archived' && detail.board !== 'accepted' && detail.board !== 'released') {
    const cancelBlock = document.createElement('div');
    cancelBlock.className = 'ex-insp-block';
    cancelBlock.append(text('h4', null, 'Annuler'));
    const reason = document.createElement('input');
    reason.className = 'input';
    reason.placeholder = "raison de l'annulation (obligatoire)";
    reason.dataset.role = 'cancel-reason';
    const force = document.createElement('input');
    force.type = 'checkbox';
    force.dataset.role = 'cancel-force';
    const forceLabel = document.createElement('label');
    forceLabel.className = 'lbl';
    forceLabel.append(force, text('span', null, " forcer même si une session la tient"));
    forceLabel.hidden = true;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Annuler la tâche';
    btn.dataset.role = 'cancel-submit';
    btn.disabled = true;
    reason.addEventListener('input', () => { btn.disabled = !reason.value.trim(); });
    btn.addEventListener('click', async () => {
      const body = { reason: reason.value.trim() };
      if (force.checked) body.force = true;
      const result = await send('cancel', body, `grimoire task cancel ${detail.id} --reason "${body.reason}"${force.checked ? ' --force' : ''}`);
      // Le refus « claim » (tenue par une autre session) ouvre l'option de
      // forcer — jamais avant : forcer n'est pas le geste par défaut.
      if (result && result.blocked && (result.refusals || []).some((r) => r.evidence === 'claim')) forceLabel.hidden = false;
    });
    cancelBlock.append(reason, forceLabel, btn);
    container.append(cancelBlock);
  }
  container.append(feedback);
}

function actionFor(target, task, reason) {
  if (target === 'in_progress' && !task.claim) {
    return ['claim', {}, `grimoire task claim ${task.id}`];
  }
  if (target === 'blocked') {
    return ['block', { reason }, `grimoire task block ${task.id} --reason "${reason}"`];
  }
  if (target === 'accepted') {
    return ['close', {}, `grimoire task close ${task.id}`];
  }
  const state = BOARD_TO_STATE[target] || target;
  return ['move', { to: state }, `grimoire task move ${task.id} --to ${state}`];
}

// La barre de portée et de filtres du portefeuille (#638). Sur un hôte qui
// n'est pas le cockpit, la portée « Tous les projets » reste visible mais
// désactivée avec sa raison : l'atelier mono-projet ne résout pas `?project=`,
// donc il ne saurait ni lire ni router vers un autre projet du registre.
function scopeToolbar(ctx, portfolio, onChange) {
  const bar = document.createElement('div');
  bar.className = 'ex-toolbar';
  const scopeLabel = document.createElement('label');
  scopeLabel.append(text('span', null, 'Portée'));
  const scopeSelect = document.createElement('select');
  scopeSelect.className = 'input';
  scopeSelect.dataset.role = 'scope';
  for (const [value, label] of [['project', 'Ce projet'], ['portfolio', 'Tous les projets']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (value === scope.mode) option.selected = true;
    if (value === 'portfolio' && ctx.host.kind !== 'cockpit') {
      option.disabled = true;
      option.title = 'portefeuille : cockpit seulement (grimoire cockpit serve)';
    }
    scopeSelect.append(option);
  }
  scopeSelect.addEventListener('change', () => { scope.mode = scopeSelect.value; onChange(); });
  scopeLabel.append(scopeSelect);
  bar.append(scopeLabel);

  if (scope.mode === 'portfolio' && portfolio) {
    const stateLabel = document.createElement('label');
    stateLabel.append(text('span', null, 'État'));
    const stateSelect = document.createElement('select');
    stateSelect.className = 'input';
    stateSelect.dataset.role = 'state';
    const stateOptions = [['', 'Tous les états'], ...LIFECYCLE.map((c) => [c, COLUMN_LABEL[c]])];
    for (const [value, label] of stateOptions) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      if (value === scope.state) option.selected = true;
      stateSelect.append(option);
    }
    stateSelect.addEventListener('change', () => { scope.state = stateSelect.value; onChange(); });
    stateLabel.append(stateSelect);

    const projectLabel = document.createElement('label');
    projectLabel.append(text('span', null, 'Projet'));
    const projectSelect = document.createElement('select');
    projectSelect.className = 'input';
    projectSelect.dataset.role = 'project';
    const projectOptions = [['', 'Tous les projets'], ...(portfolio.projects || []).map((p) => [p.slug, p.name || p.slug])];
    for (const [value, label] of projectOptions) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      if (value === scope.slug) option.selected = true;
      projectSelect.append(option);
    }
    projectSelect.addEventListener('change', () => { scope.slug = projectSelect.value; onChange(); });
    projectLabel.append(projectSelect);

    const liveLabel = document.createElement('label');
    const liveBox = document.createElement('input');
    liveBox.type = 'checkbox';
    liveBox.dataset.role = 'live';
    liveBox.checked = scope.live;
    liveBox.addEventListener('change', () => { scope.live = liveBox.checked; onChange(); });
    liveLabel.append(liveBox, text('span', null, `Sessions vivantes (< ${portfolio.live_minutes} min)`));

    const summary = portfolio.summary || {};
    const count = text('span', 'ex-count', `${portfolio.count} / ${summary.tasks ?? 0} tâche(s) · ${summary.live ?? 0} session(s) vivante(s) · ${summary.readable ?? 0}/${summary.projects ?? 0} projet(s) lisible(s)`);
    bar.append(stateLabel, projectLabel, liveLabel, count);
  }
  return bar;
}

// Les projets du registre que le portefeuille n'a pas pu lire — nommés avec
// leur raison, jamais tus (leçon #264, même règle que l'aperçu mémoire).
function unreadableNotice(portfolio) {
  const failed = (portfolio.projects || []).filter((p) => p.state !== 'ok');
  if (!failed.length) return null;
  const notice = document.createElement('div');
  notice.className = 'ex-notice';
  for (const p of failed) {
    notice.append(row(dot(p.state === 'no_ledger' ? 'warn' : 'bad'), text('span', null, `${p.name || p.slug} (${p.slug}) — ${p.reason || p.state}`)));
  }
  return notice;
}

export async function mount(root, ctx) {
  injectStyles();
  const wrap = document.createElement('div');
  wrap.className = 'ex-wrap';
  root.append(wrap);

  // `ctx.params` porte ce qu'un `goto('executer', { task, view })` a demandé —
  // Observer s'en sert pour ouvrir directement la timeline d'une tâche depuis
  // un span OTel qui en porte l'identifiant (#139). `scope: 'portfolio'`
  // ouvre directement le portefeuille (#638).
  let view = ctx.params.view === 'timeline' ? 'timeline' : 'board4';
  // Sélection par (projet, id) : en portée projet, `project` reste vide.
  let selected = ctx.params.task ? { id: ctx.params.task, project: '' } : null;
  if (ctx.params.scope === 'portfolio' && ctx.host.kind === 'cockpit') scope.mode = 'portfolio';
  if (ctx.host.kind !== 'cockpit') scope.mode = 'project';
  const board = await ctx.api.tasks();

  const VIEW_LABELS = [
    { id: 'board4', label: 'Board 4' },
    { id: 'board8', label: 'Board 8' },
    { id: 'liste', label: 'Liste' },
    { id: 'timeline', label: 'Timeline' },
  ];

  const setView = (id) => { view = id; draw(); };
  const redraw = () => { selected = null; draw(); };

  function renderNoLedger() {
    ctx.docbar.setBreadcrumb([ctx.host.project || 'projet', 'Exécuter']);
    // Avant cette issue, un early-return ici évitait tout appel à
    // `setViews` : les boutons de vue disparaissaient purement et
    // simplement plutôt que rester visibles (désactivés) — l'utilisateur ne
    // pouvait pas deviner ce que l'espace propose une fois un Mission Ledger
    // ouvert. On les rend désormais, désactivés, avec la raison en `title`.
    ctx.docbar.setViews(
      VIEW_LABELS.map((v) => ({
        ...v,
        disabled: true,
        disabledReason: "Aucun Mission Ledger : ouvrez-en un d'abord.",
      })),
      null,
      () => {},
    );
    wrap.append(scopeToolbar(ctx, null, redraw));
    wrap.append(ctx.empty(
      'Exécuter',
      board.note || "Ce projet n'a pas encore de Mission Ledger.",
      'grimoire task add "<titre>" --acceptance "<critère>"',
    ));
    // ADR-007 (issue #559) : un board du standard scaffoldé avant le lot 4.1,
    // ou jamais migré, n'a pas encore de Mission Ledger — mais SES tâches
    // existent déjà dans `task-board.yaml`. Plutôt que le seul rappel de
    // `grimoire task add` (qui ouvrirait une tâche neuve, pas celles déjà
    // déclarées), l'action de migration est proposée quand le serveur la dit
    // disponible (`tasks_view`/`workspace_api.py`).
    if (board.migration_available) {
      const migrateBtn = document.createElement('button');
      migrateBtn.type = 'button';
      migrateBtn.className = 'btn pri';
      migrateBtn.textContent = 'Migrer les tâches';
      // Jamais désactivé par `ctx.host.readOnly` (issue #560) : migrer un
      // board vers le Mission Ledger s'applique à tout projet du registre,
      // comme mettre à jour ou décider une proposition — même précédent que
      // les boutons Accepter/Refuser de Piloter (`ctx.api.proposalAction`),
      // qui ne se désactivent pas non plus sur ce verrou général.
      const feedback = text('p', 'lbl', '');
      migrateBtn.addEventListener('click', async () => {
        ctx.dock.echo('grimoire task migrate-standard .');
        migrateBtn.disabled = true;
        try {
          const report = await ctx.api.migrateStandardTasks();
          if (ctx.signal.aborted) return;
          feedback.textContent = `${report.tasks_imported} tâche(s) migrée(s) vers le Mission Ledger.`;
          root.replaceChildren();
          await mount(root, ctx);
        } catch (error) {
          migrateBtn.disabled = false;
          feedback.textContent = `échec : ${error.message}`;
        }
      });
      wrap.append(migrateBtn, feedback);
    }
    ctx.dock.echo('grimoire task add');
  }

  async function draw(opts = {}) {
    wrap.replaceChildren();
    const portfolioMode = scope.mode === 'portfolio';
    if (!portfolioMode && !board.ledger) {
      renderNoLedger();
      return;
    }
    let tasks = [];
    let portfolio = null;
    if (portfolioMode) {
      const params = {};
      if (scope.state) params.state = scope.state;
      if (scope.slug) params.slug = scope.slug;
      if (scope.live) params.live = '1';
      portfolio = await ctx.api.portfolioTasks(Object.keys(params).length ? params : undefined);
      tasks = portfolio.tasks || [];
    } else {
      const fresh = await ctx.api.tasks();
      tasks = fresh.tasks || [];
    }
    ctx.docbar.setBreadcrumb([portfolioMode ? 'Tous les projets' : (ctx.host.project || 'projet'), 'Exécuter']);
    ctx.docbar.setViews(VIEW_LABELS, view, setView);
    if (ctx.signal.aborted) return;

    wrap.append(scopeToolbar(ctx, portfolio, redraw));
    if (portfolio) {
      const notice = unreadableNotice(portfolio);
      if (notice) wrap.append(notice);
    }

    const same = (t) => selected && t.id === selected.id && (!portfolioMode || (t.project?.slug || '') === selected.project);
    const onSelect = async (task) => {
      selected = { id: task.id, project: task.project?.slug || '' };
      draw();
    };

    if (view === 'board4') {
      renderBoard(wrap, ctx, tasks, GROUPS4, onSelect, portfolioMode);
    } else if (view === 'board8') {
      renderBoard(wrap, ctx, tasks, LIFECYCLE.map((id) => ({ id, label: COLUMN_LABEL[id], cols: [id] })), onSelect, portfolioMode);
    } else if (view === 'liste') {
      renderList(wrap, ctx, tasks, onSelect, portfolioMode);
    } else if (view === 'timeline') {
      const task = tasks.find(same) || tasks[0];
      if (!task) {
        wrap.append(ctx.empty('Timeline', 'Aucune tâche à tracer.', 'grimoire task list'));
      } else {
        selected = { id: task.id, project: task.project?.slug || '' };
        await renderTimeline(wrap, ctx, task);
      }
    }

    // `{ skipInspector: true }` (issue #638, lot B) : le pilotage humain
    // réécrit son propre bloc en place (`renderSteeringInto`) et ne déclenche
    // ce rafraîchissement que pour le board (tri par priorité, badge
    // consigne) — jamais pour reconstruire l'inspecteur par-dessus un
    // panneau que l'utilisateur est peut-être déjà en train de réutiliser.
    if (opts.skipInspector) return;

    const current = tasks.find(same);
    if (current) {
      const onTimeline = (id) => { selected = { id, project: current.project?.slug || '' }; setView('timeline'); };
      await renderInspector(ctx, current, draw, onTimeline, portfolioMode);
      ctx.dock.echo(`grimoire task show ${current.id}`);
    } else {
      ctx.inspector.replaceChildren(text('p', 'lbl', 'Sélectionnez une tâche pour voir sa porte suivante.'));
      ctx.dock.echo(portfolioMode ? 'grimoire task list --all-projects' : 'grimoire task board');
    }
  }

  await draw();
}
