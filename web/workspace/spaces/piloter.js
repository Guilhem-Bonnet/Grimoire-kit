// Espace Piloter — LOT 4.
//
// Remplace `portfolio.html` (cockpit) et `index.html` du cockpit.
// Cible : Flotte (cockpit) et Projet ; tableau sur bureau, cartes sur mobile ;
// KPI en une carte divisée ; « À traiter » toujours visible.
// Inspecteur : projet — kit, hôtes, standard, actions (initialiser, mettre à
// jour derrière aperçu + confirmation, ouvrir).
//
// Corrections dues par la revue §4.1 : la donnée réelle s'appelle
// `ci_status` et `commits_total` (jamais `p.ci` / `p.commits`, qui ne
// correspondaient à rien) ; `antifragile: null` se lit « pas encore mesurée »,
// jamais comme un score nul ; `unknown` se rend « inconnue » en gris, jamais
// une couleur inventée ; ce module n'ouvre aucun jeu de données de
// démonstration — `demo` reste toujours `false` ici.
//
// API consommées : api.projects(), api.health(project?), api.memoryStatus
// (project?), api.doctor(project?), api.updateProject(project, confirm).

const STYLE_ID = 'pl-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .pl-wrap { padding: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-5); }
    .pl-kpi { display: flex; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); overflow: hidden; }
    .pl-kpi-item { flex: 1; padding: var(--sp-3) var(--sp-4); border-left: 1px solid var(--line); }
    .pl-kpi-item:first-child { border-left: 0; }
    .pl-kpi-val { font-family: var(--mono); font-size: var(--t-xl); font-weight: 600; color: var(--ink); }
    .pl-kpi-lbl { font-size: var(--t-min); color: var(--ink3); margin-top: 2px; }
    .pl-section h3 { font-size: var(--t-m); font-weight: 500; margin: 0 0 var(--sp-2); color: var(--ink); }
    .pl-watch { display: flex; flex-direction: column; gap: 6px; }
    .pl-watch-row { display: flex; align-items: center; gap: var(--sp-2); padding: 8px var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); }
    .pl-watch-txt { flex-grow: 1; }
    .pl-watch-name { font-weight: 500; }
    .pl-watch-reason { color: var(--ink2); margin-left: 8px; }
    .pl-table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: var(--r); }
    .pl-table { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    .pl-table th { text-align: left; font-size: var(--t-min); color: var(--ink3); font-weight: 500; padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); background: var(--bar); position: sticky; top: 0; }
    .pl-table td { padding: 8px var(--sp-3); border-bottom: 1px solid var(--line); height: 48px; vertical-align: middle; }
    .pl-table tbody tr { cursor: pointer; }
    .pl-table tbody tr:hover { background: var(--e2); }
    .pl-table tbody tr[aria-current="true"] { background: var(--accsoft); }
    .pl-cards { display: none; flex-direction: column; gap: var(--sp-2); }
    .pl-card { border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); padding: var(--sp-3); cursor: pointer; }
    .pl-card-row { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
    @media (max-width: 760px) {
      .pl-table-wrap { display: none; }
      .pl-cards { display: flex; }
    }
    .pl-sheet { display: flex; flex-direction: column; gap: var(--sp-4); }
    .pl-insp-block { margin-bottom: var(--sp-4); }
    .pl-insp-block h4 { font-size: var(--t-min); text-transform: none; color: var(--ink3); margin: 0 0 6px; font-weight: 500; }
    .pl-insp-row { display: flex; justify-content: space-between; gap: var(--sp-2); padding: 4px 0; font-size: var(--t-s); }
    .pl-actions { display: flex; flex-direction: column; gap: 6px; margin-top: var(--sp-2); }
    .pl-preview { margin-top: 8px; padding: 8px; border: 1px dashed var(--line); border-radius: var(--r); font-size: var(--t-min); color: var(--ink2); }
  `;
  document.head.append(style);
}

const fmtInt = (n) => (typeof n === 'number' ? n.toLocaleString('fr-FR') : '—');

function relativeAge(minutes) {
  if (minutes == null) return 'aucun';
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${Math.round(minutes)} min`;
  if (minutes < 60 * 24) return `il y a ${Math.round(minutes / 60)} h`;
  return `il y a ${Math.round(minutes / 60 / 24)} j`;
}

function ciWord(status) {
  return { success: 'réussie', passed: 'réussie', failure: 'échouée', failed: 'échouée' }[status] || 'inconnue';
}

function ciDotClass(status) {
  return { success: 'ok', passed: 'ok', failure: 'bad', failed: 'bad' }[status] || '';
}

function dot(cls) {
  const span = document.createElement('span');
  span.className = 'dot' + (cls ? ' ' + cls : '');
  return span;
}

function row(...children) {
  const div = document.createElement('div');
  div.className = 'row';
  div.append(...children);
  return div;
}

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

// ── Statut du kit : aligné (contenu à jour) ≠ installé (CLI qui tourne) ─────
//
// `kit.upToDate` dit qu'aucun fichier livré n'a de révision plus récente au
// catalogue — l'invariant qui compte pour l'utilisateur. `kit.aligned` est la
// version qui a écrit ce contenu, `kit.installed` la version du CLI qui vient
// de répondre : deux nombres différents pour une même raison saine (un fichier
// n'a pas changé depuis 3.36.0 alors que le CLI est passé en 3.38.0, cf.
// project_health.py:kit_alignment). Affirmer « à jour » à côté de deux
// versions différentes se lit comme une contradiction (#288) ; « aligné »
// reste vrai dans les deux cas, « à jour » n'est employé que quand les deux
// versions coïncident réellement.
function kitStatus(kit) {
  if (!kit || !kit.scaffolded) {
    return { word: kit ? 'non initialisé' : 'indisponible', dot: kit ? 'warn' : null };
  }
  if (!kit.upToDate) {
    return { word: `en retard (${kit.behind})`, dot: 'warn' };
  }
  if (kit.aligned && kit.installed && kit.aligned !== kit.installed) {
    return { word: 'aligné', dot: 'ok' };
  }
  return { word: 'à jour', dot: 'ok' };
}

// ── Signaux « à traiter » ────────────────────────────────────────────────────

function watchReasons(entry, health) {
  const reasons = [];
  if (!entry.managed) {
    reasons.push({ kind: 'kit', word: 'projet non initialisé', action: 'ouvrir' });
  } else if (health && health.kit && health.kit.scaffolded && health.kit.catalogAvailable && !health.kit.upToDate) {
    reasons.push({ kind: 'kit', word: `kit en retard (${health.kit.behind} fichier(s))`, action: 'update' });
  }
  if (health && health.ci_status === 'failure') {
    reasons.push({ kind: 'ci', word: 'CI rouge', action: 'ouvrir' });
  }
  return reasons;
}

// ── KPI en une carte divisée ─────────────────────────────────────────────────

function kpiCard(items) {
  const card = document.createElement('div');
  card.className = 'pl-kpi';
  for (const item of items) {
    const cell = document.createElement('div');
    cell.className = 'pl-kpi-item';
    cell.append(text('div', 'pl-kpi-val mono', item.value), text('div', 'pl-kpi-lbl', item.label));
    card.append(cell);
  }
  return card;
}

// ── Niveau Flotte (cockpit) ──────────────────────────────────────────────────

async function loadFleet(ctx) {
  const registry = await ctx.api.projects();
  const entries = registry.projects || [];
  const settled = await Promise.allSettled(
    entries.map((entry) => Promise.all([
      ctx.api.health(entry.slug).catch(() => null),
      ctx.api.memoryStatus(entry.slug).catch(() => null),
    ])),
  );
  return entries.map((entry, index) => {
    const [health, memory] = settled[index].status === 'fulfilled' ? settled[index].value : [null, null];
    return { entry, health, memory };
  });
}

function renderFleet(root, ctx, rows, onSelect) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-wrap';

  // Le cockpit ne scanne jamais le disque (#341) : un registre vide rend une
  // flotte vide, pas une panne. Un tableau muet à zéro lignes se lisait comme
  // « le cockpit ne détecte rien » ; l'état vide nomme le geste attendu.
  if (!rows.length) {
    wrap.append(text(
      'p',
      'lbl',
      "Aucun projet enregistré. Ce cockpit lit un registre, il ne scanne pas le disque : "
      + "grimoire cockpit add <chemin> pour un projet, ou grimoire cockpit scan <racine> pour explorer un dossier.",
    ));
    return wrap;
  }

  const aligned = rows.filter((r) => r.health?.kit?.upToDate).length;
  const active = rows.filter((r) => r.health?.activity?.active).length;
  const watch = rows.flatMap((r) => watchReasons(r.entry, r.health).map((reason) => ({ ...r, reason })));

  wrap.append(kpiCard([
    { value: fmtInt(rows.length), label: 'projets' },
    { value: fmtInt(aligned), label: 'kit aligné' },
    { value: fmtInt(watch.length), label: 'à traiter' },
    { value: fmtInt(active), label: 'actifs (15 min)' },
  ]));

  const watchSection = document.createElement('div');
  watchSection.className = 'pl-section';
  watchSection.append(text('h3', null, 'À traiter'));
  if (!watch.length) {
    watchSection.append(text('p', 'lbl', 'Rien à traiter : chaque projet du registre est initialisé et son kit est aligné.'));
  } else {
    const list = document.createElement('div');
    list.className = 'pl-watch';
    for (const item of watch) {
      const line = document.createElement('div');
      line.className = 'pl-watch-row';
      line.append(
        dot('warn'),
        row(text('span', 'pl-watch-name', item.entry.name || item.entry.slug), text('span', 'pl-watch-reason lbl', item.reason.word)),
      );
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn';
      btn.textContent = 'Ouvrir →';
      btn.addEventListener('click', () => onSelect(item.entry.slug));
      line.append(btn);
      list.append(line);
    }
    watchSection.append(list);
  }
  wrap.append(watchSection);

  const tableWrap = document.createElement('div');
  tableWrap.className = 'pl-table-wrap';
  const table = document.createElement('table');
  table.className = 'pl-table';
  const thead = document.createElement('thead');
  thead.innerHTML = '';
  const headRow = document.createElement('tr');
  const headTerms = { Projet: 'projet', Kit: 'kit', Antifragilité: 'antifragilite', Mémoire: 'memoire', Flows: 'workflow' };
  for (const label of ['Projet', 'Kit', 'CI', 'Commits', 'Antifragilité', 'Mémoire', 'Flows', 'Dernier événement']) {
    const th = text('th', null, label);
    if (headTerms[label]) th.dataset.term = headTerms[label];
    headRow.append(th);
  }
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.tabIndex = 0;
    tr.addEventListener('click', () => onSelect(r.entry.slug));
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(r.entry.slug); });

    const nameCell = document.createElement('td');
    nameCell.append(text('div', null, r.entry.name || r.entry.slug), text('div', 'lbl mono', r.entry.path));
    tr.append(nameCell);

    const kitCell = document.createElement('td');
    if (!r.entry.managed) kitCell.append(row(dot('warn'), text('span', null, 'non initialisé')));
    else if (!r.health) kitCell.append(row(dot(), text('span', null, 'indisponible')));
    else kitCell.append(row(dot(r.health.kit.upToDate ? 'ok' : 'warn'), text('span', null, r.health.kit.aligned || 'inconnue')));
    tr.append(kitCell);

    const ciCell = document.createElement('td');
    const status = r.health?.ci_status;
    ciCell.append(row(dot(ciDotClass(status)), text('span', null, ciWord(status))));
    tr.append(ciCell);

    tr.append(text('td', 'mono', fmtInt(r.health?.commits_total)));

    const afCell = document.createElement('td');
    afCell.append(text('span', 'lbl', r.health?.antifragile == null ? (r.health?.antifragile_note || 'pas encore mesurée') : `${r.health.antifragile}/100`));
    tr.append(afCell);

    const memCell = document.createElement('td');
    if (r.memory && r.memory.state === 'ok') memCell.append(row(dot('ok'), text('span', null, `${fmtInt(r.memory.entries)} entrée(s)`)));
    else if (r.memory && r.memory.state === 'unavailable') memCell.append(row(dot('warn'), text('span', null, 'indisponible')));
    else memCell.append(row(dot(), text('span', null, 'non initialisée')));
    tr.append(memCell);

    tr.append(text('td', 'mono', fmtInt((r.health?.flows || []).length)));
    tr.append(text('td', 'lbl', relativeAge(r.health?.activity?.ageMinutes)));
    tbody.append(tr);
  }
  table.append(tbody);
  tableWrap.append(table);
  wrap.append(tableWrap);

  const cards = document.createElement('div');
  cards.className = 'pl-cards';
  for (const r of rows) {
    const card = document.createElement('div');
    card.className = 'pl-card';
    card.addEventListener('click', () => onSelect(r.entry.slug));
    card.append(text('div', null, r.entry.name || r.entry.slug));
    card.append(row(dot(r.health?.kit?.upToDate ? 'ok' : 'warn'), text('span', 'lbl', r.health?.kit?.aligned || (r.entry.managed ? 'inconnue' : 'non initialisé'))));
    card.append(row(dot(ciDotClass(r.health?.ci_status)), text('span', 'lbl', 'CI ' + ciWord(r.health?.ci_status))));
    card.append(text('div', 'pl-card-row lbl', `${fmtInt(r.health?.commits_total)} commits · ${fmtInt((r.health?.flows || []).length)} flow(s)`));
    cards.append(card);
  }
  wrap.append(cards);

  root.append(wrap);
}

// ── Niveau Projet (fiche) ────────────────────────────────────────────────────

async function loadSheet(ctx, slug) {
  const [health, memory, doctor] = await Promise.all([
    ctx.api.health(slug).catch(() => null),
    ctx.api.memoryStatus(slug).catch(() => null),
    ctx.api.doctor(slug).catch(() => null),
  ]);
  return { health, memory, doctor };
}

function renderSheet(root, ctx, slug, name, sheet, options) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-sheet';
  const { health, memory } = sheet;

  wrap.append(text('h2', null, name || slug || ctx.host.project || 'Projet servi'));

  wrap.append(kpiCard([
    { value: health?.kit?.scaffolded ? kitStatus(health.kit).word : 'absent', label: 'kit' },
    { value: ciWord(health?.ci_status), label: 'CI' },
    { value: fmtInt(health?.commits_total), label: 'commits' },
    { value: fmtInt((health?.flows || []).length), label: 'flows' },
  ]));

  root.append(wrap);

  // ── Inspecteur : kit, hôtes, standard, actions ─────────────────────────
  ctx.inspector.replaceChildren();

  const kitBlock = document.createElement('div');
  kitBlock.className = 'pl-insp-block';
  kitBlock.append(text('h4', null, 'Kit'));
  const kit = kitStatus(health?.kit);
  kitBlock.append(row(dot(kit.dot), text('span', null, kit.word)));
  if (health?.kit?.aligned) kitBlock.append(text('div', 'lbl', `aligné sur ${health.kit.aligned}, installé ${health.kit.installed}`));
  ctx.inspector.append(kitBlock);

  const standardBlock = document.createElement('div');
  standardBlock.className = 'pl-insp-block';
  standardBlock.append(text('h4', null, 'Standard'));
  if (sheet.doctor) {
    standardBlock.append(row(dot(sheet.doctor.ok ? 'ok' : 'bad'), text('span', null, sheet.doctor.ok ? 'doctor conforme' : 'doctor en écart')));
  } else {
    standardBlock.append(text('div', 'lbl', 'diagnostic indisponible'));
  }
  ctx.inspector.append(standardBlock);

  const memBlock = document.createElement('div');
  memBlock.className = 'pl-insp-block';
  memBlock.append(text('h4', null, 'Mémoire'));
  memBlock.append(row(dot(memory?.state === 'ok' ? 'ok' : (memory?.state === 'unavailable' ? 'warn' : '')), text('span', null, memory?.configuredBackend ? `${memory.configuredBackend} · ${fmtInt(memory.entries)} entrée(s)` : 'non initialisée')));
  ctx.inspector.append(memBlock);

  const actionsBlock = document.createElement('div');
  actionsBlock.className = 'pl-insp-block pl-actions';
  actionsBlock.append(text('h4', null, 'Actions'));

  if (!health?.kit?.scaffolded) {
    const initRow = document.createElement('div');
    initRow.append(text('p', 'lbl', "Ce projet n'est pas initialisé. La Console ne lance jamais `grimoire init` à distance :"));
    const code = document.createElement('code');
    code.className = 'mono';
    code.textContent = `grimoire init ${slug ? '(dans le dossier du projet)' : '.'}`;
    initRow.append(code);
    actionsBlock.append(initRow);
  }

  const updateBtn = document.createElement('button');
  updateBtn.type = 'button';
  updateBtn.className = 'btn';
  updateBtn.textContent = 'Mettre à jour — aperçu';
  const preview = document.createElement('div');
  preview.className = 'pl-preview';
  preview.hidden = true;
  updateBtn.addEventListener('click', async () => {
    ctx.dock.echo(`grimoire up${slug ? ' # ' + slug : ''}`);
    try {
      const report = await ctx.api.updateProject(slug, false);
      preview.hidden = false;
      preview.replaceChildren();
      preview.append(text('div', null, report.ok ? 'Aperçu réussi (--dry-run).' : (report.error || 'Aperçu en échec.')));
      if (report.output) {
        const pre = document.createElement('pre');
        pre.className = 'mono lbl';
        pre.style.whiteSpace = 'pre-wrap';
        pre.style.margin = '6px 0 0';
        pre.textContent = report.output.slice(0, 2000);
        preview.append(pre);
      }
      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'btn pri';
      confirmBtn.textContent = 'Confirmer la mise à jour';
      confirmBtn.style.marginTop = '8px';
      confirmBtn.addEventListener('click', async () => {
        ctx.dock.echo(`grimoire up --yes${slug ? ' # ' + slug : ''}`);
        const result = await ctx.api.updateProject(slug, true);
        preview.append(text('div', 'lbl', result.ok ? 'Mis à jour.' : 'Échec de la mise à jour.'));
        options.refresh();
      });
      preview.append(confirmBtn);
    } catch (error) {
      preview.hidden = false;
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
    }
  });
  actionsBlock.append(updateBtn, preview);

  if (ctx.host.kind === 'cockpit' && slug && slug !== ctx.host.project) {
    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'btn';
    openBtn.textContent = 'Ouvrir ce projet';
    openBtn.addEventListener('click', () => { location.search = '?project=' + encodeURIComponent(slug); });
    actionsBlock.append(openBtn);
  }

  ctx.inspector.append(actionsBlock);
}

export async function mount(root, ctx) {
  injectStyles();
  const cockpit = ctx.host.kind === 'cockpit';
  let level = cockpit ? 'flotte' : 'projet';
  let selected = ctx.host.project || null;

  const zoomLevels = cockpit
    ? [{ id: 'flotte', label: 'Flotte' }, { id: 'projet', label: 'Projet' }]
    : [{ id: 'projet', label: 'Projet' }];

  const draw = async () => {
    root.replaceChildren();
    ctx.docbar.setBreadcrumb([ctx.host.project || 'flotte', 'Piloter', level === 'flotte' ? 'Flotte' : 'Projet']);
    ctx.docbar.setZoom(zoomLevels, level, (id) => { level = id; draw(); });

    if (level === 'flotte') {
      ctx.inspector.replaceChildren(document.createElement('div'));
      const rows = await loadFleet(ctx);
      if (ctx.signal.aborted) return;
      renderFleet(root, ctx, rows, (slug) => { selected = slug; level = 'projet'; draw(); });
      ctx.dock.echo('grimoire status');
    } else {
      const name = cockpit
        ? (await ctx.api.projects()).projects.find((p) => p.slug === selected)?.name
        : ctx.host.status?.slug;
      const sheet = await loadSheet(ctx, selected);
      if (ctx.signal.aborted) return;
      renderSheet(root, ctx, selected, name, sheet, { refresh: draw });
      ctx.dock.echo('grimoire doctor');
    }
  };

  await draw();
}
