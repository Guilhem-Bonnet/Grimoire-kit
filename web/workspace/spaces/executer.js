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

const STYLE_ID = 'ex-styles';

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
// Nature d'une dépendance (grimoire.missions.schemas.DependencyKind), en clair.
const DEP_LABEL = {
  blocks: 'bloque', relates: 'lié à', parent_child: 'parent / enfant',
  discovered_from: 'découverte depuis', supersedes: 'remplace',
};

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
    .ex-card-meta { font-size: var(--t-s); color: var(--ink2); }
    .ex-card-chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .ex-card-next { font-size: var(--t-s); color: var(--ink2); display: flex; justify-content: space-between; align-items: center; gap: 6px; border-top: 1px solid var(--line); padding-top: 6px; }
    .ex-list-wrap { overflow: auto; border: 1px solid var(--line); border-radius: var(--r); }
    .ex-list { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    .ex-list th { text-align: left; font-size: var(--t-min); color: var(--ink3); font-weight: 500; padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); background: var(--bar); }
    .ex-list td { padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); }
    .ex-list tbody tr { cursor: pointer; }
    .ex-list tbody tr:hover { background: var(--e2); }
    .ex-timeline { display: flex; flex-direction: column; gap: 6px; padding: var(--sp-2) 0; }
    .ex-tl-entry { display: flex; gap: var(--sp-3); padding: 6px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); }
    .ex-tl-entry.fail { border-color: var(--bad); }
    .ex-tl-at { font-family: var(--mono); font-size: var(--t-min); color: var(--ink3); width: 150px; flex: none; }
    .ex-insp-block { margin-bottom: var(--sp-4); }
    .ex-insp-block h4 { font-size: var(--t-min); color: var(--ink3); margin: 0 0 6px; font-weight: 500; }
    .ex-insp-block ul { margin: 0; padding-left: 18px; font-size: var(--t-s); }
    .ex-recall { margin: 0; padding: var(--sp-2) var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); font-family: var(--mono); font-size: var(--t-min); white-space: pre-wrap; word-break: break-word; }
    .ex-gate-row { display: flex; flex-direction: column; gap: 4px; padding: 8px; border: 1px solid var(--line); border-radius: var(--r); margin-bottom: 6px; }
    .ex-gate-req { font-size: var(--t-min); color: var(--ink3); }
    .ex-refusal { color: var(--bad); font-size: var(--t-s); margin-top: 6px; }
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

function nextColumn(column) {
  const index = LIFECYCLE.indexOf(column);
  return index >= 0 && index < LIFECYCLE.length - 1 ? LIFECYCLE[index + 1] : null;
}

function taskCard(task, onSelect) {
  const card = document.createElement('div');
  card.className = 'ex-card';
  card.tabIndex = 0;
  card.addEventListener('click', () => onSelect(task.id));
  card.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(task.id); });

  card.append(text('div', 'ex-card-title', task.title || task.id));
  card.append(text('div', 'ex-card-meta lbl', [task.owner || 'sans owner', task.type, task.risk_profile].filter(Boolean).join(' · ')));

  const chips = document.createElement('div');
  chips.className = 'ex-card-chips';
  for (const evidence of (task.expected_evidence || []).slice(0, 4)) chips.append(chip(evidence, false));
  if (!(task.expected_evidence || []).length) chips.append(text('span', 'lbl', 'aucune preuve déclarée'));
  card.append(chips);

  const next = nextColumn(task.board);
  const nextRow = document.createElement('div');
  nextRow.className = 'ex-card-next';
  nextRow.append(text('span', null, next ? `→ ${COLUMN_LABEL[next]}` : 'colonne terminale'));
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn';
  btn.textContent = 'Voir la porte';
  btn.addEventListener('click', (e) => { e.stopPropagation(); onSelect(task.id); });
  nextRow.append(btn);
  card.append(nextRow);
  return card;
}

function renderBoard(root, ctx, tasks, groups, onSelect) {
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
    const inColumn = group.cols.flatMap((c) => byColumn.get(c) || []);

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
      for (const task of inColumn) body.append(taskCard(task, onSelect));
    }
    col.append(body);
    board.append(col);
  }
  root.append(board);
}

function renderList(root, ctx, tasks, onSelect) {
  const wrap = document.createElement('div');
  wrap.className = 'ex-list-wrap';
  const table = document.createElement('table');
  table.className = 'ex-list';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Tâche', 'État', 'Owner', 'Preuves', 'Prochaine porte']) headRow.append(text('th', null, label));
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  for (const task of tasks) {
    const tr = document.createElement('tr');
    tr.addEventListener('click', () => onSelect(task.id));
    tr.append(text('td', null, task.title || task.id));
    const stateCell = document.createElement('td');
    stateCell.append(row(dot(task.board === 'blocked' ? 'bad' : (task.board === 'accepted' || task.board === 'released' ? 'ok' : '')), text('span', null, COLUMN_LABEL[task.board] || task.board)));
    tr.append(stateCell);
    tr.append(text('td', null, task.owner || '—'));
    const evCell = document.createElement('td');
    evCell.append(row(...(task.expected_evidence || []).slice(0, 3).map((e) => chip(e, false))));
    if (!(task.expected_evidence || []).length) evCell.append(text('span', 'lbl', '—'));
    tr.append(evCell);
    const next = nextColumn(task.board);
    tr.append(text('td', 'lbl', next ? `→ ${COLUMN_LABEL[next]}` : '—'));
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  root.append(wrap);
}

function timelineEntryNode(entry) {
  const node = document.createElement('div');
  node.className = 'ex-tl-entry' + (entry.failure ? ' fail' : '');
  node.append(text('div', 'ex-tl-at mono', entry.at || '—'));
  const body = document.createElement('div');
  body.append(row(dot(entry.failure ? 'bad' : 'ok'), text('span', null, entry.summary || entry.kind)));
  body.append(text('div', 'lbl', `${entry.source} · ${entry.kind}`));
  node.append(body);
  return node;
}

async function renderTimeline(root, ctx, task) {
  const trace = await ctx.api.taskTrace(task.id).catch(() => null);
  const wrap = document.createElement('div');
  wrap.className = 'ex-timeline';
  if (!trace || !trace.entries || !trace.entries.length) {
    wrap.append(ctx.empty(
      'Timeline',
      "Aucun journal ne mentionne cette tâche : ni le Mission Ledger, ni le TraceLedger, ni le runtime.",
      `grimoire task trace ${task.id}`,
    ));
  } else {
    for (const entry of trace.entries) wrap.append(timelineEntryNode(entry));
  }
  root.append(wrap);
  ctx.dock.log('traces', ...(trace?.entries || []).map((e) => `${e.at} · ${e.source} · ${e.summary}`));
}

// ── Inspecteur ────────────────────────────────────────────────────────────

async function renderInspector(ctx, taskId, onWritten) {
  ctx.inspector.replaceChildren();
  const [detail, trace, recall] = await Promise.all([
    ctx.api.task(taskId).catch(() => null),
    ctx.api.taskTrace(taskId).catch(() => null),
    ctx.api.taskRecall(taskId).catch(() => null),
  ]);
  if (!detail) {
    ctx.inspector.append(text('p', 'lbl', 'Tâche indisponible.'));
    return;
  }

  ctx.inspector.append(text('h3', null, detail.title || detail.id));
  ctx.inspector.append(text('div', 'lbl mono', detail.id));

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

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn pri';
    btn.textContent = ctx.host.readOnly ? 'Écriture désactivée (cockpit)' : `Réaliser : → ${COLUMN_LABEL[target] || target}`;
    btn.disabled = ctx.host.readOnly;
    if (ctx.host.readOnly) {
      gateRow.append(text('div', 'lbl', "le cockpit est en lecture seule : ouvrez l'atelier de ce projet pour agir"));
    }
    btn.addEventListener('click', async () => {
      const [action, body, command] = actionFor(target, detail, reasonInput?.value || '');
      ctx.dock.echo(command);
      try {
        const result = await ctx.api.taskAction(taskId, action, body);
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

export async function mount(root, ctx) {
  injectStyles();
  const wrap = document.createElement('div');
  wrap.className = 'ex-wrap';
  root.append(wrap);

  let view = 'board4';
  let selected = null;
  const board = await ctx.api.tasks();

  if (!board.ledger) {
    ctx.docbar.setBreadcrumb([ctx.host.project || 'projet', 'Exécuter']);
    wrap.append(ctx.empty('Exécuter', board.note || "Ce projet n'a pas encore de Mission Ledger.", 'grimoire task add'));
    ctx.dock.echo('grimoire task add');
    return;
  }

  const setView = (id) => { view = id; draw(); };

  async function draw() {
    wrap.replaceChildren();
    const fresh = await ctx.api.tasks();
    const tasks = fresh.tasks || [];
    ctx.docbar.setBreadcrumb([ctx.host.project || 'projet', 'Exécuter']);
    ctx.docbar.setViews(
      [
        { id: 'board4', label: 'Board 4' },
        { id: 'board8', label: 'Board 8' },
        { id: 'liste', label: 'Liste' },
        { id: 'timeline', label: 'Timeline' },
      ],
      view,
      setView,
    );
    if (ctx.signal.aborted) return;

    const onSelect = async (id) => {
      selected = id;
      draw();
    };

    if (view === 'board4') {
      renderBoard(wrap, ctx, tasks, GROUPS4, onSelect);
    } else if (view === 'board8') {
      renderBoard(wrap, ctx, tasks, LIFECYCLE.map((id) => ({ id, label: COLUMN_LABEL[id], cols: [id] })), onSelect);
    } else if (view === 'liste') {
      renderList(wrap, ctx, tasks, onSelect);
    } else if (view === 'timeline') {
      const task = tasks.find((t) => t.id === selected) || tasks[0];
      if (!task) {
        wrap.append(ctx.empty('Timeline', 'Aucune tâche à tracer.', 'grimoire task list'));
      } else {
        selected = task.id;
        await renderTimeline(wrap, ctx, task);
      }
    }

    if (selected && tasks.some((t) => t.id === selected)) {
      await renderInspector(ctx, selected, draw);
      ctx.dock.echo(`grimoire task show ${selected}`);
    } else {
      ctx.inspector.replaceChildren(text('p', 'lbl', 'Sélectionnez une tâche pour voir sa porte suivante.'));
      ctx.dock.echo('grimoire task board');
    }
  }

  await draw();
}
