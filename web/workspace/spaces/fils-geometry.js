// La géométrie de la vue « En direct » (lot B, artboard « Proposition A :
// Tissage »). Module pur : aucun DOM, aucun import, aucune couleur — il prend
// la charge utile de `GET /api/workspace/fleet/live` (lot A) et rend des
// chemins SVG et des positions. `tests/unit/test_workspace_live_view.py`
// l'exécute sous Node sur une réponse d'exemple.
//
// Une session = un fil horizontal sur la ligne de temps de la fenêtre. Un
// sous-agent a sa propre ligne : une courbe le détache du fil parent à son
// début, une autre l'y rejoint à sa fin (« rapport rendu »). Un worker, un
// relecteur ou un merger lancé par le tick naît de son fil par une courbe
// (`edges` de type `launched`) puis suit sa propre ligne. Un changement de
// modèle coupe le fil en segments — celui d'avant est plus pâle — et pose un
// losange au point de bascule.
//
// Trois paliers d'épaisseur portent les jetons consommés par la session :
// moins de 20 000, moins de 200 000, au-delà. Rien n'est inventé : un `at`
// absent ne pose aucun repère, une session sans usage reste au fil le plus fin.

/** Fenêtre par défaut, en minutes — celle du lot A. */
export const WINDOW_DEFAULT_MINUTES = 240;

/** Les fenêtres proposées par l'écran, en minutes. */
export const WINDOW_CHOICES = [60, 240, 1440];

//: Le nom de token d'un fournisseur. Un fournisseur absent du registre tombe
//: sur `--p-inconnu` : jamais une couleur supposée.
const PROVIDER_TOKENS = {
  anthropic: '--p-anthropic',
  openai: '--p-openai',
  google: '--p-google',
  deepseek: '--p-deepseek',
  mistral: '--p-mistral',
  local: '--p-local',
};

const PROVIDER_ALIASES = {
  gemini: 'google',
  'google-gemini': 'google',
  ollama: 'local',
  llama: 'local',
  meta: 'local',
  'open-weights': 'local',
  'openai-compatible': 'openai',
  'claude': 'anthropic',
};

/** Le token CSS d'un fournisseur (`--p-anthropic`, …, `--p-inconnu`). */
export function providerToken(provider) {
  const raw = String(provider || '').trim().toLowerCase();
  const key = PROVIDER_ALIASES[raw] || raw;
  return PROVIDER_TOKENS[key] || '--p-inconnu';
}

/** Les jetons consommés (entrée + sortie) — un compteur absent vaut 0. */
export function consumedTokens(usage) {
  if (!usage || typeof usage !== 'object') return 0;
  const input = Number(usage.input) || 0;
  const output = Number(usage.output) || 0;
  return Math.max(0, input) + Math.max(0, output);
}

/** Trois paliers d'épaisseur de fil, en pixels : 1,5 / 3 / 5. */
export function tokenStroke(usage) {
  const tokens = consumedTokens(usage);
  if (tokens >= 200000) return 5;
  if (tokens >= 20000) return 3;
  return 1.5;
}

const VENDORS = new Set(['claude', 'gpt', 'deepseek', 'gemini', 'mistral', 'openai', 'meta', 'llama', 'qwen', 'anthropic']);
const NOISE = new Set(['v1', 'v2', 'v3', 'latest', 'preview', 'beta', 'instruct']);

/** « claude-sonnet-5-5 » → « Sonnet » ; « deepseek-flash » → « Flash ». */
export function modelLabel(model) {
  if (!model) return 'modèle inconnu';
  const parts = String(model).toLowerCase().split(/[-_./]+/).filter(Boolean);
  const kept = parts.filter((part, index) => {
    if (index === 0 && VENDORS.has(part)) return false;
    if (/^\d+$/.test(part)) return false;
    return !NOISE.has(part);
  });
  const words = (kept.length ? kept : parts).map((part) => part.charAt(0).toUpperCase() + part.slice(1));
  return words.join(' ') || String(model);
}

/** « 14:32 » d'un instant ISO, heure locale ; chaîne vide si illisible. */
export function clockLabel(iso) {
  const at = parseTime(iso);
  if (at === null) return '';
  const date = new Date(at);
  const pad = (value) => String(value).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function parseTime(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const at = Date.parse(String(value));
  return Number.isNaN(at) ? null : at;
}

function number(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function round(value) {
  return Math.round(value * 100) / 100;
}

function byTime(a, b) {
  const left = parseTime(a && a.at);
  const right = parseTime(b && b.at);
  return (left === null ? 0 : left) - (right === null ? 0 : right);
}

function shortId(id) {
  const text = String(id || '');
  return text.length > 8 ? text.slice(0, 8) : text;
}

/** Le nom porté au bout du fil : la tâche tenue, la branche, sinon la session. */
export function threadName(session) {
  if (!session || typeof session !== 'object') return 'session inconnue';
  if (session.task_id) return String(session.task_id);
  // Un sous-agent hérite de la branche de son parent : son nom est ce qu'on
  // lui a demandé, sinon sa persona.
  if (session.role === 'subagent') {
    const agent = session.agent && typeof session.agent === 'object' ? session.agent : {};
    if (agent.description) return String(agent.description);
    if (agent.type) return String(agent.type);
    return `sous-agent ${shortId(session.id)}`;
  }
  if (session.branch) return String(session.branch);
  return session.id ? shortId(session.id) : 'session inconnue';
}

/** Largeur approchée d'un texte en px (police UI, ~0,56 em par signe). */
export function textWidth(text, size = 12) {
  return Math.ceil(String(text || '').length * size * 0.56);
}

/** Tronque un nom pour qu'il tienne dans `maxWidth` px, avec une ellipse. */
export function fitLabel(text, maxWidth, size = 13) {
  const value = String(text || '');
  if (textWidth(value, size) <= maxWidth) return value;
  const keep = Math.max(1, Math.floor(maxWidth / (size * 0.56)) - 1);
  return `${value.slice(0, keep)}…`;
}

/**
 * Place des étiquettes sans recouvrement : chacune garde son `x` et prend la
 * première rangée libre (au-dessus du fil, puis en dessous, puis plus haut).
 * Rend les étiquettes avec `{ x, y, anchor, box }`, `box` = `[x0, y0, x1, y1]`.
 */
export function placeLabels(items, { clampLeft = 0, clampRight = Infinity, size = 11 } = {}) {
  const placed = [];
  const rows = [-8, 16, -22, 30];
  // 10 px d'air entre deux étiquettes d'une même rangée : collées, elles se
  // lisent comme une seule (« Fable → Opus Opus → Fable »).
  const gap = 10;
  const overlaps = (a, b) => a[0] < b[2] + gap && b[0] < a[2] + gap && a[1] < b[3] && b[1] < a[3];
  for (const item of items) {
    const width = textWidth(item.text, size);
    // Ancré à droite du repère, sauf s'il déborderait de la zone de tracé.
    const anchorEnd = item.x + 8 + width > clampRight;
    const x0 = anchorEnd ? Math.max(clampLeft, Math.min(item.x, clampRight) - 8 - width) : Math.max(clampLeft, item.x + 8);
    let chosen = null;
    for (const dy of rows) {
      const y = item.y + dy;
      const box = [x0, y - size, x0 + width, y + 3];
      if (!placed.some((other) => overlaps(other.box, box)) && !(item.avoid || []).some((zone) => overlaps(zone, box))) {
        chosen = { ...item, x: round(anchorEnd ? x0 + width : x0), y: round(y), anchor: anchorEnd ? 'end' : 'start', box };
        break;
      }
    }
    if (!chosen) continue; // plus de place : l'infobulle du repère porte le libellé
    placed.push(chosen);
  }
  return placed;
}

/** Un fil : `M x y C …` d'un point à un autre, pour une arête qui change de ligne. */
function curve(fromX, fromY, toX, toY) {
  const pull = Math.max(4, Math.abs(toX - fromX) * 0.5);
  return `M ${round(fromX)} ${round(fromY)} C ${round(fromX + pull)} ${round(fromY)}, `
    + `${round(toX - pull)} ${round(toY)}, ${round(toX)} ${round(toY)}`;
}

function line(fromX, toX, y) {
  return `M ${round(fromX)} ${round(y)} L ${round(toX)} ${round(y)}`;
}

/**
 * Dispose une charge utile `fleet/live` en fils.
 *
 * Options : `width` (960), `laneHeight` (34), `projectGap` (22),
 * `labelWidth` (150, colonne des noms de projet), `rightPad` (170, étiquettes
 * directes), `paddingTop` (18).
 *
 * Rend `{ width, height, plot_left, plot_right, now_x, threads, edges,
 * changes, markers, loops, projects }` — un `threads` par session, un `edges`
 * par arête connue, un `changes` par changement de modèle.
 */
export function layoutFleetThreads(payload, options = {}) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const sessions = Array.isArray(data.sessions) ? data.sessions.filter((row) => row && row.id) : [];
  const width = number(options.width, 960);
  const laneHeight = number(options.laneHeight, 48);
  const projectGap = number(options.projectGap, 22);
  const paddingTop = number(options.paddingTop, 18);
  const labelWidth = number(options.labelWidth, 150);
  const rightPad = number(options.rightPad, 200);

  const nowMs = parseTime(data.generated_at);
  const endMs = nowMs === null ? Date.now() : nowMs;
  const windowMinutes = Math.max(1, number(data.window_minutes, WINDOW_DEFAULT_MINUTES));
  const startMs = endMs - windowMinutes * 60000;
  const plotLeft = labelWidth;
  const plotRight = Math.max(plotLeft + 40, width - rightPad);
  const span = Math.max(1, plotRight - plotLeft);

  /** Une position sur la ligne de temps, bornée à la fenêtre. */
  const x = (iso) => {
    const at = parseTime(iso);
    if (at === null) return plotRight;
    const clamped = Math.min(endMs, Math.max(startMs, at));
    return plotLeft + span * ((clamped - startMs) / Math.max(1, endMs - startMs));
  };

  // Regroupement par projet, dans l'ordre de la réponse (les vivantes d'abord,
  // déjà trié côté serveur). Un projet inconnu garde son slug tel quel.
  const grouped = new Map();
  for (const session of sessions) {
    const project = String(session.project || '');
    if (!grouped.has(project)) grouped.set(project, []);
    grouped.get(project).push(session);
  }

  const lanes = [];
  const projects = [];
  let cursor = paddingTop;
  for (const [project, rows] of grouped) {
    const top = cursor;
    for (const session of rows) {
      lanes.push({ session, y: cursor + laneHeight / 2 });
      cursor += laneHeight;
    }
    projects.push({ name: project, top: round(top), bottom: round(cursor), separator_y: round(cursor + projectGap / 2) });
    cursor += projectGap;
  }
  const height = Math.max(cursor, paddingTop + laneHeight);
  const laneOf = new Map(lanes.map((lane) => [lane.session.id, lane]));

  const threads = [];
  const changes = [];
  const markers = [];
  const loops = [];

  for (const lane of lanes) {
    const session = lane.session;
    const y = round(lane.y);
    const startedX = x(session.started_at || session.last_at);
    const lastX = x(session.last_at || session.started_at);
    const continues = session.liveness === 'active' || session.liveness === 'waiting';
    const endX = Math.max(startedX, continues ? plotRight : lastX);

    // Segments de modèle : celui d'avant chaque bascule est plus pâle.
    // Hors fenêtre, un repère n'a pas de place sur la ligne de temps : le
    // borner au bord gauche le ferait mentir (un changement du 06/11 dessiné
    // au début d'une fenêtre de 4 h). Le parcours du modèle, dans le détail,
    // garde l'historique complet.
    const inWindow = (iso) => {
      const at = parseTime(iso);
      return at !== null && at >= startMs && at <= endMs;
    };
    const modelChanges = (Array.isArray(session.model_changes) ? session.model_changes : [])
      .filter((change) => change && inWindow(change.at))
      .sort(byTime);
    const segments = [];
    let from = startedX;
    for (const change of modelChanges) {
      const at = Math.min(endX, Math.max(from, x(change.at)));
      if (at <= from) continue;
      segments.push({ from: round(from), to: round(at), y, pale: true, path: line(from, at, y) });
      from = at;
    }
    if (from < endX || !segments.length) {
      segments.push({ from: round(from), to: round(endX), y, pale: false, path: line(from, endX, y) });
    }

    const threadChanges = [];
    for (const change of modelChanges) {
      const at = Math.min(endX, Math.max(startedX, x(change.at)));
      const entry = {
        session_id: session.id,
        x: round(at),
        y,
        from: change.from || null,
        to: change.to || null,
        kind: change.kind || null,
        turn: change.turn ?? null,
        label: `${modelLabel(change.from)} → ${modelLabel(change.to)}`,
      };
      threadChanges.push(entry);
      changes.push(entry);
    }

    const end = continues
      ? { kind: 'live', x: round(plotRight), y, label: '' }
      : session.liveness === 'silent'
        ? { kind: 'silent', x: round(lastX), y, label: `muette depuis ${clockLabel(session.last_at)}` }
        : { kind: 'ended', x: round(lastX), y, label: 'terminée' };

    // Repères du journal : les erreurs, questions et compactions. Les
    // changements de modèle ont déjà leur losange — on ne les redouble pas.
    const threadMarkers = [];
    for (const marker of Array.isArray(session.markers) ? session.markers : []) {
      if (!marker || marker.kind === 'model_change' || !inWindow(marker.at)) continue;
      const entry = {
        session_id: session.id,
        kind: marker.kind || 'marker',
        label: marker.label || '',
        x: round(Math.min(endX, Math.max(startedX, x(marker.at)))),
        y,
      };
      threadMarkers.push(entry);
      markers.push(entry);
    }
    // Une boucle : trois erreurs au même libellé. Un nœud rouge sur le fil,
    // une seule étiquette, jamais trois répétitions.
    const errorsByLabel = new Map();
    for (const entry of threadMarkers) {
      if (entry.kind !== 'error') continue;
      const label = entry.label || 'échec';
      if (!errorsByLabel.has(label)) errorsByLabel.set(label, []);
      errorsByLabel.get(label).push(entry);
    }
    for (const [, same] of errorsByLabel) {
      if (same.length < 3) continue;
      const last = same[same.length - 1];
      loops.push({
        session_id: session.id,
        x: last.x,
        y,
        label: 'même échec, trois fois',
        count: same.length,
      });
    }

    const model = session.model && typeof session.model === 'object' ? session.model : {};
    threads.push({
      id: session.id,
      project: String(session.project || ''),
      role: session.role || null,
      y,
      x0: round(startedX),
      x1: round(endX),
      path: line(startedX, endX, y),
      segments,
      changes: threadChanges,
      markers: threadMarkers,
      end,
      label: {
        x: round(Math.min(width - 4, plotRight + 10)),
        y,
        name: threadName(session),
        short: fitLabel(threadName(session), Math.max(60, rightPad - 24)),
        model: modelLabel(model.current || model.requested),
        model_id: model.current || model.requested || null,
        provider: model.provider || 'inconnu',
        token: providerToken(model.provider),
        stroke: tokenStroke(session.usage),
      },
      liveness: session.liveness || null,
      // Le fil du tick (orchestrateur automatique) se dessine en pointillé
      // gris, comme sur l'artboard ; tout le reste suit le fournisseur.
      tick: session.role === 'orchestrator' || session.entrypoint === 'tick',
      subagents: Array.isArray(session.subagents) ? session.subagents : [],
    });
  }

  // Étiquettes directes (bascules de modèle, boucles, fin muette) : une seule
  // par repère, placées sans recouvrement, jamais dans la colonne des noms.
  const nameColumn = [plotRight - 12, 0, width, height];
  const labelItems = [];
  for (const thread of threads) {
    const base = { session_id: thread.id, avoid: [nameColumn] };
    for (const change of thread.changes) labelItems.push({ ...base, kind: 'change', x: change.x, y: thread.y, text: change.label });
    for (const loop of loops.filter((entry) => entry.session_id === thread.id)) labelItems.push({ ...base, kind: 'loop', x: loop.x, y: thread.y, text: loop.label });
    if (thread.end.kind === 'silent') labelItems.push({ ...base, kind: 'silent', x: thread.end.x, y: thread.y, text: thread.end.label });
  }
  // Les boucles d'abord : ce sont elles qu'on doit voir.
  labelItems.sort((a, b) => (a.kind === 'loop' ? 0 : 1) - (b.kind === 'loop' ? 0 : 1));
  const labels = placeLabels(labelItems, { clampLeft: plotLeft, clampRight: plotRight - 14 })
    .map((item) => ({ kind: item.kind, session_id: item.session_id, x: item.x, y: item.y, anchor: item.anchor, text: item.text, box: item.box }));

  const edges = [];
  for (const edge of Array.isArray(data.edges) ? data.edges : []) {
    if (!edge || !edge.from || !edge.to) continue;
    const parent = laneOf.get(edge.from);
    const child = laneOf.get(edge.to);
    if (!parent || !child) continue;
    const childSession = child.session;
    // Le départ quitte le fil parent un peu avant le début du fils et le
    // rejoint à son début : une courbe vers l'avant, jamais une boucle
    // verticale quand les deux points ont le même x.
    const startX = x(childSession.started_at || childSession.last_at);
    const lead = Math.min(28, Math.max(10, startX - plotLeft));
    const departure = curve(startX - lead, parent.y, startX, child.y);
    if (edge.kind === 'spawned') {
      const joinX = Math.min(plotRight, Math.max(startX, x(childSession.last_at || childSession.started_at)));
      edges.push({
        from: edge.from,
        to: edge.to,
        kind: 'spawned',
        departure,
        return: curve(joinX, child.y, Math.min(plotRight, joinX + lead), parent.y),
        return_point: { x: round(Math.min(plotRight, joinX + lead)), y: round(parent.y) },
        label: 'rapport rendu',
      });
    } else {
      edges.push({
        from: edge.from,
        to: edge.to,
        kind: edge.kind || 'launched',
        departure,
        launch: departure,
        label: '',
      });
    }
  }

  return {
    width: round(width),
    height: round(height),
    plot_left: round(plotLeft),
    plot_right: round(plotRight),
    now_x: round(plotRight),
    window_minutes: windowMinutes,
    from: new Date(startMs).toISOString(),
    to: new Date(endMs).toISOString(),
    projects,
    threads,
    edges,
    changes,
    markers,
    loops,
    labels,
  };
}
