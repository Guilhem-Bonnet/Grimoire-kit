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

const STYLE_ID = 'ob-styles';

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ob-wrap { padding: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-5); }
    .ob-kpi { display: flex; flex-wrap: wrap; border: 1px solid var(--line); border-radius: var(--r); background: var(--e1); overflow: hidden; }
    .ob-kpi-item { flex: 1; min-width: 140px; padding: var(--sp-3) var(--sp-4); border-left: 1px solid var(--line); }
    .ob-kpi-item:first-child { border-left: 0; }
    .ob-kpi-val { font-family: var(--mono); font-size: var(--t-xl); font-weight: 600; color: var(--ink); }
    .ob-kpi-lbl { font-size: var(--t-min); color: var(--ink3); margin-top: 2px; }
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
    .ob-table { width: 100%; border-collapse: collapse; font-size: var(--t-s); }
    .ob-table th { text-align: left; font-size: var(--t-min); color: var(--ink3); font-weight: 500; padding: 6px 8px; border-bottom: 1px solid var(--line); }
    .ob-table td { padding: 6px 8px; border-bottom: 1px solid var(--line); }
    .ob-table tbody tr { cursor: pointer; }
    .ob-table tbody tr:hover { background: var(--e2); }
    .ob-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); }
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

const SERIES = ['--s1', '--s2', '--s3'];
const RATE_BUCKETS = ['opus', 'sonnet', 'haiku'];

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

function rateFor(modelRates, model) {
  if (!model) return null;
  const needle = String(model).toLowerCase();
  const bucket = RATE_BUCKETS.find((b) => needle.includes(b));
  return bucket && modelRates ? modelRates[bucket] : null;
}

function spanCostUsd(span, modelRates) {
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

function kpiCard(items) {
  const card = document.createElement('div');
  card.className = 'ob-kpi';
  for (const item of items) {
    const cell = document.createElement('div');
    cell.className = 'ob-kpi-item';
    cell.append(text('div', 'ob-kpi-val mono', item.value), text('div', 'ob-kpi-lbl', item.label));
    card.append(cell);
  }
  return card;
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
  table.className = 'ob-table';
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

export async function mount(root, ctx) {
  injectStyles();
  ctx.docbar.setBreadcrumb([ctx.host.project || 'projet', 'Observer']);
  ctx.docbar.setViews(
    [{ id: 'runtime', label: 'Runtime' }, { id: 'activite', label: 'Activité' },
     { id: 'rtk', label: 'RTK' }, { id: 'bench', label: 'Bench' }],
    'runtime',
  );

  const [otel, costModel] = await Promise.all([
    ctx.api.otel().catch(() => ({ spans: [] })),
    ctx.api.costModel().catch(() => null),
  ]);
  const spans = Array.isArray(otel?.spans) ? otel.spans : [];
  const modelRates = costModel?.modelRates || null;

  if (ctx.signal.aborted) return;

  if (!spans.length) {
    root.append(ctx.empty(
      'Observer',
      "Aucune trace : ce projet n'a pas encore exécuté de workflow instrumenté. "
      + 'La donnée viendra de _grimoire-runtime-output/hook-runtime/events.jsonl et '
      + 'task-flow/events.jsonl dès qu\'un blueprint aura tourné — lancez-en un puis '
      + 'ouvrez cet espace à nouveau.',
      'grimoire task trace <id>',
    ));
    ctx.inspector.replaceChildren(text('p', 'lbl', 'Aucun span à inspecter.'));
    ctx.dock.log('traces', 'Aucune trace dans le TraceLedger de ce projet.');
    ctx.dock.echo('grimoire task trace');
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'ob-wrap';

  const errorCount = spans.filter((s) => s.status?.code === 'ERROR').length;
  const agents = new Set(spans.map((s) => attr(s, 'gen_ai.agent.name')).filter(Boolean));
  const models = new Set(spans.map((s) => attr(s, 'gen_ai.request.model')).filter(Boolean));
  const totalCost = spans.reduce((sum, s) => sum + (spanCostUsd(s, modelRates) || 0), 0);
  const durations = spans.map(spanDurationMs).filter((d) => d != null);
  const avgLatency = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

  wrap.append(kpiCard([
    { value: String(spans.length), label: 'spans' },
    { value: String(agents.size), label: 'agents' },
    { value: String(models.size), label: 'modèles' },
    { value: '$' + totalCost.toFixed(4), label: 'coût estimé' },
    { value: avgLatency == null ? '—' : Math.round(avgLatency) + ' ms', label: 'latence moyenne' },
    { value: `${errorCount}/${spans.length}`, label: 'erreurs' },
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
