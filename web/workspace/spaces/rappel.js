// Savoir › Rappel — le reçu de rappel en grand (P27, maquette « Savoir — reçu
// de rappel »).
//
// Deux lectures : `memory/recall` (les derniers reçus du projet, ou ceux d'une
// tâche quand on arrive de son inspecteur) et `memory/recall/replay` (rejouer
// un reçu, ou une requête libre). Le tableau dit, pour chaque souvenir servi,
// son rang, son rang dans chaque classement avant fusion (lexical,
// sémantique), son score et son état d'aujourd'hui ; « Rejouer et comparer »
// relance la même lecture et rend le différentiel.
//
// Refus tenus ici :
// - lecture seule : aucun geste d'écriture n'est offert ici (la correction
//   d'un souvenir passe par Recherche), rejouer n'écrit aucun reçu ;
// - pas de reçu : la phrase le dit, et la requête libre reste possible ;
// - « Jamais servis » (maquette) n'est pas mesuré : la ligne n'est pas rendue.
//
// API consommées : api.memoryRecall, api.memoryReplay, api.projects.

import { badge, failureText, spaceHead } from '../shell.js';
import {
  el, entryTitle, injectRecallStyles, MODE, plural, replayControl, SCOPE, scoreText, stateBadge, whenText,
} from './recall-receipt.js';

const STYLE_ID = 'rp-styles';
const STYLES = `
.rp-wrap { display: flex; flex-direction: column; gap: 14px; padding: 28px 32px; max-width: 1280px; min-width: 0; }
.rp-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.rp-bar label { display: flex; align-items: center; gap: 8px; color: var(--ink2); }
.rp-bar select, .rp-free input { height: var(--h-ctl); padding: 0 9px; border: 1px solid var(--line); border-radius: var(--r);
  background-color: var(--card); color: var(--ink); min-width: 0; }
.rp-bar select { max-width: 420px; }
.rp-free { display: flex; gap: 8px; flex: 1; min-width: 260px; }
.rp-free input { flex: 1; }
.rp-head { display: flex; flex-direction: column; gap: 4px; }
.rp-eyebrow { margin: 0; font-size: var(--t-min); font-weight: 500; color: var(--ink2); overflow-wrap: anywhere; }
.rp-head h2 { margin: 0; font-size: 17px; line-height: 22px; font-weight: 600; }
.rp-card { background: var(--card); border: 1px solid var(--line); border-radius: var(--r-l); box-shadow: var(--shadow); min-width: 0; }
.rp-table { overflow: hidden; }
.rp-row { display: grid; grid-template-columns: 48px minmax(0, 1.8fr) 72px 92px 80px 150px 88px; gap: 10px; align-items: center;
  min-height: 44px; padding: 6px 16px; border-top: 1px solid var(--line); }
.rp-table[data-lanes="false"] .rp-row { grid-template-columns: 48px minmax(0, 1fr) 80px 150px 88px; }
.rp-row.rp-th { min-height: 36px; border-top: 0; color: var(--ink2); font-size: var(--t-min); font-weight: 500; }
.rp-row[data-state="superseded"] { background: var(--bad-soft); }
.rp-rank { font-weight: 600; font-variant-numeric: tabular-nums; }
.rp-entry { display: flex; flex-direction: column; min-width: 0; }
.rp-title { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rp-title.rp-muted { color: var(--ink2); font-weight: 400; font-style: italic; }
.rp-sub { color: var(--ink2); font-size: var(--t-min); }
.rp-num { color: var(--ink2); font-variant-numeric: tabular-nums; }
.rp-state { display: flex; flex-wrap: wrap; gap: 4px; }
.rp-act { justify-self: end; }
.rp-replay { padding: 14px 16px; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px 16px; align-items: start; }
.rp-replay h2 { margin: 0; font-size: 15px; line-height: 20px; font-weight: 600; }
.rp-replay p { margin: 4px 0 0; color: var(--ink2); }
.rp-replay > .rc-replay { align-items: flex-end; }
.rp-replay > .rp-result { grid-column: 1 / -1; min-width: 0; }
.rp-none { margin: 0; padding: 14px 16px; color: var(--ink2); }
.rp-aside { display: flex; flex-direction: column; gap: 12px; padding: var(--sp-3); }
.rp-aside h3 { margin: 0; font-size: 15px; line-height: 20px; font-weight: 600; }
.rp-aside p { margin: 0; color: var(--ink2); font-size: var(--t-s); }
.rp-kv { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-top: 1px solid var(--line); }
.rp-kv span:first-child { color: var(--ink2); font-size: var(--t-min); font-weight: 500; }
.rp-kv .rp-bad { color: var(--bad); font-weight: 600; }
@container canvas (max-width: 760px) {
  .rp-wrap { padding: var(--sp-4); }
  .rp-row { grid-template-columns: 36px minmax(0, 1fr) auto; }
  .rp-row > .rp-num, .rp-row.rp-th > :nth-child(n+3) { display: none; }
  .rp-replay { grid-template-columns: minmax(0, 1fr); }
}
`;

function injectStyles() {
  injectRecallStyles();
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.append(style);
}

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

const TYPE = {
  decision: 'décision', incident: 'incident', convention: 'convention', learning: 'apprentissage', story: 'récit',
  code: 'code', docs: 'docs', task: 'tâche', projection: 'projection',
};

const laneRank = (entry, lane) => {
  const found = entry.lanes && entry.lanes[lane];
  return found && found.rank ? String(found.rank) : '—';
};

/** Le tableau des souvenirs servis : rang, souvenir, lexical, sémantique, score, état, ouvrir. */
function servedTable(ctx, receipt, slug) {
  const card = el('section', 'rp-card rp-table', null, { 'aria-label': 'Souvenirs servis' });
  card.dataset.role = 'served-table';
  const served = receipt.served || [];
  // Les colonnes Lexical et Sémantique n'existent que pour une lecture fusionnée :
  // sans classement par voie, deux colonnes de tirets ne diraient rien.
  const lanes = served.some((e) => e.lanes && Object.keys(e.lanes).length);
  card.dataset.lanes = String(lanes);
  const head = el('div', 'rp-row rp-th');
  const labels = lanes ? ['Rang', 'Souvenir servi', 'Lexical', 'Sémantique', 'Score', 'État aujourd’hui', '']
    : ['Rang', 'Souvenir servi', 'Score', 'État aujourd’hui', ''];
  for (const label of labels) head.append(el('span', null, label));
  card.append(head);
  if (!served.length) {
    card.append(el('p', 'rp-none', 'Cette lecture n’a servi aucun souvenir : la mémoire n’avait rien de proche.'));
    return card;
  }
  for (const entry of served) {
    const row = el('div', 'rp-row');
    row.dataset.id = entry.id;
    row.dataset.state = entry.state || '';
    const muted = entry.hidden || entry.found === false;
    const box = el('span', 'rp-entry');
    const title = el('span', 'rp-title' + (muted ? ' rp-muted' : ''), entryTitle(entry));
    title.title = entry.title || entry.id;
    const sub = [];
    if (entry.type && entry.type !== 'untyped') sub.push(TYPE[entry.type] || entry.type);
    sub.push(`portée ${SCOPE[entry.scope] || 'inconnue'}`);
    if (entry.freshnessLabel) sub.push(entry.freshnessLabel);
    box.append(title, el('span', 'rp-sub', sub.join(' · ')));
    const state = el('span', 'rp-state');
    state.append(stateBadge(entry.state));
    const action = el('span', 'rp-act');
    if (!muted) {
      const open = el('button', 'btn ghost', 'Ouvrir', { type: 'button', 'aria-label': `Ouvrir « ${entryTitle(entry)} » dans Recherche` });
      open.addEventListener('click', () => ctx.goto('memoire', { entry: entry.id, of: slug || '' }));
      action.append(open);
    }
    row.append(el('span', 'rp-rank', String(entry.rank ?? '—')), box);
    if (lanes) row.append(el('span', 'rp-num', laneRank(entry, 'lexical')), el('span', 'rp-num', laneRank(entry, 'vector')));
    row.append(el('span', 'rp-num', scoreText(entry.score)), state, action);
    card.append(row);
  }
  return card;
}

/** Ce que le reçu révèle, dans l'inspecteur : comptes mesurés, jamais estimés. */
function renderAside(ctx, receipt) {
  const served = receipt.served || [];
  const count = (pred) => served.filter(pred).length;
  const replaced = served.filter((e) => e.state === 'superseded');
  const retracted = count((e) => e.state === 'retracted');
  const hypotheses = count((e) => e.freshness === 'hypothese');
  const aside = el('div', 'rp-aside');
  aside.dataset.role = 'receipt-reading';
  aside.append(el('span', 'rp-eyebrow', 'Ce que le reçu révèle'));
  if (replaced.length) {
    const first = replaced[0];
    aside.append(el('h3', null, `Une entrée remplacée depuis a été servie en rang ${first.rank}`),
      el('p', null, 'L’agent l’a lue avant qu’elle soit marquée remplacée, ou à côté de sa remplaçante. Rejouer dit ce qu’il recevrait aujourd’hui.'));
  } else if (retracted) {
    aside.append(el('h3', null, 'Une entrée retirée depuis a été servie'),
      el('p', null, 'Elle ne serait plus servie aujourd’hui : rejouer le montre.'));
  } else {
    aside.append(el('h3', null, served.length ? 'Tout ce qui a été servi est encore actuel' : 'Rien n’a été servi'),
      el('p', null, served.length ? 'Aucun souvenir de ce reçu n’a été remplacé ni retiré depuis.' : 'La lecture n’a rien trouvé de proche.'));
  }
  const kv = (label, value, bad) => {
    const line = el('div', 'rp-kv');
    line.append(el('span', null, label), el('span', bad ? 'rp-bad' : null, value));
    return line;
  };
  aside.append(
    kv('Servis', plural(served.length, 'souvenir', 'souvenirs')),
    kv('Remplacés', String(replaced.length), replaced.length > 0),
    kv('Retirés', String(retracted), retracted > 0),
    kv('Hypothèses', String(hypotheses)),
  );
  if (receipt.taskRef) {
    const open = el('button', 'btn', 'Ouvrir la tâche et sa preuve', { type: 'button' });
    open.addEventListener('click', () => ctx.goto('travail', { task: receipt.taskRef }));
    aside.append(open);
  }
  ctx.inspector.replaceChildren(aside);
}

function eyebrow(receipt) {
  const bits = ['Reçu de rappel'];
  if (receipt.taskRef) bits.push(receipt.taskTitle ? `tâche « ${receipt.taskTitle} »` : `tâche ${receipt.taskRef}`);
  if (receipt.sessionId) bits.push(`session ${receipt.sessionId.slice(0, 8)}`);
  bits.push(whenText(receipt.ts));
  return bits.join(' · ');
}

function queryLine(receipt) {
  const line = el('span', 'rc-meta');
  line.dataset.role = 'receipt-meta';
  if (receipt.query) line.append('Requête envoyée : ', el('span', 'rc-query', `« ${receipt.query} »`));
  else line.append(receipt.queryChars ? `Requête non conservée (${receipt.queryChars} caractères)` : 'Lecture sans requête');
  line.append(` · ${plural((receipt.served || []).length, 'souvenir servi', 'souvenirs servis')} · ${MODE[receipt.mode] || receipt.mode}`);
  return line;
}

export async function mount(root, ctx) {
  injectStyles();
  ctx.docbar.setBreadcrumb([]);
  ctx.docbar.setViews([], null);
  ctx.inspector.replaceChildren();
  const wrap = el('div', 'rp-wrap');
  root.append(wrap);
  const params = ctx.params || {};
  const allScope = Boolean(ctx.scope && ctx.scope.all);
  const task = String(params.task || '');
  let slug = String(params.of || '');
  let projects = [];
  if (allScope) {
    const payload = await ctx.api.projects().catch(() => null);
    projects = ((payload && payload.projects) || []).filter((p) => p.exists !== false)
      .map((p) => ({ slug: p.slug, name: p.name || p.slug }));
    if (!projects.some((p) => p.slug === slug)) slug = projects.length ? projects[0].slug : '';
    if (ctx.signal.aborted) return;
  }

  const picker = () => {
    if (!allScope || !projects.length) return null;
    const label = el('label', null, 'Projet');
    const select = el('select', null, null, { 'aria-label': 'Projet lu' });
    for (const p of projects) {
      const option = el('option', null, p.name, { value: p.slug });
      option.selected = p.slug === slug;
      select.append(option);
    }
    select.addEventListener('change', () => { slug = select.value; ctx.setItem({ of: slug }); load(); });
    label.append(select);
    return label;
  };

  // La requête libre : premier passage = la liste, passage suivant = comparé à celle-là.
  const freeForm = (draw) => {
    const form = el('form', 'rp-free', null, { role: 'search', 'aria-label': 'Requête libre' });
    const input = el('input', null, null, { type: 'search', placeholder: 'Requête libre : ce que la mémoire servirait à un agent', 'aria-label': 'Requête libre' });
    input.dataset.role = 'free-query';
    const run = el('button', 'btn', 'Lancer', { type: 'submit' });
    form.append(input, run);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const q = input.value.trim();
      if (!q) { input.focus(); return; }
      run.disabled = true;
      try {
        const payload = await ctx.api.memoryReplay({ q, slug: slug || undefined });
        if (payload.reason) { draw(null, `Requête impossible : ${payload.reason}.`); return; }
        draw({
          key: '', mode: 'free', query: payload.query, ts: payload.ranAt, served: payload.results || [],
          replayable: true, free: true,
        });
      } catch (error) {
        draw(null, failureText(error));
      } finally {
        run.disabled = false;
      }
    });
    return form;
  };

  let token = 0;
  const load = async () => {
    const mine = ++token;
    const command = task ? `grimoire task recall ${task}` : 'grimoire task recall <tâche>';
    const head = spaceHead('Rappel', task ? `Reçus de la tâche ${task}` : 'Ce que la mémoire a servi aux agents', picker(), formula(command));
    wrap.replaceChildren(head, ...ctx.loading('Lecture des reçus…', 5, 'results'));
    let payload;
    try {
      payload = await ctx.api.memoryRecall(slug || undefined, task || undefined);
    } catch (error) {
      if (mine !== token || ctx.signal.aborted) return;
      wrap.replaceChildren(head, ctx.empty('Reçus indisponibles', failureText(error), null, { label: 'Réessayer', run: load },
        { art: 'i-empty-memory' }));
      return;
    }
    if (mine !== token || ctx.signal.aborted) return;
    const receipts = payload.receipts || [];
    const bar = el('div', 'rp-bar');
    const view = el('div', 'rp-wrap-inner');
    view.style.display = 'contents';

    const draw = (receipt, failure) => {
      if (failure) {
        view.replaceChildren(ctx.empty('Requête impossible', failure, null, undefined, { compact: true, art: 'i-empty-memory' }));
        return;
      }
      if (!receipt) {
        const sentence = payload.reason ? `Reçus illisibles : ${payload.reason}.`
          : !payload.logPresent ? 'Aucune lecture mémoire enregistrée : ce projet n’a encore aucun journal des lectures.'
            : task ? 'Aucune lecture mémoire enregistrée pour cette tâche.' : 'Aucune lecture mémoire enregistrée.';
        view.replaceChildren(ctx.empty('Aucun reçu de rappel', `${sentence} Une requête libre montre ce que la mémoire servirait aujourd’hui.`,
          null, undefined, { compact: true, art: 'i-empty-memory' }));
        ctx.inspector.replaceChildren();
        return;
      }
      const top = el('div', 'rp-head');
      top.dataset.role = 'receipt-head';
      top.append(
        el('p', 'rp-eyebrow', receipt.free ? `Requête libre · ${whenText(receipt.ts)}` : eyebrow(receipt)),
        el('h2', null, receipt.free ? 'Ce que la mémoire servirait maintenant' : 'Ce que l’agent a reçu avant de décider'),
        queryLine(receipt),
      );
      const parts = [top];
      if (payload.memoryReason && !receipt.free) parts.push(el('p', 'rc-why', `Entrées non décrites : ${payload.memoryReason}.`));
      parts.push(servedTable(ctx, receipt, slug));
      const card = el('section', 'rp-card rp-replay', null, { 'aria-labelledby': 'rp-h-replay' });
      card.dataset.role = 'replay-card';
      const text = el('div');
      text.append(el('h2', null, 'Rejouer maintenant', { id: 'rp-h-replay' }));
      if (receipt.replayable) {
        text.append(el('p', null, receipt.free
          ? 'Relance la même requête libre et la compare à ce passage : apparue, remplacée, déclassée, disparue.'
          : 'Même requête, même mode, mêmes filtres, sur la mémoire d’aujourd’hui : ce qui est apparu, a été remplacé, déclassé ou a disparu.'));
        const base = (receipt.served || []).map((e) => e.id);
        const control = replayControl(() => (receipt.free
          ? ctx.api.memoryReplay({ q: receipt.query, base, slug: slug || undefined })
          : ctx.api.memoryReplay({ receipt: receipt.key, task: receipt.taskRef || undefined, slug: slug || undefined })),
        { label: 'Rejouer et comparer', primary: true });
        const result = control.lastChild;
        control.removeChild(result);
        result.className = 'rp-result';
        card.append(text, control, result);
      } else {
        text.append(el('p', null, `Pas de rejeu : ${receipt.notReplayable || 'requête absente'}.`));
        text.lastChild.dataset.role = 'not-replayable';
        card.append(text);
      }
      parts.push(card);
      view.replaceChildren(...parts);
      renderAside(ctx, receipt);
    };

    if (receipts.length) {
      const label = el('label', null, 'Reçu');
      const select = el('select', null, null, { 'aria-label': 'Reçu affiché' });
      select.dataset.role = 'receipt-pick';
      receipts.forEach((receipt, index) => {
        const who = receipt.taskRef ? (receipt.taskTitle || receipt.taskRef) : 'hors tâche';
        select.append(el('option', null, `${whenText(receipt.ts)} · ${who} · ${plural((receipt.served || []).length, 'souvenir', 'souvenirs')}`,
          { value: String(index) }));
      });
      select.addEventListener('change', () => draw(receipts[Number(select.value)]));
      label.append(select);
      bar.append(label);
      if (payload.total > receipts.length) bar.append(badge(`${receipts.length} plus récents sur ${payload.total}`, ''));
    }
    bar.append(freeForm(draw));
    wrap.replaceChildren(head, bar, view);
    draw(receipts[0] || null);
    ctx.dock.echo(command);
  };

  await load();
}

