// Flotte › Données — la machine, qui partage quoi (P26, maquette « Flotte —
// données »).
//
// Une lecture : `data/machine` (workspace_machine.py), mesurée à chaque
// ouverture de la vue, jamais au premier rendu de la Flotte (piloter.js
// importe ce module au clic sur « Données »). Une carte par service mémoire
// du poste : état, version, conteneur et Compose propriétaire, politique de
// redémarrage, projets qui l'utilisent, partages. Puis la matrice « Qui
// partage quoi » (projets × services) et ce que voit le processus du cockpit
// (mot de passe Neo4j présent ou non, extras importables).
//
// Refus tenus ici :
// - aucun bouton ne démarre quoi que ce soit : chaque service porte un lien
//   vers sa console native et la commande à copier ; le vrai « Démarrer »
//   attend ADR-008 (P40) ;
// - « inconnu » se dit avec son motif (Docker injoignable, sonde expirée),
//   jamais comme un « arrêté » ;
// - le mot de passe Neo4j n'est qu'un « présent / absent ».
//
// API consommée : api.dataMachine.

import { actionStatus, badge, failureText, icon, setActionStatus, spaceHead } from '../shell.js';

const STYLE_ID = 'dn-styles';

const STYLES = `
.dn-wrap { display: flex; flex-direction: column; gap: 14px; }
.dn-docker { display: flex; align-items: flex-start; gap: 8px; margin: 0; padding: 10px 14px; border-radius: var(--r);
  background: var(--warn-soft); color: var(--ink); overflow-wrap: anywhere; }
.dn-docker .ico { flex: none; margin-top: 2px; color: var(--warn); }
.dn-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.dn-svc { background: var(--card); border: 1px solid var(--line); border-radius: var(--r-l); box-shadow: var(--shadow);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.dn-svc[data-state="absent"], .dn-svc[data-state="arrete"] { background: transparent; box-shadow: none; }
.dn-svc[data-state="absent"] { border-style: dashed; }
.dn-head { display: flex; align-items: center; gap: 8px; min-width: 0; flex-wrap: wrap; }
.dn-name { font-weight: 600; color: var(--ink); }
.dn-role { color: var(--ink2); font-size: 12px; }
.dn-mark { width: 10px; height: 10px; flex: none; border-radius: 5px; box-sizing: border-box; }
.dn-svc[data-state="en_ligne"] .dn-mark { background: var(--ok-mark); }
.dn-svc[data-state="demarrage"] .dn-mark { background: transparent; border: 2px solid var(--acc); }
.dn-svc[data-state="incomplet"] .dn-mark,
.dn-svc[data-state="emprunte"] .dn-mark { background: var(--warn-mark); }
.dn-svc[data-state="en_echec"] .dn-mark { background: var(--bad-mark); border-radius: 2px; }
.dn-svc[data-state="arrete"] .dn-mark { background: var(--ink3); border-radius: 2px; }
.dn-svc[data-state="absent"] .dn-mark { border: 1.5px dashed var(--ink3); }
.dn-svc[data-state="inconnu"] .dn-mark { border: 1.5px solid var(--ink3); }
.dn-svc > .badge { align-self: flex-start; }
.dn-reason { margin: 0; color: var(--ink2); font-size: 12.5px; overflow-wrap: anywhere; }
.dn-facts { margin: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 3px 10px; font-size: 12px; }
.dn-facts dt { color: var(--ink2); }
.dn-facts dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.dn-facts .mono { font-family: var(--mono); font-size: 11.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dn-shares { display: flex; flex-wrap: wrap; gap: 6px; }
.dn-console { display: inline-flex; align-items: center; gap: 6px; color: var(--acc); font-size: 12.5px; font-weight: 500;
  text-decoration: none; align-self: flex-start; }
.dn-console:hover { text-decoration: underline; }
.dn-cmd { display: flex; flex-direction: column; gap: 6px; padding-top: 8px; border-top: 1px solid var(--line); margin-top: auto; }
.dn-cmd-label { color: var(--ink2); font-size: 12px; }
.dn-cmd code { font-family: var(--mono); font-size: 11.5px; padding: 4px 8px; border-radius: var(--r); background: var(--hover);
  color: var(--ink); overflow-wrap: anywhere; }
.dn-cmd .btn { align-self: flex-start; }
.dn-card { background: var(--card); border: 1px solid var(--line); border-radius: var(--r-l); box-shadow: var(--shadow);
  overflow: hidden; min-width: 0; }
.dn-cardhead { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; padding: 14px 16px 10px; }
.dn-cardhead h2 { margin: 0; font-size: 15px; line-height: 20px; font-weight: 600; }
.dn-hint { margin-left: auto; color: var(--ink2); font-size: 12px; }
.dn-card .tbl-wrap { border: 0; border-radius: 0; box-shadow: none; }
.dn-cell { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; min-width: 0; }
.dn-pip { width: 8px; height: 8px; flex: none; border-radius: 2px; background: var(--acc); }
.dn-cell[data-tone="warn"] { color: var(--warn); }
.dn-cell[data-tone="warn"] .dn-pip { background: var(--warn-mark); }
.dn-cell[data-tone="bad"] { color: var(--bad); }
.dn-cell[data-tone="bad"] .dn-pip { background: var(--bad-mark); }
.dn-cell[data-tone="none"] { color: var(--ink2); }
.dn-cell[data-tone="none"] .dn-pip { background: var(--line); }
.dn-env { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 0 16px 14px; }
.dn-env-label { color: var(--ink2); font-size: 12.5px; }
@media (max-width: 760px) {
  .dn-hint { margin-left: 0; flex-basis: 100%; }
}
`;

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.append(style);
}

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** Ton du badge d'un état : le mot reste toujours écrit, la couleur le double. */
const STATE_TONE = {
  en_ligne: 'ok', demarrage: 'acc', incomplet: 'warn', emprunte: 'warn',
  en_echec: 'bad', arrete: '', absent: '', inconnu: '',
};

const COLUMNS = [
  ['weaviate', 'Weaviate'], ['neo4j', 'Neo4j'], ['qdrant', 'Qdrant'],
  ['redis', 'Redis'], ['ollama', 'Ollama'], ['embarque', 'Embarqué'],
];

function formula(command) {
  const button = el('button', 'formula', null, { type: 'button', title: 'Copier la commande' });
  button.append(el('b', null, '›'), command);
  button.addEventListener('click', () => {
    navigator.clipboard.writeText(command).then(
      () => { button.title = 'Commande copiée'; },
      () => { button.title = 'Copie refusée : recopie la commande'; },
    );
  });
  return button;
}

function fact(list, term, value, mono) {
  if (value == null || value === '') return;
  const dd = el('dd', mono ? 'mono' : null, value);
  dd.title = value;
  list.append(el('dt', null, term), dd);
}

function shareWord(share) {
  const n = share.projects.length;
  if (share.kind === 'collection') return `collection ${share.target} : ${n} projets`;
  if (share.kind === 'base') return `base ${share.target} : ${n} projets`;
  return share.collision ? `Redis : ${n} projets, même espace de noms` : `Redis base ${share.target} : ${n} projets`;
}

function commandBlock(service) {
  const box = el('div', 'dn-cmd');
  const status = actionStatus('');
  const copy = el('button', 'btn', null, { type: 'button', 'data-copy': service.id });
  copy.append(icon('copy'), 'Copier la commande');
  copy.addEventListener('click', () => {
    navigator.clipboard.writeText(service.command.text).then(
      () => setActionStatus(status, 'Commande copiée : à lancer toi-même dans un terminal. Le cockpit ne démarre rien.', 'ok'),
      () => setActionStatus(status, 'Copie refusée par le navigateur : sélectionne la commande à la main.', 'bad'),
    );
  });
  box.append(el('span', 'dn-cmd-label', service.command.label), el('code', null, service.command.text), copy, status);
  return box;
}

function serviceCard(service) {
  const card = el('article', 'dn-svc', null, { 'aria-labelledby': `dn-h-${service.id}` });
  card.dataset.service = service.id;
  card.dataset.state = service.state;
  const head = el('div', 'dn-head');
  const mark = el('span', 'dn-mark', null, { role: 'img', 'aria-label': service.stateLabel });
  mark.title = service.stateLabel;
  const title = [service.label, service.version].filter(Boolean).join(' ');
  head.append(mark, el('span', 'dn-name', title, { id: `dn-h-${service.id}` }), el('span', 'dn-role', service.role));
  const state = badge(service.stateLabel, STATE_TONE[service.state] ?? '', service.reason);
  state.dataset.stateBadge = service.state;
  card.append(head, state, el('p', 'dn-reason', service.reason));

  const facts = el('dl', 'dn-facts');
  if (service.host !== 'localhost') fact(facts, 'Adresse', `${service.host}:${service.port}`, true);
  const container = service.container;
  if (container) {
    fact(facts, 'Conteneur', `${container.name} · ${container.status || 'état inconnu'}`, true);
    if (container.compose) {
      const owner = service.owner && service.owner.name ? ` (${service.owner.name})` : '';
      fact(facts, 'Compose', `${container.compose.project}${owner}`);
    } else {
      fact(facts, 'Compose', 'aucun : conteneur lancé à la main');
    }
    fact(facts, 'Redémarrage', container.restartPolicy);
    const volumes = (container.volumes || []).map((v) => v.name).filter(Boolean);
    if (volumes.length) fact(facts, 'Volumes', volumes.join(', '), true);
  } else if (service.state !== 'absent') {
    fact(facts, 'Conteneur', 'aucun rattaché');
  }
  // L'extra ne se dit ici que s'il manque : la liste complète vit dans « Ce que voit le cockpit ».
  if (!service.extra.installed) fact(facts, 'Extra Python', `${service.extra.name} absent du cockpit`);
  const users = (service.consumers || []).map((c) => c.name);
  fact(facts, 'Utilisé par', users.length ? users.join(', ') : 'aucun projet du registre');
  card.append(facts);

  if ((service.shares || []).length) {
    const shares = el('div', 'dn-shares');
    for (const share of service.shares) {
      shares.append(badge(shareWord(share), 'warn', share.projects.map((p) => p.name).join(', ')));
    }
    card.append(shares);
  }

  const link = el('a', 'dn-console', null, { href: service.console.url, target: '_blank', rel: 'noopener noreferrer' });
  link.dataset.console = service.id;
  link.append(icon('external'), service.console.label);
  card.append(link, commandBlock(service));
  return card;
}

function cellFor(project, kind, sharedKeys, stateOf) {
  const cell = el('span', 'dn-cell');
  const pip = el('span', 'dn-pip', null, { 'aria-hidden': 'true' });
  const use = project.uses ? project.uses[kind] : null;
  let word = '—';
  let tone = 'none';
  if (project.reason && !project.backend) {
    word = 'illisible';
    tone = 'bad';
    cell.title = project.reason;
  } else if (use) {
    tone = '';
    if (kind === 'weaviate' || kind === 'qdrant' || kind === 'embarque') word = use.collection || 'utilisé';
    else if (kind === 'neo4j') word = `base ${use.database}`;
    else if (kind === 'redis') word = `base ${use.db} · ${use.namespace}`;
    else word = 'utilisé';
    if (sharedKeys.has(`${project.slug}|${kind}`)) {
      tone = 'warn';
      word += ' · partagé';
    }
    // L'état du service que ce projet utilise, quand il n'est pas « en ligne ».
    const service = stateOf.get(`${project.slug}|${kind}`);
    if (service && service.state !== 'en_ligne') {
      word += ` · ${service.stateLabel.toLowerCase()}`;
      if (service.state === 'en_echec') tone = 'bad';
      else if (service.state === 'emprunte' || service.state === 'incomplet') tone = 'warn';
    }
  }
  cell.dataset.tone = tone;
  cell.append(pip, el('span', null, word));
  return cell;
}

function matrixCard(payload) {
  const card = el('section', 'dn-card', null, { 'aria-labelledby': 'dn-h-matrix' });
  const head = el('div', 'dn-cardhead');
  head.append(
    el('h2', null, 'Qui partage quoi', { id: 'dn-h-matrix' }),
    el('span', 'dn-hint', 'lecture seule : ce que la config de chaque projet demande à chaque service'),
  );
  card.append(head);
  const kindOf = { collection: null, base: 'neo4j', redis: 'redis' };
  const sharedKeys = new Set();
  for (const share of payload.shares || []) {
    const kind = kindOf[share.kind] || share.service.split('@')[0];
    for (const p of share.projects) sharedKeys.add(`${p.slug}|${kind}`);
  }
  const stateOf = new Map();
  for (const service of payload.services || []) {
    for (const consumer of service.consumers || []) stateOf.set(`${consumer.slug}|${service.kind}`, service);
  }
  const projects = payload.projects || [];
  if (!projects.length) {
    card.append(el('p', 'dn-env-label', 'Aucun projet au registre.'));
    return card;
  }
  const table = el('table', 'tbl');
  const headRow = el('tr');
  headRow.append(el('th', null, 'Projet'), ...COLUMNS.map(([, label]) => el('th', null, label)));
  const thead = el('thead');
  thead.append(headRow);
  const tbody = el('tbody');
  for (const project of projects) {
    const tr = el('tr');
    tr.dataset.project = project.slug || project.path;
    const name = el('td', 'dn-name', project.name);
    name.title = project.path;
    tr.append(name);
    for (const [kind] of COLUMNS) {
      const td = el('td');
      td.append(cellFor(project, kind, sharedKeys, stateOf));
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  const wrap = el('div', 'tbl-wrap');
  wrap.append(table);
  card.append(wrap);
  return card;
}

function envCard(payload) {
  const card = el('section', 'dn-card', null, { 'aria-labelledby': 'dn-h-env' });
  const head = el('div', 'dn-cardhead');
  head.append(
    el('h2', null, 'Ce que voit le cockpit', { id: 'dn-h-env' }),
    el('span', 'dn-hint', 'le processus du cockpit, pas celui de tes sessions'),
  );
  const row = el('div', 'dn-env');
  for (const entry of payload.neo4jPasswordEnvs || [payload.neo4jPassword]) {
    const word = `${entry.env} : ${entry.present ? 'présent' : 'absent'}`;
    const b = badge(word, entry.present ? 'ok' : 'warn', 'Seule la présence est lue, jamais la valeur.');
    b.dataset.secret = entry.env;
    row.append(b);
  }
  row.append(el('span', 'dn-env-label', 'Extras importables :'));
  for (const extra of payload.extras || []) {
    row.append(badge(`${extra.extra} ${extra.installed ? 'oui' : 'non'}`, extra.installed ? 'ok' : '', extra.modules.join(' ou ')));
  }
  card.append(head, row);
  return card;
}

function subline(payload) {
  const services = payload.services || [];
  const counts = payload.summary ? payload.summary.byState : {};
  const bits = [plural(services.length, 'service', 'services')];
  for (const state of payload.states || []) {
    if (counts[state.id]) bits.push(`${counts[state.id]} ${state.label.toLowerCase()}`);
  }
  bits.push(payload.docker.reachable ? 'Docker joignable' : 'Docker injoignable');
  return bits.join(' · ');
}

/** Monte la vue machine dans `root`. `seg` = le segmenté Projets / Données de la Flotte. */
export async function renderMachine(root, ctx, { seg, decorate } = {}) {
  injectStyles();
  const wrap = el('div', 'pl-wrap pl-fleet dn-wrap');
  const refresh = el('button', 'btn', null, { type: 'button' });
  refresh.append(icon('refresh'), 'Relire la machine');
  const head = spaceHead('Flotte', 'Lecture des services de la machine…', formula('grimoire memory status'), refresh);
  if (decorate) decorate(head, seg);
  wrap.append(head, ...ctx.loading('Lecture de Docker et des services…', 3));
  root.replaceChildren(wrap);
  ctx.dock.echo('grimoire memory status');

  const paint = async () => {
    refresh.disabled = true;
    let payload;
    try {
      payload = await ctx.api.dataMachine();
    } catch (error) {
      if (ctx.signal.aborted) return;
      wrap.replaceChildren(head, actionStatus(`Lecture de la machine impossible. ${failureText(error)}`, 'bad'));
      refresh.disabled = false;
      return;
    }
    if (ctx.signal.aborted) return;
    head.querySelector('.space-sub').textContent = subline(payload);
    const nodes = [head];
    if (!payload.docker.reachable) {
      const note = el('p', 'dn-docker', null, { role: 'note' });
      note.append(icon('alert'), `${payload.docker.reason}. Les états qui dépendent des conteneurs restent « inconnu », jamais « arrêté ».`);
      nodes.push(note);
    }
    const grid = el('div', 'dn-grid');
    grid.append(...(payload.services || []).map(serviceCard));
    nodes.push(grid, matrixCard(payload), envCard(payload));
    wrap.replaceChildren(...nodes);
    refresh.disabled = false;
  };
  refresh.addEventListener('click', paint);
  await paint();
}
