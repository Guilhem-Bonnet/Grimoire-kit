// Panneau « Capacité » (P71) — les goulots d'étranglement de l'admission (K10).
//
// L'info reine est la FILE DES TÂCHES PRÊTES avec, pour chaque tâche différée,
// sa raison de report (« WIP atteint », « CI pleine », « hôte chargé »,
// « quota »…) : c'est la seule chose que ni la Flotte ni Sessions ne racontent.
// Elle passe donc en premier, regroupée par raison (logique des groupes de la
// maquette Plan.dc.html : « Lançables maintenant » puis un groupe par motif
// d'attente). Les réservoirs d'admission sont un bandeau compact au-dessus :
// une ligne par réservoir saturé ou inconnu, les réservoirs sains regroupés
// sur une seule ligne — jamais de gros cadrans.
//
// Ce panneau ne liste JAMAIS de sessions (la seule liste de sessions du
// cockpit est Travail › Sessions) : la file liste des tâches en attente, point.
//
// Tout vient de `GET /api/workspace/capacity` (grimoire/tools/
// workspace_capacity.py) : le même plan à blanc que `grimoire queue plan`,
// dérivé du ledger réel — aucun compteur côté client. Une sonde muette ou une
// capacité illisible s'affiche « inconnue » en ton NEUTRE (ink3) : une sonde
// muette n'est pas une panne, jamais warn/bad permanent, jamais un chiffre
// deviné (doctrine d'ancrage des affirmations).
//
// Composant réutilisable : `renderCapacityPanel(ctx, options)` rend un nœud et
// son `refresh()` — l'hôte actuel est la fiche projet de Piloter, mais une
// future vue « Plan » de Travail pourra l'adopter sans le dessouder. Le
// rafraîchissement suit le mécanisme « en direct » existant (`ctx.live`),
// jamais une boucle de sondage dédiée : c'est l'hôte qui appelle `refresh()`.

import { badge, icon, levelBars } from '../shell.js';

const STYLE_ID = 'cp-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .cp-panel { display: flex; flex-direction: column; gap: var(--sp-3); }
    .cp-panel h3 { font-size: var(--t-m); font-weight: 500; margin: 0; color: var(--ink); display: flex; align-items: center; gap: var(--sp-2); }
    .cp-head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2) var(--sp-4); font-size: var(--t-s); color: var(--ink2); }
    .cp-head b { color: var(--ink); font-weight: 600; }
    .cp-band { display: flex; flex-direction: column; gap: 4px; }
    .cp-res { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); font-size: var(--t-s); color: var(--ink2); padding: 4px 8px; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); }
    .cp-res[data-etat="plein"] { border-color: var(--warn); }
    .cp-res[data-etat="inconnu"], .cp-res[data-etat="non_declare"] { color: var(--ink3); }
    .cp-res-name { font-weight: 500; color: var(--ink); }
    .cp-res[data-etat="inconnu"] .cp-res-name, .cp-res[data-etat="non_declare"] .cp-res-name { color: var(--ink3); font-weight: 400; }
    .cp-meter { width: 72px; height: 6px; border-radius: 3px; background: var(--e2); overflow: hidden; flex: none; }
    .cp-meter i { display: block; height: 100%; background: var(--acc); }
    .cp-meter[data-etat="plein"] i { background: var(--warn); }
    .cp-sains { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); font-size: var(--t-s); color: var(--ink2); padding: 2px 0; }
    .cp-sains .cp-meter { width: 48px; }
    .cp-sain { display: inline-flex; align-items: center; gap: 6px; }
    .cp-file { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: var(--r-l); background: var(--card); box-shadow: var(--shadow); overflow: hidden; }
    .cp-group { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--sp-2); padding: 8px var(--sp-3) 4px; border-top: 1px solid var(--line); }
    .cp-group:first-child { border-top: 0; }
    .cp-group h4 { margin: 0; font-size: var(--t-min); font-weight: 600; color: var(--ink2); }
    .cp-group .cp-n { color: var(--rub); font-weight: 600; font-size: var(--t-min); }
    .cp-group .cp-why { color: var(--ink3); font-size: var(--t-min); overflow-wrap: anywhere; }
    .cp-row { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto auto; gap: var(--sp-2); align-items: center; padding: 4px var(--sp-3); font-size: var(--t-s); }
    .cp-rank { color: var(--ink3); font-family: var(--mono); font-size: var(--t-min); text-align: right; }
    .cp-title { display: flex; align-items: baseline; gap: var(--sp-2); min-width: 0; }
    .cp-title .cp-id { font-family: var(--mono); font-size: var(--t-min); color: var(--ink3); flex: none; }
    .cp-title .cp-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .cp-retry { color: var(--ink3); font-size: var(--t-min); white-space: nowrap; }
    .cp-empty, .cp-foot { color: var(--ink3); font-size: var(--t-s); margin: 0; }
    .cp-foot { padding: 6px var(--sp-3); border-top: 1px solid var(--line); }
    @container canvas (max-width: 900px) {
      .cp-row { grid-template-columns: 24px minmax(0, 1fr) auto; }
      .cp-row .cp-retry { display: none; }
    }
  `;
  document.head.append(style);
}

const PRIORITY_LABEL = { low: 'basse', medium: 'moyenne', high: 'haute', critical: 'critique' };
const PRIORITY_BARS = { low: 1, medium: 2, high: 3, critical: 3 };

function el(tag, className, textContent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent !== undefined) node.textContent = textContent;
  return node;
}

const fmtInt = (n) => (typeof n === 'number' ? n.toLocaleString('fr-FR') : 'inconnue');

/** Jauge accessible d'un réservoir : barre + `role="meter"`, seulement si la capacité est connue. */
function meter(r) {
  if (typeof r.utilise !== 'number' || typeof r.capacite !== 'number' || r.capacite <= 0) return null;
  const bar = el('span', 'cp-meter');
  bar.dataset.etat = r.etat;
  bar.setAttribute('role', 'meter');
  bar.setAttribute('aria-valuenow', String(r.utilise));
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', String(r.capacite));
  bar.setAttribute('aria-label', `${r.libelle} : ${r.utilise} sur ${r.capacite}`);
  const fill = document.createElement('i');
  fill.style.width = `${Math.min(100, Math.round((r.utilise / r.capacite) * 100))}%`;
  bar.append(fill);
  return bar;
}

/** Valeur courte d'un réservoir : « 1/3 », « 0.42/2 », « 2048 Mo », « inconnue ». */
function gaugeText(r) {
  if (r.etat === 'non_declare') return 'non déclaré'; // la lecture réelle, s'il y en a une, est dans le détail
  if (r.etat === 'inconnu') return 'inconnue';
  if (typeof r.utilise === 'number' && typeof r.capacite === 'number') return `${r.utilise}/${r.capacite}`;
  if (typeof r.utilise === 'number') return r.id === 'host.memoire' ? `${fmtInt(r.utilise)} Mo` : String(r.utilise);
  return 'inconnue';
}

/** Une ligne de réservoir à montrer seule : saturé, ou inconnu (ton neutre, jamais une panne). */
function reservoirLine(r) {
  const line = el('div', 'cp-res');
  line.dataset.etat = r.etat;
  line.dataset.reservoir = r.id;
  line.append(icon(r.etat === 'plein' ? 'gauge' : 'info'));
  line.append(el('span', 'cp-res-name', r.libelle));
  const m = meter(r);
  if (m) line.append(m);
  line.append(el('span', 'cp-res-val', gaugeText(r)));
  if (r.detail) line.append(el('span', 'lbl', r.detail));
  return line;
}

/** Les réservoirs sains, regroupés sur une seule ligne compacte. */
function healthyLine(reservoirs) {
  const line = el('div', 'cp-sains');
  line.append(el('span', 'lbl', 'Réservoirs sains :'));
  for (const r of reservoirs) {
    const item = el('span', 'cp-sain');
    item.dataset.reservoir = r.id;
    item.title = r.detail || r.libelle;
    item.append(el('span', null, r.libelle));
    const m = meter(r);
    if (m) item.append(m);
    item.append(el('span', 'lbl', gaugeText(r)));
    line.append(item);
  }
  return line;
}

function quotaChip(quota) {
  const chip = el('span', 'cp-quota');
  chip.dataset.mode = quota.mode;
  if (quota.mode === 'illimite') {
    chip.textContent = 'quota : illimité';
    if (typeof quota.consomme === 'number') chip.textContent += ` (consommé ≥ ${fmtInt(quota.consomme)})`;
  } else if (quota.mode === 'fini') {
    const used = quota.consomme === null ? 'inconnue' : `≥ ${fmtInt(quota.consomme)}`;
    chip.textContent = `quota : ${used} / ${fmtInt(quota.plafond)} tokens aujourd'hui`;
  } else {
    chip.textContent = 'quota : inconnu';
  }
  chip.title = quota.detail || '';
  return chip;
}

function priorityCell(ligne) {
  const wrap = el('span', 'cp-prio');
  const bars = PRIORITY_BARS[ligne.priorite_effective]
    ? levelBars(
        PRIORITY_BARS[ligne.priorite_effective],
        `priorité ${PRIORITY_LABEL[ligne.priorite_effective] || ligne.priorite_effective}`
        + (ligne.priorite !== ligne.priorite_effective ? ` (vieillie depuis ${PRIORITY_LABEL[ligne.priorite] || ligne.priorite})` : ''),
        ligne.priorite_effective === 'critical' ? 'bad' : '',
      )
    : el('span', 'lbl', ligne.priorite_effective);
  wrap.append(bars);
  return wrap;
}

function queueRow(ligne, famine) {
  const row = el('div', 'cp-row');
  row.dataset.task = ligne.task_id;
  row.dataset.verdict = ligne.verdict;
  if (ligne.verdict === 'defer') row.title = ligne.detail || ligne.raison_libelle || '';
  row.append(el('span', 'cp-rank', String(ligne.rang)));
  const title = el('span', 'cp-title');
  title.append(el('span', 'cp-id', ligne.task_id));
  title.append(el('span', 'cp-t', ligne.titre));
  if (famine.has(ligne.task_id)) title.append(badge('en famine', 'warn', 'différée depuis plus de 24 h'));
  row.append(title);
  row.append(priorityCell(ligne));
  const retry = el('span', 'cp-retry');
  if (ligne.retry_at) {
    const when = new Date(ligne.retry_at);
    retry.textContent = Number.isNaN(when.getTime())
      ? `réessai : ${ligne.retry_at}`
      : `réessai ${when.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
  }
  row.append(retry);
  return row;
}

/** La file groupée par verdict puis par raison, les lançables d'abord (maquette Plan). */
function queueGroups(file, famine) {
  const groups = [];
  const admit = file.filter((l) => l.verdict === 'admit');
  if (admit.length) groups.push({ key: 'admit', title: 'Lançables maintenant', why: '', rows: admit });
  const byReason = new Map();
  for (const ligne of file) {
    if (ligne.verdict !== 'defer') continue;
    const key = ligne.raison || 'unknown';
    if (!byReason.has(key)) {
      byReason.set(key, { key: `defer-${key}`, title: ligne.raison_libelle || key, why: ligne.detail || '', rows: [] });
    }
    byReason.get(key).rows.push(ligne);
  }
  groups.push(...byReason.values());
  const out = document.createDocumentFragment();
  for (const group of groups) {
    const head = el('div', 'cp-group');
    head.dataset.group = group.key;
    head.append(el('h4', null, group.title), el('span', 'cp-n', String(group.rows.length)));
    if (group.why) head.append(el('span', 'cp-why', group.why));
    out.append(head);
    for (const ligne of group.rows) out.append(queueRow(ligne, famine));
  }
  return out;
}

function renderInto(node, data) {
  node.replaceChildren();
  const h = el('h3');
  h.append(icon('gauge'), document.createTextNode('Capacité'));
  node.append(h);

  if (!data) {
    node.append(el('p', 'cp-empty', 'Capacité illisible — la lecture a échoué, rien n’est deviné.'));
    return;
  }

  // ── Tête : « N workers actifs sur M » + quota ──────────────────────────────
  const head = el('div', 'cp-head');
  const wipText = el('span', 'cp-wip');
  const actifs = data.wip && typeof data.wip.actifs === 'number' ? data.wip.actifs : null;
  const limite = data.wip ? data.wip.limite : null;
  const strong = el('b', null, actifs === null ? 'inconnue' : fmtInt(actifs));
  wipText.append(strong, document.createTextNode(
    ` worker${actifs !== null && actifs > 1 ? 's' : ''} actif${actifs !== null && actifs > 1 ? 's' : ''} sur `
    + (data.wip && data.wip.etat === 'inconnu' ? 'une limite inconnue' : limite === null ? 'WIP sans limite' : fmtInt(limite)),
  ));
  if (data.wip && data.wip.detail) wipText.title = data.wip.detail;
  head.append(wipText, quotaChip(data.quota || { mode: 'inconnu' }));
  for (const label of data.pause || []) head.append(badge(`pause : ${label}`, 'warn'));
  node.append(head);

  // ── Bandeau des réservoirs : saturés et inconnus en lignes, sains regroupés ─
  const band = el('div', 'cp-band');
  const reservoirs = data.reservoirs || [];
  const loud = reservoirs.filter((r) => r.etat !== 'ok');
  const sains = reservoirs.filter((r) => r.etat === 'ok');
  for (const r of loud) band.append(reservoirLine(r));
  if (sains.length) band.append(healthyLine(sains));
  if (reservoirs.length) node.append(band);

  // ── La file, info reine : l'ordre réel d'admission, groupé par raison ──────
  if (!data.ledger) {
    node.append(el('p', 'cp-empty', 'Aucun ledger de mission ici — la file d’admission n’existe pas.'));
    return;
  }
  const file = data.file || [];
  if (!file.length) {
    node.append(el('p', 'cp-empty', 'Aucune tâche prête : la file est vide.'));
  } else {
    const list = el('div', 'cp-file');
    list.setAttribute('role', 'region');
    list.setAttribute('aria-label', 'File d’admission des tâches prêtes');
    list.append(queueGroups(file, new Set(data.famine || [])));
    const exclues = data.exclues || [];
    if (exclues.length) {
      const foot = el('p', 'cp-foot', `Hors file : ${exclues.length} tâche(s) — ${exclues.map((e) => `${e.task_id} (${e.raison})`).slice(0, 3).join(' · ')}`);
      foot.title = exclues.map((e) => `${e.task_id} — ${e.raison}`).join('\n');
      list.append(foot);
    }
    node.append(list);
  }
}

/**
 * Le panneau Capacité : `{ node, refresh }`. L'hôte insère `node` où il veut
 * et branche `refresh` sur son propre mécanisme « en direct » (`ctx.live`) —
 * le panneau ne sonde jamais de lui-même. `project` cible un AUTRE projet que
 * celui résolu par l'hôte (même convention que `ctx.api.sheet`).
 */
export function renderCapacityPanel(ctx, { project = null, initial = null } = {}) {
  injectStyles();
  const node = el('section', 'pl-section cp-panel');
  node.dataset.panel = 'capacite';
  let generation = 0;
  const refresh = async () => {
    const mine = ++generation;
    const data = await ctx.api.capacity(project).catch(() => null);
    if (ctx.signal.aborted || mine !== generation) return;
    renderInto(node, data);
  };
  // `initial` vient de la fiche agrégée (`sheet.capacity`, #548 : un seul
  // aller-retour au premier rendu) ; sans elle — ou cassée — on va chercher.
  if (initial && !initial.error && Array.isArray(initial.reservoirs)) {
    renderInto(node, initial);
  } else {
    node.append(el('p', 'cp-empty', 'Chargement de la capacité…'));
    refresh();
  }
  return { node, refresh };
}
