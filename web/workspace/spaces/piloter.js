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
// (project?), api.doctor(project?), api.updateProject(project, confirm),
// api.agents(project?), api.agentSkill(name, skill, action),
// api.agentFields(name, fields).
//
// Wizard de setup (#171) : api.archetypesCatalogue(), api.backendsCatalogue(),
// api.needsCatalogue(), api.setupPlan(payload) — exécute réellement (même
// mécanique que `grimoire up`) sauf `payload.planOnly`, qui garde le repli
// « copier-coller la commande ». Refusé côté serveur (readOnly) hors projet
// d'accueil, comme le reste des écritures de cette fiche.
//
// Agents (#374) : section de la fiche projet, pas un septième espace. La
// gestion d'agents est une facette du même objet que « kit, hôtes, standard,
// actions » — un réglage du projet, pas un artefact qu'on façonne (ça, c'est
// Concevoir) ni une trace d'exécution (Observer). Écritures désactivées
// (`ctx.host.readOnly`) hors projet d'accueil, comme le reste de la fiche.

import { renderMarkdown, truncateMarkdown } from '../markdown.js';

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
    .pl-table { width: 100%; border-collapse: collapse; font-size: var(--t-s); background: var(--e1); }
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
    .pl-preview, .pl-review-preview { margin-top: 8px; padding: 8px; border: 1px dashed var(--line); border-radius: var(--r); font-size: var(--t-min); color: var(--ink2); }
    .pl-nodes { margin: 8px 0; }
    /* Un bloc de code garde sa largeur propre et défile horizontalement —
       jamais pre-wrap, qui casse l'alignement d'une sortie CLI/Rich à
       chaque redimensionnement. */
    .pl-markdown { font-size: var(--t-s); }
    .pl-markdown h1, .pl-markdown h2, .pl-markdown h3 { font-size: var(--t-m); font-weight: 500; margin: 10px 0 4px; color: var(--ink); }
    .pl-markdown p { margin: 4px 0; }
    .pl-markdown pre { font-family: var(--mono); font-size: var(--t-min); background: var(--e2); border-radius: var(--r); padding: 8px; overflow-x: auto; white-space: pre; margin: 4px 0; }
    .pl-markdown code { font-family: var(--mono); }
    .pl-badge { display: inline-block; padding: 1px 6px; border-radius: 999px; font-size: var(--t-min); border: 1px solid var(--line); color: var(--ink2); }
    .pl-badge.overrides { color: var(--ink); border-color: var(--acc); }
    /* --warn n'est jamais utilisé comme couleur de texte ailleurs dans la
       coque (voir .dot.warn, toujours un fond) : ses deux variantes
       clair/sombre ne garantissent pas 4.5:1 sur --e1. Texte en --ink
       (contraste garanti), --warn réservé à la bordure. */
    .pl-badge.stale { color: var(--ink); border-color: var(--warn); }
    /* Informationnel, pas un avertissement : même traitement que .overrides. */
    .pl-badge.fresh { color: var(--ink); border-color: var(--acc); }
    .pl-chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .pl-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px; background: var(--e2); font-size: var(--t-min); }
    .pl-chip button { border: 0; background: none; color: var(--ink3); cursor: pointer; padding: 0; font-size: var(--t-min); line-height: 1; }
    .pl-chip button:hover { color: var(--ink); }
    .pl-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: var(--sp-2); }
    .pl-field label { font-size: var(--t-min); color: var(--ink3); }
    .pl-field textarea, .pl-field input[type="text"] { font: inherit; font-size: var(--t-s); padding: 6px 8px; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); color: var(--ink); resize: vertical; }
    .pl-tool-opts { display: flex; flex-wrap: wrap; gap: var(--sp-2); font-size: var(--t-s); }
    .pl-tool-opts label { display: flex; align-items: center; gap: 4px; }
    .pl-prop-row { flex-direction: column; align-items: stretch; gap: 6px; }
    .pl-prop-head { display: flex; align-items: center; gap: var(--sp-2); }
    .pl-prop-facts { color: var(--ink2); font-size: var(--t-s); }
    .pl-prop-actions { display: flex; gap: var(--sp-2); }
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

// Pastille pleine (seconde marche) : remplace le point + mot pour kit/CI/
// doctor/mémoire (Inspecteur et Flotte), le type de proposition et le nœud
// du déroulé — `.chip.pill.<cls>` (shell.css) porte un fond saturé et
// `--onfill` comme texte ; un `cls` vide garde l'apparence neutre de `.chip`.
function pill(cls, word) {
  const span = document.createElement('span');
  span.className = 'chip pill' + (cls ? ' ' + cls : '');
  span.textContent = word;
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

// ── Trois versions du kit, quand elles diffèrent (issue #510 point 4c) ─────
//
// `kit.aligned` (contenu du projet) et `kit.installed` (CLI qui vient de
// répondre, ici le serveur cockpit) ne disent rien de l'outil qui gouverne
// vraiment le projet au quotidien — un pipx séparé peut être plus vieux sans
// qu'aucun des deux autres chiffres ne le montre. `kit.projectTool` (server
// side : `.venv/bin/grimoire` ou `project-context.yaml` `tool:`) porte cette
// troisième version, ou `null` quand le projet ne déclare rien de vérifiable.
function projectToolLine(kit) {
  if (!kit || !kit.aligned) return null;
  const known = [kit.aligned, kit.installed, kit.projectTool].filter(Boolean);
  const distinct = new Set(known);
  // Deux déclencheurs, l'un suffit : `aligned` et `installed` divergent déjà
  // (le cas générique — un projet pas encore aligné sur le serveur cockpit),
  // ou l'outil déclaré par le projet ajoute une 3e valeur distincte (le cas
  // réel de l'issue : cockpit et projet d'accord sur le kit, pipx en retard
  // sans que rien d'autre ne le montre). Sans l'un des deux, rien à ajouter —
  // afficher « inconnu » sur chaque projet sans `.venv` dédié serait du bruit.
  const alreadyDivergent = Boolean(kit.installed) && kit.aligned !== kit.installed;
  const toolAddsDivergence = Boolean(kit.projectTool) && distinct.size > 1;
  if (!alreadyDivergent && !toolAddsDivergence) return null;
  const toolWord = kit.projectTool
    ? kit.projectTool
    : 'inconnu, vérifiez `grimoire --version` dans le projet';
  return `outil du projet : ${toolWord}`;
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
//
// Cache mémoire de la Flotte (issue #510 point 5) — chaque affichage relançait
// `health()` + `memoryStatus()` pour TOUS les projets du registre (34 requêtes
// pour 17 projets, 20 pour 10, constaté en pilotant neuf projets réels l'un
// après l'autre depuis le cockpit). Le seul point d'entrée qui fait ce
// balayage reste `loadFleet` — sélectionner un projet (`onSelect` plus bas)
// ou revenir sur sa fiche ne touche jamais cette fonction, une seule lecture
// ciblée (`loadSheet`) suffit. Un cache par slug, 60 s, absorbe les
// affichages répétés de la Flotte dans une même session (zoom Flotte/Projet
// va-et-vient) sans jamais mentir plus d'une minute ; `force` (bouton
// « Rafraîchir la flotte ») et l'invalidation ciblée après une mise à jour
// (`invalidateFleetCache`, appelé depuis `renderSheet`) restent les deux
// seules façons de le contourner.
const FLEET_CACHE_TTL_MS = 60_000;
const fleetCache = new Map(); // slug -> { at, health, memory }

function invalidateFleetCache(slug) {
  if (slug) fleetCache.delete(slug);
  else fleetCache.clear();
}

async function loadFleet(ctx, { force = false } = {}) {
  const registry = await ctx.api.projects();
  const entries = registry.projects || [];
  const now = Date.now();
  const settled = await Promise.allSettled(
    entries.map(async (entry) => {
      const cached = !force ? fleetCache.get(entry.slug) : null;
      if (cached && now - cached.at < FLEET_CACHE_TTL_MS) {
        return [cached.health, cached.memory];
      }
      const [health, memory] = await Promise.all([
        ctx.api.health(entry.slug).catch(() => null),
        ctx.api.memoryStatus(entry.slug).catch(() => null),
      ]);
      fleetCache.set(entry.slug, { at: now, health, memory });
      return [health, memory];
    }),
  );
  return entries.map((entry, index) => {
    const [health, memory] = settled[index].status === 'fulfilled' ? settled[index].value : [null, null];
    return { entry, health, memory };
  });
}

// ── « Nouveau projet » (#172) : bouton + section repliable, Flotte ─────────
//
// Toujours visible, y compris sur un registre vide — c'est justement là que
// le geste manque le plus. `onCreated` ferme la section et bascule sur le
// projet créé, par le même chemin que cliquer une ligne du tableau (aucune
// route dédiée à réinventer, et le sélecteur de projets — ce même tableau —
// le montrera dès qu'on y revient, sans redémarrer le cockpit).
function renderNewProjectSection(ctx, onCreated) {
  const section = document.createElement('div');
  section.className = 'pl-section';
  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'btn pri';
  toggleBtn.textContent = '+ Nouveau projet';
  section.append(toggleBtn);

  let open = false;
  let formHost = null;
  toggleBtn.addEventListener('click', () => {
    open = !open;
    if (!open) {
      if (formHost) { formHost.remove(); formHost = null; }
      return;
    }
    formHost = document.createElement('div');
    formHost.className = 'pl-card';
    formHost.style.marginTop = 'var(--sp-3)';
    formHost.append(text('p', 'lbl', 'Chargement…'));
    section.append(formHost);
    renderCreateProjectForm(ctx, (slug) => {
      open = false;
      if (formHost) { formHost.remove(); formHost = null; }
      onCreated(slug);
    }).then((node) => { if (formHost) formHost.replaceWith(node); formHost = node; });
  });
  return section;
}

function renderFleet(root, ctx, rows, onSelect, onRefresh) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-wrap';
  wrap.append(renderNewProjectSection(ctx, onSelect));

  if (onRefresh) {
    const refreshBtn = document.createElement('button');
    refreshBtn.type = 'button';
    refreshBtn.className = 'btn';
    refreshBtn.textContent = 'Rafraîchir la flotte';
    refreshBtn.title = "Relit health() et memoryStatus() pour chaque projet, sans attendre le cache (60 s).";
    refreshBtn.addEventListener('click', () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = 'Rafraîchissement…';
      onRefresh();
    });
    wrap.append(refreshBtn);
  }

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
    if (!r.entry.managed) kitCell.append(pill('warn', 'non initialisé'));
    else if (!r.health) kitCell.append(pill('', 'indisponible'));
    else kitCell.append(pill(r.health.kit.upToDate ? 'ok' : 'warn', r.health.kit.aligned || 'inconnue'));
    tr.append(kitCell);

    const ciCell = document.createElement('td');
    const status = r.health?.ci_status;
    ciCell.append(pill(ciDotClass(status), ciWord(status)));
    tr.append(ciCell);

    tr.append(text('td', 'mono', fmtInt(r.health?.commits_total)));

    const afCell = document.createElement('td');
    afCell.append(text('span', 'lbl', r.health?.antifragile == null ? (r.health?.antifragile_note || 'pas encore mesurée') : `${r.health.antifragile}/100`));
    tr.append(afCell);

    const memCell = document.createElement('td');
    if (r.memory && r.memory.state === 'ok') memCell.append(pill('ok', `${fmtInt(r.memory.entries)} entrée(s)`));
    else if (r.memory && r.memory.state === 'unavailable') memCell.append(pill('warn', 'indisponible'));
    else memCell.append(pill('', 'non initialisée'));
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
    card.append(pill(r.health?.kit?.upToDate ? 'ok' : 'warn', r.health?.kit?.aligned || (r.entry.managed ? 'inconnue' : 'non initialisé')));
    card.append(pill(ciDotClass(r.health?.ci_status), 'CI ' + ciWord(r.health?.ci_status)));
    card.append(text('div', 'pl-card-row lbl', `${fmtInt(r.health?.commits_total)} commits · ${fmtInt((r.health?.flows || []).length)} flow(s)`));
    cards.append(card);
  }
  wrap.append(cards);

  root.append(wrap);
}

// ── Niveau Projet (fiche) ────────────────────────────────────────────────────

async function loadSheet(ctx, slug) {
  const [health, memory, doctor, agents, proposals, setupRun, upgradeRuns] = await Promise.all([
    ctx.api.health(slug).catch(() => null),
    ctx.api.memoryStatus(slug).catch(() => null),
    ctx.api.doctor(slug).catch(() => null),
    ctx.api.agents(slug).catch(() => null),
    ctx.api.proposals(slug).catch(() => null),
    // Dernière exécution du wizard (#171) : lue depuis le journal persistant
    // (`_grimoire/setup-run.json`), donc encore là après ce refresh — pas
    // seulement le temps d'un toast.
    ctx.api.setupRun(slug).catch(() => null),
    // Restes #510/#513 : le dernier run `project-upgrade` de CE projet,
    // avec son statut live — d'où dérive le badge « checkpoint destructif
    // en attente », jamais d'un état client posé après un clic (voir
    // `checkpointPendingRunId`, `renderSheet`). `slug` explicite (jamais
    // `host.project` implicite) : cette fiche peut être celle d'un AUTRE
    // projet que celui déjà résolu par l'hôte (navigation Flotte → Projet).
    ctx.api.flowRuns('project-upgrade', slug).catch(() => null),
  ]);
  return { health, memory, doctor, agents, proposals, setupRun, upgradeRuns };
}

// ── Propositions d'artefact (#395) : à la répétition d'un non-choix ────────
//
// Toujours dans la fiche projet, section agents (#382) : une proposition
// n'est rien d'autre qu'une décision différée sur un agent, un skill, ou une
// réparation (`repair`, #502 — une référence morte que la mise à jour a
// trouvée mais n'a pas touchée d'elle-même). Décider — accepter ou refuser —
// fonctionne pour n'importe quel projet du registre depuis le cockpit
// (#490) : c'est la même porte que le bouton « Mettre à jour » juste
// au-dessus, jamais la garde `readOnly` générale de la vue de travail.
// Accepter écrit l'artefact réel (ou applique la substitution `repair`
// évidente) et re-fetch `agents()` pour que la table au-dessus le montre
// aussitôt ; refuser ne fait que marquer la proposition.

//: Préfixe que `_accept_repair` (grimoire/proposals.py) reconnaît seul comme
//: une substitution qu'il peut appliquer sans intervention humaine — même
//: motif que côté serveur, dupliqué ici pour décider quels boutons montrer
//: sans faire d'aller-retour réseau.
const REPAIR_SUBSTITUTION_RE = /^substitution évidente\s*:/;

function repairHasEvidentSubstitution(p) {
  return REPAIR_SUBSTITUTION_RE.test(p.carrier_reason || '');
}

// `artifact_ref` porte "<chemin>:<ligne> → <cible morte>" (même forme que
// `_dead_reference_strings`, core/integrity.py) — jamais autre chose pour un
// type `repair`. `indexOf` plutôt que `split(':')` : un chemin Windows aurait
// pu porter un deuxième ':' (lecteur), même si le kit ne cible que POSIX ici.
function parseArtifactRef(ref) {
  const raw = String(ref || '');
  const colon = raw.indexOf(':');
  if (colon < 0) return null;
  const rest = raw.slice(colon + 1);
  const arrow = rest.indexOf(' → ');
  if (arrow < 0) return null;
  const line = parseInt(rest.slice(0, arrow), 10);
  const file = raw.slice(0, colon);
  if (!file || Number.isNaN(line)) return null;
  return { file, line };
}

// `artifact_type` (grimoire.proposals) porte six valeurs — avant la revue
// 2026-09, seules trois avaient un libellé et toutes partageaient le même
// point orange (`dot('warn')`), sans distinction visuelle. `PROPOSAL_TYPE_DOT`
// répare ça sur le vocabulaire des séries et des états déjà en place —
// jamais la couleur seule, toujours doublée du libellé ci-dessous.
const PROPOSAL_TYPE_LABEL = {
  skill: 'skill',
  repair: 'réparation',
  agent: 'agent',
  'memory-link': 'lien mémoire',
  'override-migration': 'migration override',
  'needs-hosts': 'besoins / hôtes',
};
const PROPOSAL_TYPE_DOT = {
  skill: 's2',
  repair: 'warn',
  agent: 's1',
  'memory-link': 'ok',
  'override-migration': 'bad',
  'needs-hosts': 's3',
};

function proposalTypeLabel(p) {
  return PROPOSAL_TYPE_LABEL[p.artifact_type] || p.artifact_type || 'agent';
}

function proposalTypeDot(p) {
  return PROPOSAL_TYPE_DOT[p.artifact_type] || '';
}

function proposalFacts(p) {
  if (p.artifact_type === 'repair') {
    // Ni compte de non-choix ni agent de repli pour une réparation — c'est
    // un défaut de fichier, pas un manque de spécialiste. `carrier_reason`
    // porte déjà toute l'explication (substitution trouvée, ou son absence).
    const bits = [];
    if (p.category) bits.push(`catégorie « ${p.category} »`);
    if (p.carrier_reason) bits.push(p.carrier_reason);
    return bits.join(' · ');
  }
  const bits = [`${fmtInt(p.count)} non-choix`];
  if (p.category) bits.push(`catégorie « ${p.category} »`);
  if (p.artifact_type === 'skill' && p.target_agent) bits.push(`à attacher à ${p.target_agent}`);
  // Le porteur retenu peut différer du repli brut observé (`fallback_agent`)
  // quand ce dernier est la persona d'entrée — `carrier_reason` explique le
  // choix (issue #402) : « repli observé », « porteur par catégorie : X »,
  // ou « persona d'entrée exclue, aucun porteur : agent ».
  if (p.carrier_reason) bits.push(p.carrier_reason);
  return bits.join(' · ');
}

// `slug` cible le projet dont cette fiche parle — jamais celui, ambiant, que
// le cockpit sert par défaut : c'est ce que `ctx.api.proposalAction` envoie
// désormais explicitement (#490), pour que décider une proposition sur un
// projet de la Flotte ne touche jamais un autre projet par erreur de query
// string ambiante.
function renderProposalsSection(ctx, proposalsPayload, onChanged, slug) {
  const section = document.createElement('div');
  section.className = 'pl-section';
  section.append(text('h3', null, 'Propositions'));

  const all = proposalsPayload?.proposals || [];
  const pending = all.filter((p) => p.status === 'pending');
  if (!pending.length) {
    // `.soft` (--ink2), pas `.lbl` (--ink3) : à cette taille de police, --ink3
    // ne tient pas le contraste 4.5:1 mesuré par
    // `tests/e2e/test_workspace_shell.py::test_aucune_encre_rendue_sous_45`.
    section.append(text('p', 'soft', 'Aucune proposition en attente — le déclencheur agit sur les non-choix répétés (`grimoire agent-miss`).'));
    return section;
  }

  const list = document.createElement('div');
  list.className = 'pl-watch';
  for (const proposal of pending) {
    const line = document.createElement('div');
    line.className = 'pl-watch-row pl-prop-row';

    const head = document.createElement('div');
    head.className = 'pl-prop-head';
    head.append(
      pill(proposalTypeDot(proposal), proposalTypeLabel(proposal)),
      text('span', 'pl-watch-name', proposal.specialty),
      text('span', 'lbl', proposalFacts(proposal)),
    );
    line.append(head);
    if (proposal.use_when) line.append(text('div', 'pl-prop-facts', proposal.use_when));

    const actions = document.createElement('div');
    actions.className = 'pl-prop-actions';

    const isRepair = proposal.artifact_type === 'repair';
    const canApply = !isRepair || repairHasEvidentSubstitution(proposal);

    // Une réparation sans substitution évidente n'offre jamais « Accepter » :
    // `_accept_repair` la refuserait de toute façon (« revue humaine
    // requise ») — mieux vaut ne pas montrer un bouton qui échoue toujours.
    if (canApply) {
      const acceptBtn = document.createElement('button');
      acceptBtn.type = 'button';
      acceptBtn.className = 'btn pri';
      acceptBtn.textContent = 'Accepter';
      acceptBtn.addEventListener('click', async () => {
        ctx.dock.echo(`grimoire proposals accept ${proposal.slug}`);
        const result = await ctx.api.proposalAction(proposal.slug, 'accept', slug).catch((error) => ({ ok: false, error: error.message }));
        if (!result.ok) { ctx.dock.echo(`refusé : ${result.error}`); return; }
        onChanged();
      });
      actions.append(acceptBtn);
    }

    const rejectBtn = document.createElement('button');
    rejectBtn.type = 'button';
    rejectBtn.className = 'btn';
    rejectBtn.textContent = 'Refuser';
    rejectBtn.addEventListener('click', async () => {
      ctx.dock.echo(`grimoire proposals reject ${proposal.slug}`);
      const result = await ctx.api.proposalAction(proposal.slug, 'reject', slug).catch((error) => ({ ok: false, error: error.message }));
      if (!result.ok) { ctx.dock.echo(`refusé : ${result.error}`); return; }
      onChanged();
    });
    actions.append(rejectBtn);

    if (isRepair) {
      const ref = parseArtifactRef(proposal.artifact_ref);
      if (ref) {
        const openBtn = document.createElement('button');
        openBtn.type = 'button';
        openBtn.className = 'btn';
        openBtn.textContent = 'Ouvrir le fichier';
        openBtn.title = `${ref.file}:${ref.line}`;
        openBtn.addEventListener('click', () => ctx.goto('source', { file: ref.file, line: ref.line }));
        actions.append(openBtn);
      }
    }

    line.append(actions);
    list.append(line);
  }
  section.append(list);
  return section;
}

// ── Agents (#374) : liste + inspecteur d'édition ────────────────────────────

const TOOL_VERBS = ['read', 'search', 'edit', 'execute', 'web'];

// Fraîcheur (#396) : verdict calculé sur le tag `agent.dispatch` du journal,
// le même que `grimoire doctor` — distinct du compteur `usage` ci-dessous
// (tout sous-agent tracé, #374). `judged` vaut faux quand le journal est trop
// jeune pour le seuil configuré : dans ce cas on ne dit rien plutôt que de
// confondre absence de données et absence d'usage.
function freshnessWord(freshness) {
  if (!freshness || !freshness.judged) return null;
  // Plancher par agent : un fichier plus jeune que le seuil n'a pas eu le
  // temps d'être choisi — ce n'est pas la même chose que « personne n'en
  // veut ». Le dire explicitement plutôt que de laisser lire « jamais
  // invoqué » comme un signal de dette.
  if (freshness.too_recent) return 'trop récent pour juger';
  if (freshness.last_seen == null) return 'jamais invoqué';
  return `invoqué il y a ${fmtInt(freshness.days_since)} j`;
}

function freshnessBadge(freshness) {
  if (!freshness || !freshness.judged) return null;
  if (freshness.too_recent) {
    const badge = text('span', 'pl-badge fresh', 'trop récent');
    badge.title = "Le fichier de définition de cet agent est plus jeune que le seuil de fraîcheur configuré — pas encore eu le temps d'être choisi.";
    return badge;
  }
  if (!freshness.stale) return null;
  const badge = text('span', 'pl-badge stale', 'périmé');
  badge.title = 'Aucun agent.dispatch journalisé depuis le seuil de fraîcheur configuré (project-context.yaml: agents.freshness_threshold_days).';
  return badge;
}

function usageWord(agent) {
  const usage = agent.usage;
  const fresh = freshnessWord(agent.freshness);
  const countPart = usage && usage.choices ? `${fmtInt(usage.choices)} choix` : null;
  if (countPart && fresh) return `${countPart} · ${fresh}`;
  if (fresh) return fresh;
  if (countPart) {
    const ago = relativeAge((Date.now() - new Date(usage.last_chosen_at).getTime()) / 60000);
    return `${countPart} · dernier ${ago}`;
  }
  return 'jamais choisi';
}

function renderAgentsTable(ctx, agentsPayload, selectedName, onSelect) {
  const section = document.createElement('div');
  section.className = 'pl-section';
  const heading = text('h3', null, 'Agents');
  heading.dataset.term = 'agent';
  section.append(heading);

  const agents = agentsPayload?.agents || [];
  if (!agents.length) {
    section.append(text('p', 'lbl', "Aucun agent lisible sur ce projet — l'API des agents a répondu vide ou n'a pas répondu."));
    return section;
  }

  const tableWrap = document.createElement('div');
  tableWrap.className = 'pl-table-wrap';
  const table = document.createElement('table');
  table.className = 'pl-table';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Agent', 'Couche', 'Outils', 'Skills', 'Usage']) {
    headRow.append(text('th', null, label));
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = document.createElement('tbody');
  for (const agent of agents) {
    const tr = document.createElement('tr');
    tr.tabIndex = 0;
    tr.setAttribute('aria-current', String(agent.name === selectedName));
    tr.addEventListener('click', () => onSelect(agent.name));
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSelect(agent.name); });

    const nameCell = document.createElement('td');
    const nameRow = row(text('span', null, agent.name));
    if (agent.entry_point) nameRow.append(text('span', 'lbl', '· entrée'));
    nameCell.append(nameRow);
    tr.append(nameCell);

    const layerCell = document.createElement('td');
    const layerBadge = text('span', 'pl-badge ' + agent.layer, agent.layer);
    if (agent.layer === 'overrides') layerBadge.dataset.term = 'override';
    layerCell.append(layerBadge);
    tr.append(layerCell);

    tr.append(text('td', 'lbl', agent.tools.join(', ') || '—'));
    tr.append(text('td', 'lbl', agent.skills.length ? String(agent.skills.length) : '—'));
    const usageCell = document.createElement('td');
    usageCell.append(text('span', 'lbl', usageWord(agent)));
    const badge = freshnessBadge(agent.freshness);
    if (badge) usageCell.append(document.createTextNode(' '), badge);
    tr.append(usageCell);
    tbody.append(tr);
  }
  table.append(tbody);
  tableWrap.append(table);
  section.append(tableWrap);
  return section;
}

function fieldRow(labelText, node) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-field';
  const label = document.createElement('label');
  label.textContent = labelText;
  wrap.append(label, node);
  return wrap;
}

function renderAgentInspector(ctx, agentsPayload, agent, callbacks) {
  const block = document.createElement('div');
  block.className = 'pl-insp-block';
  block.dataset.agentInspector = 'true';
  block.append(row(text('h4', null, agent.name), text('span', 'pl-badge ' + agent.layer, agent.layer)));
  const usageRow = row(text('span', 'lbl', usageWord(agent)));
  const inspBadge = freshnessBadge(agent.freshness);
  if (inspBadge) usageRow.append(inspBadge);
  block.append(usageRow);

  const readOnly = ctx.host.readOnly;
  const saveLabel = (verb) => (readOnly ? 'Écriture désactivée (cockpit)' : verb);

  // ── Clause d'emploi ──────────────────────────────────────────────────
  const useWhen = document.createElement('textarea');
  useWhen.rows = 2;
  useWhen.value = agent.use_when || '';
  useWhen.disabled = readOnly;
  const dontUseWhen = document.createElement('textarea');
  dontUseWhen.rows = 2;
  dontUseWhen.value = agent.dont_use_when || '';
  dontUseWhen.disabled = readOnly;

  const saveClauseBtn = document.createElement('button');
  saveClauseBtn.type = 'button';
  saveClauseBtn.className = 'btn';
  saveClauseBtn.textContent = saveLabel('Enregistrer la clause');
  saveClauseBtn.disabled = readOnly;
  saveClauseBtn.addEventListener('click', async () => {
    ctx.dock.echo(`# clause d'emploi — ${agent.name}`);
    try {
      await ctx.api.agentFields(agent.name, { use_when: useWhen.value, dont_use_when: dontUseWhen.value });
      callbacks.refresh(agent.name);
    } catch (error) {
      ctx.dock.log('doctor', 'refusé : ' + error.message);
    }
  });

  block.append(
    fieldRow('use_when', useWhen),
    fieldRow('dont_use_when', dontUseWhen),
    saveClauseBtn,
  );
  if (agent.tool_boundary) block.append(text('div', 'lbl', 'Frontière : ' + agent.tool_boundary));

  // ── Outils ───────────────────────────────────────────────────────────
  const toolOpts = document.createElement('div');
  toolOpts.className = 'pl-tool-opts';
  const toolChecks = {};
  for (const verb of TOOL_VERBS) {
    const label = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = agent.tools.includes(verb);
    cb.disabled = readOnly;
    toolChecks[verb] = cb;
    label.append(cb, document.createTextNode(verb));
    toolOpts.append(label);
  }
  const saveToolsBtn = document.createElement('button');
  saveToolsBtn.type = 'button';
  saveToolsBtn.className = 'btn';
  saveToolsBtn.textContent = saveLabel('Enregistrer les outils');
  saveToolsBtn.disabled = readOnly;
  saveToolsBtn.addEventListener('click', async () => {
    const chosen = TOOL_VERBS.filter((v) => toolChecks[v].checked);
    ctx.dock.echo(`# outils — ${agent.name} → ${chosen.join(', ')}`);
    try {
      await ctx.api.agentFields(agent.name, { tools: chosen });
      callbacks.refresh(agent.name);
    } catch (error) {
      ctx.dock.log('doctor', 'refusé : ' + error.message);
    }
  });
  block.append(fieldRow('tools', toolOpts), saveToolsBtn);

  // ── Contexte ─────────────────────────────────────────────────────────
  const contextArea = document.createElement('textarea');
  contextArea.rows = 3;
  contextArea.value = (agent.context || []).join('\n');
  contextArea.disabled = readOnly;
  contextArea.placeholder = 'un chemin de projet par ligne';
  const saveContextBtn = document.createElement('button');
  saveContextBtn.type = 'button';
  saveContextBtn.className = 'btn';
  saveContextBtn.textContent = saveLabel('Enregistrer le contexte');
  saveContextBtn.disabled = readOnly;
  saveContextBtn.addEventListener('click', async () => {
    const paths = contextArea.value.split('\n').map((s) => s.trim()).filter(Boolean);
    ctx.dock.echo(`# contexte — ${agent.name}`);
    try {
      await ctx.api.agentFields(agent.name, { context: paths });
      callbacks.refresh(agent.name);
    } catch (error) {
      ctx.dock.log('doctor', 'refusé : ' + error.message);
    }
  });
  block.append(fieldRow('context', contextArea), saveContextBtn);

  // ── Skills ───────────────────────────────────────────────────────────
  const skillsWrap = document.createElement('div');
  skillsWrap.className = 'pl-chips';
  for (const slug of agent.skills) {
    const chip = document.createElement('span');
    chip.className = 'pl-chip';
    chip.append(document.createTextNode(slug));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.disabled = readOnly;
    remove.setAttribute('aria-label', `Retirer ${slug}`);
    remove.addEventListener('click', async () => {
      ctx.dock.echo(`# retirer skill — ${agent.name} : ${slug}`);
      try {
        await ctx.api.agentSkill(agent.name, slug, 'remove');
        callbacks.refresh(agent.name);
      } catch (error) {
        ctx.dock.log('doctor', 'refusé : ' + error.message);
      }
    });
    chip.append(remove);
    skillsWrap.append(chip);
  }
  if (!agent.skills.length) skillsWrap.append(text('span', 'lbl', 'aucun skill attaché'));

  const assignRow = document.createElement('div');
  assignRow.className = 'row';
  assignRow.style.marginTop = '6px';
  const select = document.createElement('select');
  const available = (agentsPayload.skills || []).filter((s) => !agent.skills.includes(s.slug));
  for (const skill of available) {
    const option = document.createElement('option');
    option.value = skill.slug;
    option.textContent = skill.name || skill.slug;
    select.append(option);
  }
  select.disabled = readOnly || !available.length;
  const assignBtn = document.createElement('button');
  assignBtn.type = 'button';
  assignBtn.className = 'btn';
  assignBtn.textContent = saveLabel('Assigner');
  assignBtn.disabled = readOnly || !available.length;
  assignBtn.addEventListener('click', async () => {
    const slug = select.value;
    if (!slug) return;
    ctx.dock.echo(`# assigner skill — ${agent.name} : ${slug}`);
    try {
      await ctx.api.agentSkill(agent.name, slug, 'assign');
      callbacks.refresh(agent.name);
    } catch (error) {
      ctx.dock.log('doctor', 'refusé : ' + error.message);
    }
  });
  assignRow.append(select, assignBtn);

  block.append(fieldRow('skills', skillsWrap), assignRow);
  return block;
}

// ── Wizard de setup — le wizard exécute (#171) ──────────────────────────────
//
// Remplace le repli « copier-coller la commande » sur le projet d'accueil
// (écritures actives, `!ctx.host.readOnly`) : exécution réelle par la même
// mécanique que `grimoire up` (`project_setup.execute_setup_plan`, jamais un
// sous-processus), needs pré-cochés depuis `needs_suggest.py` (B3 rebranché
// sur B2), état final vérifiable (doctor). Le mode copier-coller reste un
// repli explicite, toujours accessible en dessous. Naviguer vers un AUTRE
// projet du registre (cockpit, lecture seule) garde l'ancien message : la
// Console ne lance jamais `grimoire init` à distance sur ce cas-là.

function stepStatusWord(status) {
  return { done: 'fait', skipped: 'sans effet', failed: 'échec', planned: 'prévu' }[status] || status;
}

function renderRunReport(run) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-preview';
  wrap.append(row(
    dot(run.ok ? 'ok' : 'bad'),
    text('span', null, run.ok ? 'Projet initialisé.' : 'Initialisation en échec.'),
    text('span', 'lbl', run.doctorOk ? 'doctor conforme ✓' : 'doctor à revoir'),
  ));
  const list = document.createElement('div');
  list.className = 'pl-watch';
  list.style.marginTop = '8px';
  for (const step of run.steps || []) {
    const line = document.createElement('div');
    line.className = 'pl-watch-row';
    line.append(
      dot(step.status === 'failed' ? 'bad' : (step.status === 'done' ? 'ok' : '')),
      row(
        text('span', 'pl-watch-name', step.step),
        text('span', 'pl-watch-reason lbl', `${stepStatusWord(step.status)} — ${step.detail}`),
      ),
    );
    list.append(line);
  }
  wrap.append(list);
  if ((run.extensionErrors || []).length) {
    wrap.append(text('div', 'lbl', 'Extensions en échec : ' + run.extensionErrors.join(' · ')));
  }
  return wrap;
}

// Champs partagés par les deux formulaires de setup — celui qui initialise le
// projet déjà servi (`renderSetupWizard`) et celui qui en crée un nouveau
// depuis le portefeuille (`renderCreateProjectForm`, #172). Un seul endroit
// qui sait construire les sélecteurs archétype/backend/needs et lire leur
// valeur : les deux formulaires ne diffèrent que par leur cible (le projet
// servi, ou un chemin choisi) et leur appel API final.
async function buildSetupFields(ctx, wrap) {
  const [archetypes, backendsPayload, needsPayload] = await Promise.all([
    ctx.api.archetypesCatalogue().catch(() => []),
    ctx.api.backendsCatalogue().catch(() => ({ backends: [] })),
    ctx.api.needsCatalogue().catch(() => ({ needs: [], suggested: [] })),
  ]);

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.placeholder = ctx.host.project || 'nom du projet';
  const userInput = document.createElement('input');
  userInput.type = 'text';
  userInput.placeholder = 'votre nom (artefacts)';

  const archSelect = document.createElement('select');
  for (const a of (archetypes || [])) {
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.textContent = a.name || a.id;
    archSelect.append(opt);
  }
  if (!archSelect.options.length) {
    const opt = document.createElement('option');
    opt.value = 'minimal';
    opt.textContent = 'minimal';
    archSelect.append(opt);
  }

  const backendSelect = document.createElement('select');
  const autoOpt = document.createElement('option');
  autoOpt.value = 'auto';
  autoOpt.textContent = 'auto (recommandé)';
  backendSelect.append(autoOpt);
  for (const b of (backendsPayload.backends || [])) {
    if (b.id === 'auto') continue;
    const opt = document.createElement('option');
    opt.value = b.id;
    opt.textContent = b.label || b.id;
    backendSelect.append(opt);
  }

  const suggestedIds = new Set((needsPayload.suggested || []).map((s) => s.id));
  const needsWrap = document.createElement('div');
  needsWrap.className = 'pl-tool-opts';
  const needChecks = {};
  for (const n of (needsPayload.needs || [])) {
    const label = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = suggestedIds.has(n.id);
    if (n.rationale) label.title = n.rationale;
    needChecks[n.id] = cb;
    label.append(cb, document.createTextNode(n.label || n.id));
    needsWrap.append(label);
  }
  if ((needsPayload.suggested || []).length) {
    const hint = needsPayload.suggested.map((s) => s.reason).join(' · ');
    wrap.append(text('p', 'lbl', 'Suggéré pour ce projet : ' + hint));
  }

  wrap.append(
    fieldRow('nom', nameInput),
    fieldRow('votre nom', userInput),
    fieldRow('archétype', archSelect),
    fieldRow('mémoire / BDD', backendSelect),
    fieldRow('needs (standard agentique)', needsWrap),
  );

  return {
    summary: () => `--archetype ${archSelect.value} --backend ${backendSelect.value}`,
    payload: (extra) => ({
      name: nameInput.value.trim(),
      user: userInput.value.trim(),
      archetype: archSelect.value,
      backend: backendSelect.value,
      needs: Object.keys(needChecks).filter((id) => needChecks[id].checked),
      ...extra,
    }),
  };
}

async function renderSetupWizard(ctx, options) {
  const wrap = document.createElement('div');
  wrap.append(text('p', 'lbl', "Ce projet n'est pas initialisé — l'exécuter écrit réellement le projet."));

  const fields = await buildSetupFields(ctx, wrap);

  const runBtn = document.createElement('button');
  runBtn.type = 'button';
  runBtn.className = 'btn pri';
  runBtn.textContent = 'Initialiser le projet';

  const preview = document.createElement('div');
  preview.hidden = true;

  const fallbackBtn = document.createElement('button');
  fallbackBtn.type = 'button';
  fallbackBtn.className = 'btn';
  fallbackBtn.textContent = 'ou : obtenir la commande à copier-coller';

  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    runBtn.textContent = 'Initialisation…';
    preview.hidden = false;
    preview.replaceChildren(text('p', 'lbl', 'grimoire up — en cours…'));
    ctx.dock.echo(`grimoire up . ${fields.summary()}`);
    try {
      await ctx.api.setupPlan(fields.payload({ planOnly: false }));
      // Le rapport (doctor compris) est désormais un bloc persistant de la
      // fiche — lu depuis `_grimoire/setup-run.json`, pas ce `preview` que le
      // refresh qui suit va de toute façon effacer avec le reste de la fiche.
      options.refresh();
      return;
    } catch (error) {
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = 'Initialiser le projet';
    }
  });

  fallbackBtn.addEventListener('click', async () => {
    fallbackBtn.disabled = true;
    try {
      const plan = await ctx.api.setupPlan(fields.payload({ planOnly: true }));
      preview.hidden = false;
      preview.replaceChildren(text('p', 'lbl', 'Plan écrit dans _grimoire/setup-plan.json — terminez :'));
      const code = document.createElement('code');
      code.className = 'mono';
      code.textContent = plan.initCommand;
      preview.append(code);
    } catch (error) {
      preview.hidden = false;
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
    } finally {
      fallbackBtn.disabled = false;
    }
  });

  wrap.append(runBtn, fallbackBtn, preview);
  return wrap;
}

// ── Nouveau projet depuis le portefeuille (#172, volet création) ───────────
//
// Même formulaire que `renderSetupWizard`, plus un chemin explicite : la
// cible n'est pas le projet servi mais un dossier choisi, qui ne doit pas
// déjà être un projet (`POST /api/projects/create`, refusé sinon par le
// serveur — voir cmd_cockpit.py). À la réussite, le registre a déjà le
// nouveau projet (grimoire up l'enrôle comme tout `up`/`init` normal) :
// `onCreated(slug)` referme la modale et rafraîchit la Flotte sans recharger
// la page.
async function renderCreateProjectForm(ctx, onCreated) {
  const wrap = document.createElement('div');
  wrap.append(text('p', 'lbl', 'Un chemin qui n’est pas déjà un projet Grimoire. Le dossier est créé si besoin.'));

  const pathInput = document.createElement('input');
  pathInput.type = 'text';
  pathInput.placeholder = '/chemin/absolu/du/nouveau-projet';
  wrap.append(fieldRow('chemin', pathInput));

  const fields = await buildSetupFields(ctx, wrap);

  const createBtn = document.createElement('button');
  createBtn.type = 'button';
  createBtn.className = 'btn pri';
  createBtn.textContent = 'Créer le projet';

  const preview = document.createElement('div');
  preview.hidden = true;

  createBtn.addEventListener('click', async () => {
    const path = pathInput.value.trim();
    if (!path) { pathInput.focus(); return; }
    createBtn.disabled = true;
    createBtn.textContent = 'Création…';
    preview.hidden = false;
    preview.replaceChildren(text('p', 'lbl', 'grimoire up ' + path + ' ' + fields.summary() + ' — en cours…'));
    try {
      const result = await ctx.api.createProject(fields.payload({ path }));
      onCreated(result.slug);
    } catch (error) {
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
      createBtn.disabled = false;
      createBtn.textContent = 'Créer le projet';
    }
  });

  wrap.append(createBtn, preview);
  return wrap;
}

// ── Déroulé du flow de mise à jour, nœud par nœud (issue #506) ─────────────
//
// `report.nodes`/`result.nodes` (project_update.py::_node_statuses) porte un
// statut par nœud du blueprint `project-upgrade`, dans l'ordre fixe — jamais
// déduit du texte libre `output`. Les libellés ici sont d'affichage
// seulement ; l'identité (`node.id`) reste celle du blueprint.

const NODE_LABELS = {
  backup: 'Sauvegarde', preview: 'Aperçu', orphans: 'Orphelins', apply: 'Application',
  overrides: 'Overrides', memory: 'Mémoire', 'needs-hosts': 'Besoins / hôtes',
  verify: 'Vérification', destructive: 'Destructif (checkpoint)',
};

const NODE_STATUS_WORD = {
  fait: 'fait',
  proposition: 'proposition écrite — à décider dans Propositions',
  sauté: 'jamais atteint',
  erreur: 'en erreur',
  'checkpoint en attente': 'arrêté ici, jamais décidé à votre place',
};

const NODE_STATUS_DOT = {
  fait: 'ok', proposition: 'acc', sauté: '', erreur: 'bad', 'checkpoint en attente': 'warn',
};

function renderNodeList(nodes) {
  const list = document.createElement('div');
  list.className = 'pl-watch pl-nodes';
  for (const node of nodes) {
    const nodeRow = document.createElement('div');
    nodeRow.className = 'pl-watch-row';
    nodeRow.append(
      text('span', 'pl-watch-name', NODE_LABELS[node.id] || node.id),
      pill(NODE_STATUS_DOT[node.status] ?? '', NODE_STATUS_WORD[node.status] || node.status),
    );
    list.append(nodeRow);
  }
  return list;
}

//: Un rapport CLI/Rich complet peut dépasser largement cette taille ; le
//: cockpit n'a pas besoin du journal entier pour montrer ce qui a tourné, et
//: une UI qui charge tout gèlerait sur un gros projet. `truncateMarkdown`
//: coupe sur une frontière de ligne et referme un bloc de code resté ouvert.
const PREVIEW_MARKDOWN_LIMIT = 4000;

// Rend `preview.md`/`report.md` (ou, à défaut, la sortie brute) en Markdown
// structuré — titres et blocs de code, jamais un `<pre>` unique où les
// caractères de mise en forme de la commande sous-jacente s'affichent tels
// quels. `preview-wrap` cassait l'alignement d'un bloc de code multi-lignes
// à chaque redimensionnement ; un bloc de code garde son défilement
// horizontal propre (voir `.pl-markdown pre` dans les styles du module).
function appendPreviewMarkdown(container, previewText) {
  if (!previewText) return;
  const { text: shown, truncated } = truncateMarkdown(previewText, PREVIEW_MARKDOWN_LIMIT);
  const rendered = document.createElement('div');
  rendered.className = 'pl-markdown';
  rendered.innerHTML = renderMarkdown(shown);
  container.append(rendered);
  if (truncated) {
    container.append(text('div', 'lbl', 'Aperçu tronqué — le rapport complet reste sur disque sous `_grimoire-output/upgrade/<date>/`.'));
  }
}

function renderSheet(root, ctx, slug, name, sheet, options) {
  const wrap = document.createElement('div');
  wrap.className = 'pl-sheet';
  const { health, memory } = sheet;

  wrap.append(text('h2', null, name || slug || ctx.host.project || 'Projet servi'));

  const kpi = kpiCard([
    { value: health?.kit?.scaffolded ? kitStatus(health.kit).word : 'absent', label: 'kit' },
    { value: ciWord(health?.ci_status), label: 'CI' },
    { value: fmtInt(health?.commits_total), label: 'commits' },
    { value: fmtInt((health?.flows || []).length), label: 'flows' },
  ]);
  wrap.append(kpi);

  root.append(wrap);

  // ── Inspecteur : kit, hôtes, standard, actions ─────────────────────────
  ctx.inspector.replaceChildren();

  const kitBlock = document.createElement('div');
  kitBlock.className = 'pl-insp-block';
  kitBlock.append(text('h4', null, 'Kit'));
  const kit = kitStatus(health?.kit);
  const kitRow = pill(kit.dot, kit.word);
  kitBlock.append(kitRow);
  if (health?.kit?.aligned) kitBlock.append(text('div', 'lbl', `aligné sur ${health.kit.aligned}, installé ${health.kit.installed}`));
  const toolLine = projectToolLine(health?.kit);
  if (toolLine) kitBlock.append(text('div', 'lbl', toolLine));
  // Le badge « en retard (N) » nommait N sans jamais dire lesquels
  // (`kit.behindFiles`, déjà rendu par le serveur, jusqu'ici jamais lu ici).
  let behindBlock = null;
  const behindFiles = health?.kit?.behindFiles || [];
  if (behindFiles.length) {
    behindBlock = text('div', 'lbl', `En retard : ${behindFiles.join(', ')}`);
    kitBlock.append(behindBlock);
  }
  ctx.inspector.append(kitBlock);

  // Après un run confirmé arrêté au checkpoint `destructive` (le seul point
  // d'arrêt d'un flow complet, #490), le badge doit dire ce qui vient de se
  // passer plutôt que garder le même libellé qu'avant toute action.
  //
  // Restes #510/#513 (validation finale de la boucle de mise à jour) :
  // jusqu'ici purement client, ce badge ne survivait pas à une navigation
  // Flotte → Projet — un rechargement de la fiche perdait l'information
  // que le run précédent avait tourné jusqu'au bout sans qu'on décide du
  // checkpoint. Il dérive donc maintenant aussi du backend, à CHAQUE rendu
  // (voir `checkpointPendingRunId` plus bas, depuis `GET /api/workspace/
  // flows/runs`, #513), pas seulement juste après un clic « Confirmer ».
  function markKitCheckpointPending(runId) {
    kitRow.className = 'chip pill warn';
    kitRow.textContent = 'mis à niveau, checkpoint destructif en attente';
    if (runId) kitRow.title = `grimoire flow status ${runId}`;
    const kitCell = kpi.querySelector('.pl-kpi-item .pl-kpi-val');
    if (kitCell) kitCell.textContent = 'checkpoint en attente';
    if (behindBlock) { behindBlock.remove(); behindBlock = null; }
  }

  // Le dernier run `project-upgrade` de CE projet (route `flows/runs`,
  // #513) : `status`/`currentNode` viennent du kernel réel (issue
  // #510/#513, `flow_runs.list_flow_runs`), jamais d'un état client posé
  // après un clic — c'est ce qui permet au badge de survivre à un
  // rechargement de la fiche.
  const upgradeRuns = Array.isArray(sheet.upgradeRuns?.runs) ? sheet.upgradeRuns.runs : [];
  const latestUpgradeRun = upgradeRuns[0] || null;
  let checkpointPendingRunId = (
    latestUpgradeRun
    && latestUpgradeRun.status === 'checkpointed'
    && latestUpgradeRun.currentNode === 'destructive'
  ) ? latestUpgradeRun.runId : null;
  if (checkpointPendingRunId) markKitCheckpointPending(checkpointPendingRunId);

  const standardBlock = document.createElement('div');
  standardBlock.className = 'pl-insp-block';
  standardBlock.append(text('h4', null, 'Standard'));
  if (sheet.doctor) {
    standardBlock.append(pill(sheet.doctor.ok ? 'ok' : 'bad', sheet.doctor.ok ? 'doctor conforme' : 'doctor en écart'));
  } else {
    standardBlock.append(text('div', 'lbl', 'diagnostic indisponible'));
  }
  ctx.inspector.append(standardBlock);

  const memBlock = document.createElement('div');
  memBlock.className = 'pl-insp-block';
  memBlock.append(text('h4', null, 'Mémoire'));
  memBlock.append(pill(memory?.state === 'ok' ? 'ok' : (memory?.state === 'unavailable' ? 'warn' : ''), memory?.configuredBackend ? `${memory.configuredBackend} · ${fmtInt(memory.entries)} entrée(s)` : 'non initialisée'));
  ctx.inspector.append(memBlock);

  // ── Dernière exécution du wizard (#171) ─────────────────────────────────
  //
  // Lue depuis le journal persistant (`_grimoire/setup-run.json`), pas
  // seulement le temps d'un toast : `options.refresh()` recharge la fiche
  // après une exécution, et ce bloc doit encore montrer le rapport (doctor
  // compris) une fois la fiche redessinée.
  if (sheet.setupRun && sheet.setupRun.available) {
    const runBlock = document.createElement('div');
    runBlock.className = 'pl-insp-block';
    runBlock.append(text('h4', null, 'Dernière initialisation (wizard)'));
    runBlock.append(renderRunReport(sheet.setupRun));
    ctx.inspector.append(runBlock);
  }

  const actionsBlock = document.createElement('div');
  actionsBlock.className = 'pl-insp-block pl-actions';
  actionsBlock.append(text('h4', null, 'Actions'));

  if (!health?.kit?.scaffolded) {
    if (ctx.host.readOnly) {
      // Naviguer vers un AUTRE projet du registre depuis le cockpit reste en
      // lecture seule : la Console ne lance jamais `grimoire init` à distance
      // sur un projet qu'on ne fait que regarder (#356).
      const initRow = document.createElement('div');
      initRow.append(text('p', 'lbl', "Ce projet n'est pas initialisé. La Console ne lance jamais `grimoire init` à distance :"));
      const code = document.createElement('code');
      code.className = 'mono';
      code.textContent = `grimoire init ${slug ? '(dans le dossier du projet)' : '.'}`;
      initRow.append(code);
      actionsBlock.append(initRow);
    } else {
      const wizardHost = document.createElement('div');
      wizardHost.append(text('p', 'lbl', 'Chargement du wizard…'));
      actionsBlock.append(wizardHost);
      renderSetupWizard(ctx, options).then((node) => wizardHost.replaceWith(node));
    }
  }

  const updateBtn = document.createElement('button');
  updateBtn.type = 'button';
  updateBtn.className = 'btn';
  updateBtn.textContent = 'Mettre à jour — aperçu';
  const preview = document.createElement('div');
  preview.className = 'pl-preview';
  preview.hidden = true;
  updateBtn.addEventListener('click', async () => {
    ctx.dock.echo(`grimoire upgrade-flow run --dry-run${slug ? ' # ' + slug : ''}`);
    // État « en cours » (issue #506) : le flow peut prendre jusqu'à une
    // minute (backup + `up --dry-run` + `host sync --dry-run`) — sans ceci,
    // l'écran passait de « cliqué » à « terminé » sans rien entre.
    updateBtn.disabled = true;
    updateBtn.textContent = 'Aperçu en cours…';
    preview.hidden = false;
    preview.replaceChildren(row(dot('acc'), text('span', 'lbl', "Aperçu en cours — jusqu'à une minute…")));
    try {
      const report = await ctx.api.updateProject(slug, false);
      preview.replaceChildren();
      preview.append(text('div', null, report.ok ? "Aperçu réussi (sauvegarde + preview, rien d'autre écrit)." : (report.error || 'Aperçu en échec.')));
      if (report.nodes) preview.append(renderNodeList(report.nodes));
      if (report.backupPath) preview.append(text('div', 'lbl', `Sauvegarde : ${report.backupPath}`));
      appendPreviewMarkdown(preview, report.preview || report.output);
      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'btn pri';
      confirmBtn.textContent = 'Confirmer la mise à jour';
      confirmBtn.style.marginTop = '8px';
      confirmBtn.addEventListener('click', async () => {
        ctx.dock.echo(`grimoire upgrade-flow run --executor interactive${slug ? ' # ' + slug : ''}`);
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Mise à jour en cours…';
        const progress = row(dot('acc'), text('span', 'lbl', "Mise à jour en cours — jusqu'à une minute, ne fermez pas cet onglet."));
        preview.append(progress);
        const result = await ctx.api.updateProject(slug, true).catch((error) => ({ ok: false, error: error.message }));
        progress.remove();
        preview.append(text('div', 'lbl', result.ok
          ? "Mis à jour — le flow s'est arrêté au checkpoint final, jamais décidé à votre place."
          : ('Échec de la mise à jour' + (result.error ? ` : ${result.error}` : '.'))));
        if (result.nodes) preview.append(renderNodeList(result.nodes));
        if (result.backupPath) preview.append(text('div', 'lbl', `Sauvegarde : ${result.backupPath}`));
        // `proposals` est joint par le backend dès que le run les connaît —
        // `completed`, `upgraded-checkpoint-pending` ET `upgraded-but-failed`
        // (`project_update.py::_run_upgrade_flow`) — jamais seulement sur
        // succès. Garder cette condition sur `result.ok` (issue #538) faisait
        // disparaître la section Propositions ET son rafraîchissement dès
        // qu'`apply` refusait après écriture, alors que des propositions
        // pouvaient déjà être en attente (ou tout juste écrites par ce run
        // avant l'échec) : `state: "upgraded-but-failed"` implique toujours
        // `ok: false` (`_derive_state`), les deux blocs ci-dessous restaient
        // donc systématiquement morts sur cet état précis.
        const hasProposals = Array.isArray(result.proposals);
        if (hasProposals && result.proposals.length) {
          preview.append(text('div', 'lbl', `${result.proposals.length} proposition(s) en attente — voir la section Propositions ci-dessous.`));
        }
        confirmBtn.remove();
        if (result.ok) {
          markKitCheckpointPending(result.runId);
          // Un run complet s'arrête toujours, non décidé, au checkpoint
          // `destructive` (jamais un autre point d'arrêt pour un run mené à
          // son terme) — le bouton « Revoir dans l'IDE » doit donc le
          // refléter tout de suite, sans attendre un rechargement complet
          // de la fiche. N'a de sens que sur un run qui a réellement atteint
          // ce checkpoint : jamais sur `upgraded-but-failed`, dont le nœud en
          // échec (`apply`) précède `destructive` dans le déroulé.
          checkpointPendingRunId = result.runId;
        }
        if (result.ok || hasProposals) {
          // Le prochain retour sur la Flotte servirait sinon la ligne mise
          // en cache d'avant la mise à jour jusqu'à 60 s (issue #510 point
          // 5) — ce projet précis doit se relire, pas toute la flotte.
          invalidateFleetCache(slug || ctx.host.project);
          // Jamais `options.refresh()` ici : il redessine toute la fiche et
          // effacerait ce déroulé à l'instant même où on vient de le montrer
          // (constat terrain, issue #506 — « l'Inspecteur revient
          // silencieusement à l'état initial »). Seules les propositions (et
          // la table d'agents qu'elles peuvent alimenter) ont besoin d'une
          // lecture fraîche ; `refreshProposals` — définie plus bas dans
          // cette même fiche — s'en charge sans toucher au reste du DOM.
          refreshProposals();
        }
      });
      preview.append(confirmBtn);
    } catch (error) {
      preview.replaceChildren(text('div', 'lbl', 'refusé : ' + error.message));
    } finally {
      updateBtn.disabled = false;
      updateBtn.textContent = 'Mettre à jour — aperçu';
    }
  });
  actionsBlock.append(updateBtn, preview);

  // ── Revoir dans l'IDE (issue #520, lot 2) ───────────────────────────────
  //
  // `grimoire upgrade-flow run` s'arrête volontairement, non décidé, au
  // checkpoint `destructive` ou sur une proposition V1 — rien depuis le
  // cockpit n'aidait jusqu'ici à ouvrir la revue de ces décisions ailleurs
  // que par la CLI à la main. Ce bouton n'écrit jamais rien lui-même : il
  // ne fait que préparer le texte que `/grimoire-upgrade-review` (le
  // prompt mission pack, issue #520 lot 1) attend, et le mettre à
  // disposition — presse-papiers si possible, affiché sinon. Aucun lien
  // d'ouverture d'IDE : rien dans ce projet ne déclare d'éditeur ouvrable
  // (aucun schéma `vscode://`, aucun champ de config) — inventer ce lien
  // promettrait une action que le cockpit ne peut pas tenir.
  //
  // Présent dans la page seulement s'il y a un travail réel à revoir —
  // jamais un bouton greyé sans objet, ni un bouton toujours là qui
  // inviterait à « revoir » un projet sans rien en attente.
  function pendingProposalSlugs(payload) {
    return (payload?.proposals || []).filter((p) => p.status === 'pending').map((p) => p.slug);
  }

  const reviewBtn = document.createElement('button');
  reviewBtn.type = 'button';
  reviewBtn.className = 'btn';
  reviewBtn.textContent = "Revoir dans l'IDE";
  const reviewPreview = document.createElement('div');
  // Classe distincte de `.pl-preview` (même style, `injectStyles` ci-dessus) :
  // le bouton « Mettre à jour » a déjà son propre `.pl-preview` dans le même
  // bloc d'actions — un sélecteur `.pl-preview` unique doit continuer à n'en
  // trouver qu'un (tests e2e existants, `test_workspace_cockpit_upgrade_
  // flow.py`).
  reviewPreview.className = 'pl-review-preview';
  reviewPreview.hidden = true;

  let pendingSlugs = [];
  function updateReviewButton(payload) {
    pendingSlugs = pendingProposalSlugs(payload);
    const hasWork = pendingSlugs.length > 0 || Boolean(checkpointPendingRunId);
    if (hasWork) {
      if (!reviewBtn.isConnected) actionsBlock.append(reviewBtn, reviewPreview);
    } else {
      reviewBtn.remove();
      reviewPreview.remove();
      reviewPreview.hidden = true;
    }
  }
  updateReviewButton(sheet.proposals);

  reviewBtn.addEventListener('click', async () => {
    const runId = checkpointPendingRunId || latestUpgradeRun?.runId || '';
    const toPaste = ['/grimoire-upgrade-review', runId, ...pendingSlugs].filter(Boolean).join(' ');
    const reviewCmd = `grimoire upgrade-flow review${slug ? ' # ' + slug : ''}`;
    ctx.dock.echo(reviewCmd);

    let copied = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(toPaste);
        copied = true;
      }
    } catch (_error) {
      copied = false;
    }

    reviewPreview.hidden = false;
    reviewPreview.replaceChildren();
    reviewPreview.append(text('div', null, copied
      ? 'Copié dans le presse-papiers :'
      : 'Presse-papiers indisponible — copiez ce texte à la main :'));
    const pasteCode = document.createElement('code');
    pasteCode.className = 'mono';
    pasteCode.textContent = toPaste;
    reviewPreview.append(pasteCode);
    reviewPreview.append(text('div', 'lbl', "Aucun IDE ouvrable déclaré pour ce projet — collez ce texte dans l'hôte de votre choix, ou lancez directement dans le projet :"));
    const cliCode = document.createElement('code');
    cliCode.className = 'mono';
    cliCode.textContent = 'grimoire upgrade-flow review';
    reviewPreview.append(cliCode);
  });

  if (ctx.host.kind === 'cockpit' && slug && slug !== ctx.host.project) {
    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'btn';
    openBtn.textContent = 'Ouvrir ce projet';
    // Naviguer via `ctx.goto` (une seule page, un seul montage de Piloter),
    // jamais `location.search = ...` (issue #506) : ce dernier rechargeait
    // le navigateur en entier, et le `?project=` qui en résultait retombait
    // sur la vue Flotte par défaut (`projectFromUrl`, voir le docstring de
    // `mount` plus bas) — le clic n'ouvrait jamais l'écran Projet qu'il
    // promettait, il fallait recliquer « Projet » à la main. `ctx.params.
    // openProject`, lu par `mount`, sélectionne directement ce projet au
    // niveau Projet, sans reconstruire toute la coque.
    openBtn.addEventListener('click', () => ctx.goto('piloter', { openProject: slug }));
    actionsBlock.append(openBtn);
  }

  ctx.inspector.append(actionsBlock);

  // ── Agents (#374) : table dans la fiche, détail éditable dans l'inspecteur ─
  //
  // Le clic sélectionne un agent et rend son détail dans l'inspecteur, sans
  // redessiner la fiche entière ni la refaire au serveur — seule une écriture
  // réussie re-fetch `agents()` pour tenir la table et l'inspecteur à jour
  // avec l'état réel du disque, jamais avec un optimisme local.
  let agentsPayload = sheet.agents;
  let selectedAgent = null;
  let agentsSection = null;

  const renderAgentBlock = () => {
    const previous = ctx.inspector.querySelector('[data-agent-inspector]');
    if (previous) previous.remove();
    if (!selectedAgent || !agentsPayload) return;
    const agent = agentsPayload.agents.find((a) => a.name === selectedAgent);
    if (!agent) { selectedAgent = null; return; }
    ctx.inspector.append(
      renderAgentInspector(ctx, agentsPayload, agent, {
        refresh: async (keepSelected) => {
          agentsPayload = await ctx.api.agents(slug).catch(() => agentsPayload);
          selectedAgent = keepSelected;
          const fresh = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
          agentsSection.replaceWith(fresh);
          agentsSection = fresh;
          renderAgentBlock();
        },
      }),
    );
  };

  const selectAgent = (agentName) => {
    selectedAgent = agentName === selectedAgent ? null : agentName;
    const fresh = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
    agentsSection.replaceWith(fresh);
    agentsSection = fresh;
    renderAgentBlock();
  };

  agentsSection = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
  wrap.append(agentsSection);

  // ── Propositions (#395) : section agents, sous la table ────────────────
  //
  // Accepter en crée un — l'agent nouvellement écrit doit apparaître dans la
  // table juste au-dessus sans recharger toute la fiche, donc son callback
  // re-fetch `agents()` en plus de `proposals()`.
  let proposalsPayload = sheet.proposals;
  let proposalsSection = null;

  const refreshProposals = async () => {
    const [freshProposals, freshAgents] = await Promise.all([
      ctx.api.proposals(slug).catch(() => proposalsPayload),
      ctx.api.agents(slug).catch(() => agentsPayload),
    ]);
    proposalsPayload = freshProposals;
    agentsPayload = freshAgents;
    updateReviewButton(proposalsPayload);
    const freshProposalsSection = renderProposalsSection(ctx, proposalsPayload, refreshProposals, slug);
    proposalsSection.replaceWith(freshProposalsSection);
    proposalsSection = freshProposalsSection;
    const freshAgentsSection = renderAgentsTable(ctx, agentsPayload, selectedAgent, selectAgent);
    agentsSection.replaceWith(freshAgentsSection);
    agentsSection = freshAgentsSection;
  };

  proposalsSection = renderProposalsSection(ctx, proposalsPayload, refreshProposals, slug);
  wrap.append(proposalsSection);
}

export async function mount(root, ctx) {
  injectStyles();
  const cockpit = ctx.host.kind === 'cockpit';
  // Flotte reste le point d'entrée du pilotage multi-projets — y compris
  // quand une navigation cockpit délibérée a un projet en `?project=` — SAUF
  // quand ce projet vient du lancement direct depuis son dossier (#351) :
  // `ctx.host.projectFromUrl` distingue les deux (voir api.js). Sans cette
  // exception, `cockpit serve` lancé dans un projet — seul serveur restant
  // depuis #356 — n'ouvrait plus jamais Piloter sur sa propre fiche.
  const directLaunch = ctx.host.project && !ctx.host.projectFromUrl;
  let level = cockpit && !directLaunch ? 'flotte' : 'projet';
  let selected = ctx.host.project || null;

  // `ctx.goto('piloter', { openProject: slug })` (bouton « Ouvrir ce
  // projet », issue #506) : une navigation EXPLICITE vers la fiche d'un
  // projet précis prime sur la Flotte par défaut, y compris pour un hôte non
  // home — c'est tout le sens du clic, contrairement à `?project=` dans
  // l'URL au chargement (`directLaunch` ci-dessus), qui reste une
  // navigation de flotte par défaut (#351).
  if (ctx.params && ctx.params.openProject) {
    selected = ctx.params.openProject;
    level = 'projet';
  }

  const zoomLevels = cockpit
    ? [{ id: 'flotte', label: 'Flotte' }, { id: 'projet', label: 'Projet' }]
    : [{ id: 'projet', label: 'Projet' }];

  const draw = async ({ forceFleet = false } = {}) => {
    root.replaceChildren();
    ctx.docbar.setBreadcrumb([ctx.host.project || 'flotte', 'Piloter', level === 'flotte' ? 'Flotte' : 'Projet']);
    ctx.docbar.setZoom(zoomLevels, level, (id) => { level = id; draw(); });

    if (level === 'flotte') {
      ctx.inspector.replaceChildren(document.createElement('div'));
      // `forceFleet` ne vient que du bouton « Rafraîchir la flotte » — un
      // simple retour sur ce niveau (zoom, sélection d'un autre projet puis
      // retour) sert le cache de `loadFleet` (issue #510 point 5).
      const rows = await loadFleet(ctx, { force: forceFleet });
      if (ctx.signal.aborted) return;
      renderFleet(
        root, ctx, rows,
        (slug) => { selected = slug; level = 'projet'; draw(); },
        () => draw({ forceFleet: true }),
      );
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
