// Le seul endroit qui parle à l'API locale.
//
// Deux hôtes, une coque : `grimoire serve` sert un projet, `grimoire cockpit
// serve` en sert N et attend `?project=<slug>` sur chaque lecture. Ce module
// porte cette différence pour que les six modules d'espace ne l'aient jamais à
// connaître : ils appellent `api.tasks()`, et la cible est déjà bonne.
//
// Aucun module d'espace ne doit appeler `fetch` directement. Une route qui
// manque s'ajoute ici, et son contrat est celui de
// `src/grimoire/tools/workspace_routes.py`.

const WS = '/api/workspace/';

/** État de l'hôte, résolu une fois à l'amorçage. */
export const host = {
  /** 'atelier' | 'cockpit' — décidé par la réponse de /api/status. */
  kind: 'atelier',
  /** Slug du projet ciblé côté cockpit ; null en atelier. */
  project: null,
  /** Vrai quand l'hôte refuse les écritures (cockpit). */
  readOnly: false,
  // Vrai si `project` vient d'un `?project=` explicite dans l'URL (une
  // navigation cockpit délibérée — carte Flotte cliquée, lien copié) ; faux
  // s'il vient de la sélection courante que le serveur a résolue tout seul
  // (`?project=` absent — lancement direct depuis un projet, #351/#356). Les
  // espaces qui distinguent « je regarde ce projet en passant » de « j'ai
  // lancé le cockpit depuis ce projet » lisent ce champ, pas seulement
  // `project` (voir piloter.js : Flotte reste le point d'entrée par défaut du
  // pilotage de flotte, sauf lancement direct).
  projectFromUrl: false,
  status: null,
};

class ApiError extends Error {
  constructor(message, code, payload) {
    super(message);
    this.code = code;
    this.payload = payload;
  }
}
export { ApiError };

function withProject(path) {
  if (!host.project) return path;
  return path + (path.includes('?') ? '&' : '?') + 'project=' + encodeURIComponent(host.project);
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(withProject(path), {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch (cause) {
    throw new ApiError('API locale injoignable', 0, { cause: String(cause) });
  }
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = { raw: text }; }
  }
  if (!response.ok) {
    const message = (payload && (payload.error || payload.message)) || `HTTP ${response.status}`;
    throw new ApiError(message, response.status, payload);
  }
  return payload;
}

function get(path, params) {
  const query = params ? '?' + new URLSearchParams(params).toString() : '';
  return request(path + query);
}

function post(path, body) {
  if (host.readOnly) {
    return Promise.reject(new ApiError('hôte en lecture seule', 403, { readOnly: true }));
  }
  return request(path, { method: 'POST', body: JSON.stringify(body || {}) });
}

// `/api/projects/update` n'est PAS une écriture de la vue de travail : c'est
// la seule route où le cockpit, lecture seule sur les tâches et les fichiers,
// écrit légitimement dans un dépôt — avec son propre aperçu par défaut et son
// `confirm` explicite (spec §4, Piloter → « mettre à jour »). La gate générale
// de `post()` la refuserait par erreur ; elle appelle donc `request` directement.
function postOpen(path, body) {
  return request(path, { method: 'POST', body: JSON.stringify(body || {}) });
}

function put(path, body) {
  if (host.readOnly) {
    return Promise.reject(new ApiError('hôte en lecture seule', 403, { readOnly: true }));
  }
  return request(path, { method: 'PUT', body: JSON.stringify(body || {}) });
}

/** Amorçage : résout l'hôte et le projet ciblé. À appeler une fois. */
export async function boot() {
  const params = new URLSearchParams(location.search);
  const wanted = params.get('project');
  if (wanted) host.project = wanted;
  const status = await get('/api/status');
  host.status = status;
  host.kind = status.host === 'cockpit' ? 'cockpit' : 'atelier';
  host.readOnly = status.readOnly === true;
  host.projectFromUrl = host.kind === 'cockpit' && Boolean(wanted);
  if (host.kind === 'cockpit') host.project = host.project || status.project || status.slug;
  else host.project = null;
  return status;
}

// ── Lectures partagées par les deux hôtes ───────────────────────────────────

export const api = {
  status: () => get('/api/status'),
  // `project` cible un AUTRE projet que celui déjà résolu par l'hôte — c'est ce
  // dont a besoin le niveau Flotte de Piloter, qui appelle la santé de chaque
  // projet du registre l'un après l'autre. Sans argument, le comportement est
  // celui d'avant : la santé du projet déjà ciblé par l'hôte.
  health: (project) => get('/api/health', project ? { project } : undefined),
  projects: () => get('/api/projects'),
  memoryStatus: (project) => get('/api/memory/status', project ? { project } : undefined),
  blueprints: () => get('/api/blueprints'),
  primitives: () => get('/api/primitives'),
  features: () => get('/api/features'),
  stigmergy: () => get('/api/stigmergy'),
  eventsLog: () => get('/api/events/log'),
  otel: () => get('/api/otel'),

  // ── Vue de travail ────────────────────────────────────────────────────────
  glossary: () => get(WS + 'glossary'),
  tasks: (params) => get(WS + 'tasks', params),
  task: (id) => get(WS + 'tasks/' + encodeURIComponent(id)),
  taskTrace: (id) => get(WS + 'tasks/' + encodeURIComponent(id) + '/trace'),
  taskRecall: (id) => get(WS + 'tasks/' + encodeURIComponent(id) + '/recall'),
  files: (tier) => get(WS + 'files', tier ? { tier } : undefined),
  file: (path) => get(WS + 'file', { path }),
  fileDiff: (path) => get(WS + 'file/diff', { path }),
  fileUsage: (path) => get(WS + 'file/usage', { path }),
  fileHistory: (path) => get(WS + 'file/history', { path }),
  commands: () => get(WS + 'commands'),
  doctor: (project) => get(WS + 'doctor', project ? { project } : undefined),
  // IntelliSense de l'éditeur Source (#280) : tokens + diagnostics toujours,
  // complétions seulement si `pos` est fourni. `text` porte le brouillon en
  // cours d'édition — omis, la lecture vient du disque comme `file()`.
  language: (path, { text, pos } = {}) => {
    const params = { path };
    if (text !== undefined) params.text = text;
    if (pos) { params.line = String(pos.line); params.col = String(pos.col); }
    return get(WS + 'language', params);
  },
  // Concevoir (lot 3) : les containers enrichis (genre, agents, équipe,
  // dernière modification) que `/api/blueprints` seul ne porte pas.
  blueprintContainers: () => get(WS + 'blueprints'),

  // ── Blueprints : éditeur de graphe (édition — atelier seulement) ───────────
  // Lecture, validation et simulation sont disponibles sur les deux hôtes
  // (le cockpit les sert pour le projet déjà sélectionné, #356) : aucune des
  // trois n'écrit sur disque. `postOpen` contourne donc le même verrou
  // `readOnly` que `updateProject` plus haut, et pour la même raison — ce
  // n'est pas une mutation de la vue de travail. Seules `blueprintPut` et
  // `blueprintCompile` écrivent réellement, et restent derrière `put`/`post`.
  blueprintGet: (id) => get('/api/blueprints/' + encodeURIComponent(id)),
  blueprintDiff: (id, ref) =>
    get('/api/blueprints/' + encodeURIComponent(id) + '/diff', ref ? { ref } : undefined),
  blueprintValidate: (id, blueprint) =>
    postOpen('/api/blueprints/' + encodeURIComponent(id) + '/validate', blueprint),
  blueprintSimulate: (id, blueprint) =>
    postOpen('/api/blueprints/' + encodeURIComponent(id) + '/simulate', blueprint),
  blueprintCompile: (id, blueprint) =>
    post('/api/blueprints/' + encodeURIComponent(id) + '/compile', blueprint),
  costModel: (model) => get('/api/cost-model', model ? { model } : undefined),

  // ── Écritures : atelier seulement, refusées côté cockpit ──────────────────
  taskAction: (id, action, body) =>
    post(WS + 'tasks/' + encodeURIComponent(id) + '/' + action, body),
  createOverride: (path) => post(WS + 'file/override', { path }),
  writeFile: (path, text) => post(WS + 'file/write', { path, text }),
  run: (argv) => post(WS + 'command', { argv }),
  blueprintPut: (id, blueprint) => put('/api/blueprints/' + encodeURIComponent(id), blueprint),

  // Aligner un projet sur le kit installé (`grimoire up`). Disponible sur les
  // deux hôtes : `confirm: false` (par défaut) rend un aperçu, `confirm: true`
  // écrit réellement. `project` est le slug ciblé (portefeuille) ; omis, la
  // cible est le projet déjà servi par l'atelier.
  updateProject: (project, confirm = false) =>
    postOpen('/api/projects/update', { project: project || undefined, confirm }),
};

export default api;
