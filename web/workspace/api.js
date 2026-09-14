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
  // N'ajoute `project` que s'il est absent : un appel qui cible déjà un AUTRE
  // projet explicitement (`agents(project)`, `proposals(project)`, la Flotte
  // qui interroge chaque projet du registre l'un après l'autre) portait déjà
  // son propre `?project=` dans `path` — l'ajouter en plus ne le remplaçait
  // pas, il s'AJOUTAIT en deuxième valeur du même paramètre
  // (`?project=terraform&project=grimoire-forge`, la valeur ambiante de
  // `host.project` gagnant côté serveur selon le parseur). Ce doublon a fait
  // lire à la garde d'écriture le mauvais projet lors du premier test réel du
  // cockpit. `URLSearchParams` déduplique en ne posant `project` que quand il
  // manque, jamais en écrasant le choix explicite de l'appelant.
  const [base, query = ''] = path.split('?');
  const params = new URLSearchParams(query);
  if (params.has('project')) return path;
  params.set('project', host.project);
  return `${base}?${params.toString()}`;
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
  // Suggestions par un petit modèle local (#280, voie 2) — jamais à la place
  // de `language()` ci-dessus, toujours derrière : `assistStatus()` est une
  // lecture sans coût (l'éditeur l'appelle au montage pour savoir si le
  // bouton « Suggérer » doit même apparaître) ; `assist()` appelle
  // réellement le modèle et rend `{available, model?, suggestion?, unknown?,
  // reason?}` — `available: false` est une réponse normale (opt-in absent,
  // Ollama indisponible, délai dépassé), jamais une exception.
  assistStatus: () => get(WS + 'assist'),
  assist: (path, { text, position, intent, diagnostic } = {}) =>
    post(WS + 'assist', { path, text, position, intent, diagnostic }),
  // Concevoir (lot 3) : les containers enrichis (genre, agents, équipe,
  // dernière modification) que `/api/blueprints` seul ne porte pas.
  blueprintContainers: () => get(WS + 'blueprints'),
  // Agents du projet — clause d'emploi, outils, contexte, skills, usage réel
  // (#374). `{agents[], skills[], entry_point}` — `skills[]` est le catalogue
  // disponible pour l'assignation, pas seulement ceux déjà attachés. `project`
  // cible un AUTRE projet que celui déjà résolu — même convention que
  // `health()` juste au-dessus, pour le niveau Flotte de Piloter.
  agents: (project) => get(WS + 'agents', project ? { project } : undefined),
  // Propositions d'artefact (#395) : le déclencheur relit le journal des
  // non-choix à chaque appel — jamais un fichier figé — et rend les faits
  // qui fondent chaque proposition (spécialité, catégorie, agent de repli,
  // compte). Écritures via `proposalAction`, jamais silencieuses.
  proposals: (project) => get(WS + 'proposals', project ? { project } : undefined),
  // Agrégation mémoire multi-projets (#172, dernier volet de « du générateur
  // statique au portefeuille actif ») : `projects` vaut 'all', une liste
  // 'slug1,slug2', ou est omis pour « ce projet » seul — jamais toute la
  // flotte par défaut. Lecture seule, aucune fusion de stores.
  memoryOverview: (projects) => get(WS + 'memory/overview', projects ? { projects } : undefined),
  memorySearch: (q, projects) => get(WS + 'memory/search', projects ? { q, projects } : { q }),

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
  // Assigner/retirer un skill, ou modifier la clause d'emploi/outils/contexte
  // d'un agent (#374) — porte toujours sur sa copie `overrides`, jamais sur
  // le kit ; refusé côté serveur hors projet d'accueil, comme le reste.
  agentSkill: (name, skill, action) =>
    post(WS + 'agents/' + encodeURIComponent(name) + '/skill', { skill, action }),
  agentFields: (name, fields) =>
    post(WS + 'agents/' + encodeURIComponent(name) + '/fields', fields),
  // Accepter écrit l'artefact réel (agent, skill attaché, ou substitution
  // `repair` évidente) ; refuser ne fait que marquer la proposition (#395).
  // Jamais d'option automatique — chaque appel est le geste explicite que
  // l'issue exige. Décider une proposition ouvre la même porte que
  // `updateProject` ci-dessous, pour n'importe quel projet du registre
  // (#490) : `postOpen` contourne donc le même verrou `readOnly` général, et
  // `project` est explicite dans la requête plutôt que confié à l'état
  // ambiant `host.project` — la fiche qui rend ce bouton connaît déjà le
  // projet qu'elle affiche, c'est le sien qu'il faut décider, jamais celui
  // que le cockpit sert par défaut.
  proposalAction: (slug, action, project) =>
    postOpen(
      WS + 'proposals/' + encodeURIComponent(slug) + '/' + action +
        (project ? '?' + new URLSearchParams({ project }).toString() : ''),
      {},
    ),
  blueprintPut: (id, blueprint) => put('/api/blueprints/' + encodeURIComponent(id), blueprint),

  // Aligner un projet sur le kit installé — `grimoire upgrade-flow run`
  // (#490), pas `up` seul : sauvegarde, aperçu, orphelins, application,
  // propositions (overrides/mémoire/besoins), vérification. Disponible sur
  // les deux hôtes : `confirm: false` (par défaut) s'arrête après l'aperçu
  // (`report.preview`, jamais réécrit) ; `confirm: true` lance le flow
  // complet, mécanique, arrêté au checkpoint final (`report.report`,
  // `report.proposals` — jamais décidé à la place de qui que ce soit).
  // `project` est le slug ciblé (portefeuille) ; omis, la cible est le
  // projet déjà servi par l'atelier.
  updateProject: (project, confirm = false) =>
    postOpen('/api/projects/update', { project: project || undefined, confirm }),

  // ── Wizard de setup — le wizard exécute (#171) ─────────────────────────────
  // Catalogues lus par le wizard avant de le montrer : archétypes (déjà
  // servis pour atelier.html), backends mémoire connus, needs du catalogue +
  // suggestions pour CE projet (pont B2/B3, `needs_suggest.py`).
  archetypesCatalogue: () => get('/api/archetypes'),
  backendsCatalogue: () => get('/api/backends'),
  needsCatalogue: () => get('/api/needs'),
  // Exécute réellement le plan (même mécanique que `grimoire up`, jamais un
  // sous-processus — voir project_setup.execute_setup_plan). Refusé côté
  // serveur, fail-closed, avant toute écriture si le plan ne peut pas
  // s'exécuter. `planOnly: true` garde l'ancien repli — écrire
  // `_grimoire/setup-plan.json` et rendre la commande à copier-coller, sans
  // rien exécuter — pour qui préfère lancer `grimoire up` lui-même.
  setupPlan: (payload) => post('/api/setup', payload || {}),
  // Dernier journal d'exécution (`_grimoire/setup-run.json`), pour relire la
  // progression après un rechargement de page. `project` cible un AUTRE
  // projet que celui déjà résolu — même convention que `health()`.
  setupRun: (project) => get('/api/setup/run', project ? { project } : undefined),

  // « Nouveau projet » depuis le portefeuille (#172) : `path` est explicite
  // dans le corps, comme `updateProject` — la porte n'est donc pas la garde
  // générale `readOnly` (regarder un projet en lecture seule n'empêche pas
  // d'en créer un autre), mais la validation propre de la route côté
  // serveur (chemin permis, pas déjà un projet, plan exécutable).
  createProject: (payload) => postOpen('/api/projects/create', payload),
};

export default api;
