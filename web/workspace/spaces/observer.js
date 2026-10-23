// Espace Observer — LOT 4.
//
// Remplace `observability.html`. Cible : le RUNTIME seulement — six KPI, coût
// par modèle, latence, spans lents, traces par agent ; activité, RTK et bench
// passent en onglets secondaires. Inspecteur : trace ou span.
//
// Corrections dues par la revue §4.4 : un projet sans trace obtient UN bloc
// vide qui dit d'où viendra la donnée, jamais un mur de zéros ni une erreur
// JS (le défaut mesuré : `const gs = OBS.graph_stats` sur une réponse absente).
// Ce module n'accède jamais à un champ imbriqué sans un repli — chaque lecture
// passe par un opérateur optionnel ou un test de présence explicite, pour
// qu'une donnée manquante rende un état vide et jamais une exception.
// Les séries de coût utilisent --s1/--s2/--s3 (trois catégories au plus, le
// surplus regroupé) ; la latence, une seule teinte neutre — ce n'est qu'une
// seule série (la latence) vue à trois percentiles, pas trois catégories.
//
// API consommées : api.otel(), api.costModel(), api.eventsLog(), api.stigmergy().
//
// Drill-down timeline (#139) : quand un span porte `grimoire.task_id`,
// l'inspecteur de span propose un bouton vers la timeline unifiée de cette
// tâche dans l'espace Exécuter (`ctx.goto('executer', { task, view:
// 'timeline' })`) — pure navigation, aucune lecture supplémentaire ici.

import { kpiRow, stateIcon } from '../shell.js';

const STYLE_ID = 'ob-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ob-wrap { padding: var(--sp-4) var(--sp-5); display: flex; flex-direction: column; gap: var(--sp-5); }
    .ob-panels { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-4); }
    @media (max-width: 900px) { .ob-panels { grid-template-columns: 1fr; } }
    .ob-panel { border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); padding: var(--sp-3); }
    .ob-panel h3 { font-size: var(--t-m); font-weight: 500; margin: 0 0 var(--sp-3); }
    .ob-bar-row { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: 6px; font-size: var(--t-min); }
    .ob-bar-lbl { width: 110px; flex: none; color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ob-bar-track { flex-grow: 1; background: var(--e2); border-radius: var(--r); height: 14px; overflow: hidden; }
    .ob-bar-fill { height: 100%; border-radius: var(--r); }
    .ob-bar-val { width: 72px; flex: none; text-align: right; font-family: var(--mono); color: var(--ink); }
    .ob-legend { display: flex; gap: var(--sp-3); margin-top: 6px; }
    .ob-legend-item { display: flex; align-items: center; gap: 6px; font-size: var(--t-min); color: var(--ink3); }
    .ob-legend-dot { width: 8px; height: 8px; border-radius: 999px; }
    .ob-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); }
    .ob-runs { margin-top: var(--sp-4); padding: var(--sp-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); }
    .ob-runs h4 { font-size: var(--t-min); text-transform: none; color: var(--ink3); margin: 0 0 6px; font-weight: 500; }
  `;
  document.head.append(style);
}

const dot = (cls) => stateIcon(cls);
function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}
function row(...children) { const d = document.createElement('div'); d.className = 'row'; d.append(...children); return d; }

const SERIES = ['--s1', '--s2', '--s3'];

function attr(span, key) {
  return span && span.attributes ? span.attributes[key] : undefined;
}

function spanDurationMs(span) {
  const start = span?.startTime ? Date.parse(span.startTime) : NaN;
  const end = span?.endTime ? Date.parse(span.endTime) : NaN;
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  const ms = end - start;
  return ms >= 0 ? ms : null;
}

// Même règle que `normalize_model_id` (src/grimoire/tools/cost_model.py) :
// `claude-haiku-4.5-20251001[1m]` → `haiku-4-5`, version exacte, sans famille.
export function normalizeModelId(model) {
  return String(model).trim().toLowerCase().split('[')[0].replaceAll('.', '-')
    .replace(/^claude-/, '').replace(/-\d{8}$/, '');
}

// Appariement exact sur l'id normalisé : une version absente de la table n'a
// pas de tarif (null = coût inconnu), jamais le tarif d'une famille voisine.
export function rateFor(modelRates, model) {
  if (!model || !modelRates) return null;
  const key = normalizeModelId(model);
  return Object.hasOwn(modelRates, key) ? modelRates[key] : null;
}

export function spanCostUsd(span, modelRates) {
  const model = attr(span, 'gen_ai.request.model');
  const rate = rateFor(modelRates, model);
  const tokensIn = Number(attr(span, 'gen_ai.usage.input_tokens')) || 0;
  const tokensOut = Number(attr(span, 'gen_ai.usage.output_tokens')) || 0;
  if (!rate) return null;
  return (tokensIn * rate.in + tokensOut * rate.out) / 1_000_000;
}

function percentile(sorted, p) {
  if (!sorted.length) return null;
  const index = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[index];
}

function costPanel(spans, modelRates) {
  const panel = document.createElement('div');
  panel.className = 'ob-panel';
  const h3 = text('h3', null, 'Coût par modèle');
  h3.dataset.term = 'cout';
  panel.append(h3);

  const byModel = new Map();
  let uncosted = 0;
  for (const span of spans) {
    const model = attr(span, 'gen_ai.request.model');
    if (!model) continue;
    const cost = spanCostUsd(span, modelRates);
    if (cost == null) { uncosted += 1; continue; }
    byModel.set(model, (byModel.get(model) || 0) + cost);
  }
  const ranked = [...byModel.entries()].sort((a, b) => b[1] - a[1]);
  if (!ranked.length) {
    panel.append(text('p', 'lbl', uncosted ? `${uncosted} span(s) sans modèle facturable connu.` : 'Aucun span de modèle dans ce journal.'));
    return panel;
  }
  const top = ranked.slice(0, 3);
  const rest = ranked.slice(3);
  const restTotal = rest.reduce((sum, [, v]) => sum + v, 0);
  const bars = rest.length ? [...top, [`autres (${rest.length})`, restTotal]] : top;
  const max = Math.max(...bars.map(([, v]) => v)) || 1;
  for (const [index, [model, cost]] of bars.entries()) {
    const bar = document.createElement('div');
    bar.className = 'ob-bar-row';
    bar.append(text('span', 'ob-bar-lbl', model));
    const track = document.createElement('div');
    track.className = 'ob-bar-track';
    const fill = document.createElement('div');
    fill.className = 'ob-bar-fill';
    fill.style.width = Math.max(4, Math.round((cost / max) * 100)) + '%';
    fill.style.background = index < 3 ? `var(${SERIES[index]})` : 'var(--ink3)';
    track.append(fill);
    bar.append(track, text('span', 'ob-bar-val mono', '$' + cost.toFixed(4)));
    panel.append(bar);
  }
  if (uncosted) panel.append(text('p', 'lbl', `${uncosted} span(s) sans tarif listé pour leur version : hors du total.`));
  return panel;
}

function latencyPanel(spans) {
  const panel = document.createElement('div');
  panel.className = 'ob-panel';
  panel.append(text('h3', null, 'Latence'));
  const durations = spans.map(spanDurationMs).filter((d) => d != null).sort((a, b) => a - b);
  if (!durations.length) {
    panel.append(text('p', 'lbl', 'Aucun span daté des deux bouts : la latence ne peut pas être calculée.'));
    return panel;
  }
  const rows = [['p50', percentile(durations, 50)], ['p95', percentile(durations, 95)], ['p99', percentile(durations, 99)]];
  const max = Math.max(...rows.map(([, v]) => v)) || 1;
  for (const [label, value] of rows) {
    const bar = document.createElement('div');
    bar.className = 'ob-bar-row';
    bar.append(text('span', 'ob-bar-lbl', label));
    const track = document.createElement('div');
    track.className = 'ob-bar-track';
    const fill = document.createElement('div');
    fill.className = 'ob-bar-fill';
    fill.style.width = Math.max(4, Math.round((value / max) * 100)) + '%';
    fill.style.background = 'var(--ink3)';
    track.append(fill);
    bar.append(track, text('span', 'ob-bar-val mono', Math.round(value) + ' ms'));
    panel.append(bar);
  }
  return panel;
}

function slowSpansPanel(spans, ctx, onSelect) {
  const panel = document.createElement('div');
  panel.className = 'ob-panel';
  panel.append(text('h3', null, 'Spans lents'));
  const withDuration = spans
    .map((span) => ({ span, duration: spanDurationMs(span) }))
    .filter((e) => e.duration != null)
    .sort((a, b) => b.duration - a.duration)
    .slice(0, 8);
  if (!withDuration.length) {
    panel.append(text('p', 'lbl', 'Aucun span daté des deux bouts.'));
    return panel;
  }
  const table = document.createElement('table');
  table.className = 'tbl tbl-pick';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Span', 'Agent', 'Durée', 'Issue']) headRow.append(text('th', null, label));
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  for (const { span, duration } of withDuration) {
    const tr = document.createElement('tr');
    tr.addEventListener('click', () => onSelect(span));
    tr.append(text('td', null, span.name || '—'));
    tr.append(text('td', 'lbl', attr(span, 'gen_ai.agent.name') || attr(span, 'grimoire.source') || '—'));
    tr.append(text('td', 'mono', Math.round(duration) + ' ms'));
    const issueCell = document.createElement('td');
    issueCell.append(row(dot(span.status?.code === 'ERROR' ? 'bad' : 'ok'), text('span', null, span.status?.code === 'ERROR' ? 'erreur' : 'ok')));
    tr.append(issueCell);
    tbody.append(tr);
  }
  table.append(tbody);
  panel.append(table);
  return panel;
}

function agentsPanel(spans, onSelect) {
  const panel = document.createElement('div');
  panel.className = 'ob-panel';
  const h3 = text('h3', null, 'Traces par agent');
  h3.dataset.term = 'agent';
  panel.append(h3);
  const byAgent = new Map();
  for (const span of spans) {
    const agent = attr(span, 'gen_ai.agent.name') || 'inconnu';
    if (!byAgent.has(agent)) byAgent.set(agent, []);
    byAgent.get(agent).push(span);
  }
  const ranked = [...byAgent.entries()].sort((a, b) => b[1].length - a[1].length);
  if (!ranked.length) {
    panel.append(text('p', 'lbl', "Aucun span ne porte d'agent."));
    return panel;
  }
  for (const [agent, list] of ranked.slice(0, 10)) {
    const line = document.createElement('div');
    line.className = 'ob-bar-row';
    line.style.cursor = 'pointer';
    line.addEventListener('click', () => onSelect(list[0]));
    line.append(text('span', 'ob-bar-lbl', agent), text('span', 'ob-bar-val mono', String(list.length)));
    panel.append(line);
  }
  return panel;
}

function renderSpanInspector(ctx, span) {
  ctx.inspector.replaceChildren();
  if (!span) {
    ctx.inspector.append(text('p', 'lbl', 'Sélectionnez un span pour voir son détail.'));
    return;
  }
  ctx.inspector.append(text('h3', null, span.name || 'Span'));
  const rows = [
    ['gen_ai.system', attr(span, 'gen_ai.system')],
    ['gen_ai.request.model', attr(span, 'gen_ai.request.model')],
    ['gen_ai.agent.name', attr(span, 'gen_ai.agent.name')],
    ['tokens entrée', attr(span, 'gen_ai.usage.input_tokens')],
    ['tokens sortie', attr(span, 'gen_ai.usage.output_tokens')],
    ['source', attr(span, 'grimoire.source')],
    ['traceId', span.traceId],
  ];
  for (const [label, value] of rows) {
    if (value == null) continue;
    const line = document.createElement('div');
    line.className = 'row';
    line.style.justifyContent = 'space-between';
    line.style.padding = '4px 0';
    line.append(text('span', 'lbl', label), text('span', 'mono', String(value)));
    ctx.inspector.append(line);
  }
  if (span.status?.code === 'ERROR') {
    ctx.inspector.append(text('p', null, 'Erreur : ' + span.status.message));
  }

  // Drill-down (#139) : un span qui porte `grimoire.task_id` (export OTel du
  // TraceLedger, #322) renvoie vers la timeline unifiée de cette tâche dans
  // l'espace Exécuter — pas de reconstruction ici, juste la navigation vers
  // la même lecture que fait déjà `api.taskTrace(id)`. Les spans de la pile
  // events.jsonl (`grimoire.source` renseigné) n'ont aujourd'hui aucun moyen
  // de porter cet identifiant : le bouton n'apparaît que quand la donnée
  // existe réellement, jamais devinée.
  const taskId = attr(span, 'grimoire.task_id');
  if (taskId) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.style.marginTop = '8px';
    btn.textContent = `Voir la timeline de ${taskId}`;
    btn.addEventListener('click', () => ctx.goto('executer', { task: taskId, view: 'timeline' }));
    ctx.inspector.append(btn);
  }
}

// Statut kernel d'un run (`flow_runs._attach_live_status`), en clair.
const RUN_STATUS_WORD = {
  checkpointed: 'checkpoint en attente', running: 'en cours', completed: 'terminé',
  failed: 'en échec', aborted: 'abandonné',
};

function runsBlockNode(runs, runsTotal) {
  const runsBlock = document.createElement('div');
  runsBlock.className = 'ob-runs';
  runsBlock.append(text('h4', null, `Runs de flow (hors TraceLedger) · ${runsTotal}`));
  for (const run of runs.slice(0, 10)) {
    const line = document.createElement('div');
    line.className = 'row';
    line.style.justifyContent = 'space-between';
    const status = run.status ? (RUN_STATUS_WORD[run.status] || run.status) : 'statut inconnu';
    line.append(
      text('span', 'mono', run.runId),
      text('span', 'lbl', `${run.blueprintId || '?'} · ${status} · ${run.createdAt || 'date inconnue'}`),
    );
    runsBlock.append(line);
  }
  return runsBlock;
}

function renderRuntime(root, ctx, { spans, modelRates, runs, runsTotal, streams, onActivite }) {
  if (!spans.length) {
    // Aucun span OTel n'est pas « rien n'a tourné » : le ledger (claims,
    // transitions, refus de gate), les hooks et les runs de flow écrivent
    // ailleurs que dans le TraceLedger. « Aucune trace » n'est dit que si
    // aucune de ces sources ne porte un seul événement pour ce projet.
    if (runs.length || streams.length) {
      const wrap = document.createElement('div');
      wrap.className = 'ob-wrap';
      const panel = document.createElement('div');
      panel.className = 'ob-panel';
      panel.dataset.role = 'activity-without-spans';
      panel.append(text('h3', null, 'Activité enregistrée, aucun span OTel'));
      panel.append(text('p', 'lbl', "Aucun appel de modèle ou d'outil instrumenté (span OTel) pour l'instant. "
        + 'Ce projet porte pourtant des événements :'));
      for (const [name, entries] of streams) {
        panel.append(text('div', null, `${name} : ${entries.length} évènement(s) lu(s)`));
      }
      if (streams.length && onActivite) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn';
        btn.style.marginTop = '8px';
        btn.textContent = "Voir l'activité";
        btn.addEventListener('click', onActivite);
        panel.append(btn);
      }
      wrap.append(panel);
      if (runs.length) wrap.append(runsBlockNode(runs, runsTotal));
      root.append(wrap);
      ctx.inspector.replaceChildren(text('p', 'lbl',
        `${streams.length} flux d'événements, ${runsTotal} run(s) de flow, aucun span OTel.`));
      ctx.dock.log('traces', ...streams.map(([name, entries]) => `${name} : ${entries.length} évènement(s)`),
        ...(runs.length ? [`${runsTotal} run(s) de flow enregistré(s)`] : []));
      // `grimoire upgrade-flow status` n'existe pas (restes #510/#513) —
      // `grimoire flow status <run_id>` (cmd_flow.py) est la vraie commande.
      ctx.dock.echo(runs.length ? `grimoire flow status ${runs[0].runId}` : 'grimoire task trace <id>');
      return;
    }
    root.append(ctx.empty(
      'Observer',
      "Aucune trace : ce projet n'a pas encore exécuté de workflow instrumenté. "
      + 'La donnée viendra de _grimoire-runtime-output/hook-runtime/events.jsonl, '
      + 'task-flow/events.jsonl et ledger/events.jsonl dès qu\'un blueprint ou une tâche aura tourné — '
      + 'lancez-en un puis ouvrez cet espace à nouveau.',
      'grimoire task trace <id>',
    ));
    ctx.inspector.replaceChildren(text('p', 'lbl', 'Aucun span à inspecter.'));
    ctx.dock.log('traces', 'Aucune trace dans le TraceLedger, le ledger ni les hooks de ce projet.');
    ctx.dock.echo('grimoire task trace');
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'ob-wrap';

  const errorCount = spans.filter((s) => s.status?.code === 'ERROR').length;
  const agents = new Set(spans.map((s) => attr(s, 'gen_ai.agent.name')).filter(Boolean));
  const models = new Set(spans.map((s) => attr(s, 'gen_ai.request.model')).filter(Boolean));
  const totalCost = spans.reduce((sum, s) => sum + (spanCostUsd(s, modelRates) || 0), 0);
  // Un span au modèle sans tarif listé n'est pas gratuit : il est compté à part.
  const unpriced = spans.filter((s) => attr(s, 'gen_ai.request.model') && spanCostUsd(s, modelRates) == null).length;
  const durations = spans.map(spanDurationMs).filter((d) => d != null);
  const avgLatency = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

  wrap.append(kpiRow([
    { value: String(spans.length), label: 'spans', icon: 'network' },
    { value: String(agents.size), label: 'agents', icon: 'team' },
    { value: String(models.size), label: 'modèles', icon: 'cognition' },
    { value: '$' + totalCost.toFixed(4), label: unpriced ? `coût estimé (${unpriced} span(s) sans tarif)` : 'coût estimé', icon: 'chart' },
    { value: avgLatency == null ? '—' : Math.round(avgLatency) + ' ms', label: 'latence moyenne', icon: 'bolt' },
    { value: `${errorCount}/${spans.length}`, label: 'erreurs', icon: errorCount ? 'alert' : 'check', tone: errorCount ? 'bad' : 'ok' },
  ]));

  const panels = document.createElement('div');
  panels.className = 'ob-panels';
  const onSelect = (span) => renderSpanInspector(ctx, span);
  panels.append(costPanel(spans, modelRates));
  panels.append(latencyPanel(spans));
  panels.append(slowSpansPanel(spans, ctx, onSelect));
  panels.append(agentsPanel(spans, onSelect));
  wrap.append(panels);

  root.append(wrap);
  renderSpanInspector(ctx, null);
  ctx.dock.log('traces', ...spans.slice(-50).map((s) => `${s.startTime || '—'} · ${s.name}`));
  ctx.dock.echo('grimoire task trace');
}

// Activité : seul onglet secondaire avec une route serveur (`api.eventsLog()`
// → /api/events/log, moteur `blueprint_telemetry.read_events`) — un flux par
// fichier `events.jsonl` sous _grimoire-runtime-output/. Contenu réel, même
// minimal : le compte par flux et les dernières lignes brutes, jamais un
// onglet muet.
function renderActivite(root, ctx, { events }) {
  const streams = Object.entries(events || {}).filter(([, entries]) => Array.isArray(entries) && entries.length);
  if (!streams.length) {
    root.append(ctx.empty(
      'Observer — Activité',
      "Aucun flux d'événements : ni hook-runtime/events.jsonl ni task-flow/events.jsonl "
      + "n'ont encore de ligne pour ce projet.",
      'grimoire task trace',
    ));
    ctx.inspector.replaceChildren(text('p', 'lbl', 'Aucun flux à inspecter.'));
    return;
  }
  const wrap = document.createElement('div');
  wrap.className = 'ob-wrap';
  for (const [name, entries] of streams) {
    const panel = document.createElement('div');
    panel.className = 'ob-panel';
    panel.append(text('h3', null, `${name} · ${entries.length} évènement(s)`));
    const list = document.createElement('div');
    list.className = 'ob-activite-list';
    for (const entry of entries.slice(-20).reverse()) {
      const line = text('p', 'mono', JSON.stringify(entry).slice(0, 200));
      line.style.margin = '2px 0';
      list.append(line);
    }
    panel.append(list);
    wrap.append(panel);
  }
  root.append(wrap);
  ctx.inspector.replaceChildren(text('p', 'lbl', `${streams.length} flux d'événements chargé(s).`));
  ctx.dock.log('traces', ...streams.map(([name, entries]) => `${name} : ${entries.length} évènement(s)`));
}

// RTK et Bench : aucune route serveur ne les alimente aujourd'hui (grep sur
// forge_routes.py) — RTK vit uniquement côté CLI (`rtk gain`), et il n'existe
// pas de bench exporté par ce serveur. Plutôt qu'un onglet qui ne réagit à
// rien, l'un et l'autre le disent explicitement et pointent la vraie
// commande CLI qui, elle, existe.
function renderUnavailable(root, ctx, title, sentence, command) {
  root.append(ctx.empty(title, sentence, command));
  ctx.inspector.replaceChildren(text('p', 'lbl', 'Rien à inspecter : aucune donnée serveur pour cet onglet.'));
}

export async function mount(root, ctx) {
  injectStyles();
  ctx.docbar.setBreadcrumb([ctx.host.project || 'projet', 'Observer']);

  let view = 'runtime';

  const [otel, costModel, flowRuns, events] = await Promise.all([
    ctx.api.otel().catch(() => ({ spans: [] })),
    ctx.api.costModel().catch(() => null),
    // Distinct du TraceLedger qu'`otel()` lit (#506) : un `grimoire
    // upgrade-flow run` n'y écrit jamais de span, et cet espace disait
    // « TraceLedger vide » même juste après un run réel — vrai du ledger,
    // trompeur pour qui vient de lancer une mise à jour depuis Piloter.
    ctx.api.flowRuns().catch(() => ({ runs: [] })),
    ctx.api.eventsLog().catch(() => ({})),
  ]);
  const spans = Array.isArray(otel?.spans) ? otel.spans : [];
  const modelRates = costModel?.modelRates || null;
  const runs = Array.isArray(flowRuns?.runs) ? flowRuns.runs : [];
  // Même lecture que le KPI « runs de flow » de la fiche Piloter
  // (`flow_runs_view`) : le total, pas seulement les runs rendus.
  const runsTotal = Number.isFinite(flowRuns?.total) ? flowRuns.total : runs.length;
  const streams = Object.entries(events || {}).filter(([, entries]) => Array.isArray(entries) && entries.length);

  if (ctx.signal.aborted) return;

  const setView = (id) => { view = id; draw(); };

  function draw() {
    root.replaceChildren();
    // Le callback (3e argument) doit être repassé À CHAQUE rendu, sinon le
    // premier clic met bien `aria-pressed` à jour (le shell recrée les
    // boutons) mais plus aucun clic suivant n'appelle `onPick` : régression
    // constatée sur cet espace, cf. revue du 2026-09-14.
    ctx.docbar.setViews(
      [{ id: 'runtime', label: 'Runtime' }, { id: 'activite', label: 'Activité' },
       { id: 'rtk', label: 'RTK' }, { id: 'bench', label: 'Bench' }],
      view,
      setView,
    );
    if (view === 'activite') {
      renderActivite(root, ctx, { events });
      return;
    }
    if (view === 'rtk') {
      renderUnavailable(
        root, ctx, 'Observer — RTK',
        "RTK (rust token killer) n'expose aujourd'hui aucune route serveur : ses économies "
        + "ne vivent que côté CLI, dans son propre journal.",
        'rtk gain --history',
      );
      return;
    }
    if (view === 'bench') {
      renderUnavailable(
        root, ctx, 'Observer — Bench',
        "Aucun résultat de benchmark n'est exposé par ce serveur pour l'instant.",
        'grimoire blueprint evals --record',
      );
      return;
    }
    renderRuntime(root, ctx, { spans, modelRates, runs, runsTotal, streams, onActivite: () => setView('activite') });
  }

  draw();
}
