// Le seul endroit qui parle à l'API locale.
//
// Deux hôtes, une coque : `grimoire serve` sert un projet, `grimoire cockpit
// serve` en sert N et attend `?project=<slug>` sur chaque lecture. Ce module
// porte cette différence pour que les modules d'espace ne l'aient jamais à
// connaître : ils appellent `api.tasks()`, et la cible est déjà bonne.
//
// Aucun module d'espace ne doit appeler `fetch` directement. Une route qui
// manque s'ajoute ici, et son contrat est celui de
// `src/grimoire/tools/workspace_routes.py`.

const WS = '/api/workspace/';

/** Lectures d'attention en vol, par portée : partagées entre lecteurs simultanés. */
const attentionInFlight = new Map();
/** Dernière réponse d'attention par portée, reprise seulement par qui le demande (`maxAgeMs`). */
const attentionRecent = new Map();

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
  /** La portée de la coque (P31) : vrai = « Tous les projets » (`?scope=all`,
   *  ou cockpit sans aucun projet résolu). `project` reste alors le projet
   *  servi, celui auquel les lectures sans cible explicite s'adressent. */
  scopeAll: false,
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

// P79 : vrai pendant qu'un pouls changé fait recharger les espaces (live.js). Les lectures GET lancées alors
// portent `pulse_reload=1` (jamais `live`, déjà le filtre « Sessions vivantes » du portefeuille) : le serveur peut les servir depuis son cache tant que l'empreinte n'a pas bougé. Un
// chargement de page ou une navigation n'en portent pas : ils relisent toujours.
let liveReload = false;
export function duringLiveReload(run) {
  liveReload = true;
  try { return run(); } finally { liveReload = false; }
}

function liveMark(path, options) {
  if (!liveReload || (options.method && options.method !== 'GET')) return path;
  const [base, query = ''] = path.split('?');
  const params = new URLSearchParams(query);
  params.set('pulse_reload', '1');
  return `${base}?${params.toString()}`;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(withProject(liveMark(path, options)), {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch (cause) {
    // Une requête annulée par l'appelant (onglet masqué) n'est pas une panne.
    if (cause && cause.name === 'AbortError') throw cause;
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

function get(path, params, options) {
  const query = params ? '?' + new URLSearchParams(params).toString() : '';
  return request(path + query, options);
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
  // `?project=` l'emporte : un lien vers un projet ouvre ce projet.
  host.scopeAll = host.kind === 'cockpit' && ((params.get('scope') === 'all' && !wanted) || !host.project);
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
  // `probe: true` force une sonde réseau fraîche (coûteuse) — jamais le mode
  // par défaut. Sans lui, le serveur sert le dernier statut caché
  // (`probedAt`/`stale` explicites, voir `memory_link.py::memory_link_status`).
  // La Flotte et le premier rendu d'une fiche projet ne passent jamais
  // `probe` ; seul un geste explicite (bouton « Sonder la mémoire ») le fait.
  memoryStatus: (project, { probe } = {}) => {
    const params = { ...(project ? { project } : {}), ...(probe ? { probe: '1' } : {}) };
    return get('/api/memory/status', Object.keys(params).length ? params : undefined);
  },
  // Flotte du cockpit (#541, remplace le balayage 2×N que `loadFleet`
  // faisait ici même par projet — voir `project_health.py::fleet_status`) :
  // santé + mémoire (mode rapide) de TOUT le registre en une seule réponse.
  fleet: () => get('/api/fleet'),
  blueprints: () => get('/api/blueprints'),
  primitives: () => get('/api/primitives'),
  features: () => get('/api/features'),
  stigmergy: () => get('/api/stigmergy'),
  eventsLog: () => get('/api/events/log'),
  otel: () => get('/api/otel'),
  // Runs de flow (#506) — distinct du TraceLedger qu'`otel()` lit : un
  // `grimoire upgrade-flow run` n'y écrit jamais de span. `blueprint` filtre
  // (ex. `project-upgrade`) ; omis, tous les flows du projet. `project`
  // cible un AUTRE projet que celui déjà résolu par l'hôte (restes #510/
  // #513) — nécessaire à la fiche Piloter d'un projet consulté depuis la
  // Flotte, où `host.project` (que `withProject` ajouterait sinon) reste
  // celui de la navigation d'origine, pas celui de la fiche affichée.
  flowRuns: (blueprint, project) => {
    const params = { ...(blueprint ? { blueprint } : {}), ...(project ? { project } : {}) };
    return get(WS + 'flows/runs', Object.keys(params).length ? params : undefined);
  },

  // ── Vue de travail ────────────────────────────────────────────────────────
  // Le pouls (UI-3) : « quelque chose a-t-il changé depuis `since` ? ». Sans
  // changement la réponse est minuscule (`changed: false`) ; sinon elle porte
  // le compte des décisions en attente. `scope: 'portfolio'` agrège tout le
  // registre du cockpit. `signal` annule le sondage quand l'onglet est masqué.
  pulse: (since, scope, signal) => {
    const params = { ...(since ? { since } : {}), ...(scope ? { scope } : {}) };
    return get(WS + 'pulse', Object.keys(params).length ? params : undefined, signal ? { signal } : undefined);
  },
  // Le même pouls en flux (P10) : l'URL que `live.js` ouvre en `EventSource`
  // (qui ne passe pas par `fetch`), avec la même cible que toute lecture.
  pulseStreamUrl: (since, scope) => {
    const params = new URLSearchParams({ ...(since ? { since } : {}), ...(scope ? { scope } : {}) });
    const query = params.toString();
    return withProject(WS + 'stream' + (query ? '?' + query : ''));
  },
  // Chat de l'orchestrateur (ACP2). Le flux passe par `EventSource` (pas par `fetch`) : seule
  // l'URL vit ici, avec la même cible que toute lecture. Un message est un tour humain : le
  // cockpit le relaie à l'agent et n'écrit rien lui-même (ADR-007).
  chatStreamUrl: (resume) => withProject(WS + 'chat/stream' + (resume ? '?resume=1' : '')),
  chatSend: (text) => post(WS + 'chat/send', { text }),
  chatPermission: (requestId, decision) => post(WS + 'chat/permission', { request_id: requestId, decision }),
  chatCancel: () => post(WS + 'chat/cancel', {}),
  // ACP3 : l'hôte du chat, par projet. Les hôtes absents reviennent grisés, avec leur raison.
  chatHosts: () => get(WS + 'chat/hosts'),
  chatHost: (host) => post(WS + 'chat/host', { host }),
  glossary: () => get(WS + 'glossary'),
  // Ce qui attend une décision humaine (P22), rangé par urgence. `portfolio`
  // lit tout le registre — la portée « Tous les projets » ; `probe` relance
  // les sondes au lieu de lire leur cache (« Réessayer », P32).
  // Une lecture d'attention parcourt tout le registre côté serveur : deux
  // lecteurs simultanés (compteur de la barre, Flotte, À traiter) partagent la
  // même requête en vol au lieu d'en lancer deux. `probe` force une lecture neuve ;
  // `maxAgeMs` accepte une réponse déjà reçue s'il n'est pas plus vieille.
  attention: (portfolio, { probe = false, maxAgeMs = 0 } = {}) => {
    const params = { ...(portfolio ? { scope: 'portfolio' } : {}), ...(probe ? { probe: '1' } : {}) };
    const key = portfolio ? 'portfolio' : 'project';
    if (!probe) {
      const recent = attentionRecent.get(key);
      if (maxAgeMs > 0 && recent && Date.now() - recent.at <= maxAgeMs) return Promise.resolve(recent.data);
      if (attentionInFlight.has(key)) return attentionInFlight.get(key);
    }
    const request = get(WS + 'attention', Object.keys(params).length ? params : undefined);
    request.then((data) => attentionRecent.set(key, { at: Date.now(), data })).catch(() => {});
    if (!probe) {
      attentionInFlight.set(key, request);
      request.finally(() => attentionInFlight.delete(key)).catch(() => {});
    }
    return request;
  },
  // « Depuis ta dernière visite » (P53) : livré, refusé, laissé en attente
  // depuis `since` (ISO, gardé par le navigateur). Une lecture, à part
  // d'`attention` : le premier rendu d'À traiter ne l'attend pas.
  attentionSince: (portfolio, since) =>
    get(WS + 'attention/since', { ...(portfolio ? { scope: 'portfolio' } : {}), since }),
  tasks: (params) => get(WS + 'tasks', params),
  // `project` cible un AUTRE projet que celui déjà résolu par l'hôte — même
  // convention que `health()` : l'inspecteur du portefeuille (#638, lot C)
  // lit la tâche d'un projet du registre qui n'est pas celui de la
  // navigation. Sans argument, le comportement est celui d'avant.
  task: (id, project) => get(WS + 'tasks/' + encodeURIComponent(id), project ? { project } : undefined),
  taskTrace: (id, project) =>
    get(WS + 'tasks/' + encodeURIComponent(id) + '/trace', project ? { project } : undefined),
  taskRecall: (id, project) =>
    get(WS + 'tasks/' + encodeURIComponent(id) + '/recall', project ? { project } : undefined),
  // Préflight d'assignation (P47) : le plan de lancement d'une session worker
  // sur une tâche — modèle, worktree, vérifications, commande équivalente.
  // Lecture seule stricte : rien n'est lancé ni écrit (ADR-008, phase plan),
  // donc permise même sur un hôte en lecture seule.
  taskLaunchPlan: (id, project) =>
    get(WS + 'tasks/' + encodeURIComponent(id) + '/launch-plan', project ? { project } : undefined),
  // Bloc Activité (P69v2) : le fil de la tâche dérivé du ledger (claim,
  // transitions, portes, questions/réponses, notes de progression), et le
  // même fil vu d'une session (l'union des tâches que son claim tient).
  taskFeed: (id, project) =>
    get(WS + 'tasks/' + encodeURIComponent(id) + '/feed', project ? { project } : undefined),
  sessionFeed: (id, project) =>
    get(WS + 'sessions/' + encodeURIComponent(id) + '/feed', project ? { project } : undefined),
  // Portefeuille (#638, lot C) : toutes les tâches de tous les projets du
  // registre cockpit, chacune avec son projet, son claim, sa session et sa
  // priorité — agrégé côté serveur (`workspace_portfolio.py`), jamais une
  // boucle par projet ici. Filtres : `state` (état ledger ou colonne board),
  // `slug` (un projet — `project` est déjà le projet servi par l'hôte),
  // `live` ('1' : sessions dont le journal a moins de `minutes` minutes).
  portfolioTasks: (params) => get(WS + 'portfolio/tasks', params),
  // Vue « Sessions » de Travail (P33) : les sessions d'agent du projet servi, ou de
  // tout le registre (`portfolio`) — état, tâches tenues, dernier signe, consignes
  // en attente, activité des 15 dernières minutes, commande de reprise à copier.
  sessions: (portfolio) => get(WS + 'sessions', portfolio ? { scope: 'portfolio' } : undefined),
  // PR à revoir (P48) : les PR ouvertes, l'état de leur CI et la tâche liée,
  // les plus prêtes d'abord. Lecture seule : le cockpit ne fusionne jamais.
  reviews: (portfolio) => get(WS + 'reviews', portfolio ? { scope: 'portfolio' } : undefined),
  files: (tier) => get(WS + 'files', tier ? { tier } : undefined),
  file: (path) => get(WS + 'file', { path }),
  fileDiff: (path) => get(WS + 'file/diff', { path }),
  // P44 : ce qu'une mise à jour écrirait (par catégorie), et le diff d'un
  // fichier qu'elle toucherait — deux lectures pures, jamais la route qui écrit.
  upgradeChanges: () => get(WS + 'upgrade/changes'),
  upgradeDiff: (path) => get(WS + 'upgrade/diff', { path }),
  fileUsage: (path) => get(WS + 'file/usage', { path }),
  fileHistory: (path) => get(WS + 'file/history', { path }),
  commands: () => get(WS + 'commands'),
  // Panneau « Preuves » du rail (#534) : les tâches du standard gouverné,
  // l'état de leurs gates et le chemin de leur pack — jamais construit ici
  // depuis les fichiers, toujours la même lecture que `grimoire standard
  // verify`, côté serveur.
  evidence: () => get(WS + 'evidence'),
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
  // Configuration (P66) : registre de sûreté des hooks et fournisseurs de
  // modèles, deux lectures (jamais d'écriture : les gestes sont des commandes).
  hooks: () => get(WS + 'hooks'),
  providers: () => get(WS + 'providers'),
  // Options du projet (P75) : taux USD→CAD de la colonne Fait, lu et écrit
  // dans project-context.yaml (section cockpit) par le serveur.
  options: () => get(WS + 'options'),
  optionsWrite: (usdCad) => post(WS + 'options', { usd_cad: usdCad }),
  // I1 : la commande de notification (options.notify.command), son écriture et son essai.
  notify: () => get(WS + 'notify'),
  notifyWrite: (command) => post(WS + 'notify', { command }),
  notifyTest: (command) => post(WS + 'notify/test', command === undefined ? {} : { command }),

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
  // Fiche Piloter agrégée (#548, remplace les sept appels — huit avec le nom
  // du projet — que `piloter.js::loadSheet` faisait un par un) : santé
  // (mode caché, jamais `doctor` complet — mesuré comme le vrai coût),
  // mémoire (mode rapide), agents, propositions, dernier run du wizard,
  // dernier run de flow `project-upgrade`, nom du projet. `project` cible un
  // AUTRE projet que celui déjà résolu — même convention que `health()`.
  sheet: (project) => get(WS + 'sheet', project ? { project } : undefined),
  // Capacité (P71) : WIP, réservoirs d'admission, quota et file des tâches
  // prêtes avec la raison de chaque report — le même plan à blanc que
  // `grimoire queue plan`, côté serveur (workspace_capacity.py), lecture
  // seule. `project` cible un AUTRE projet que celui déjà résolu par l'hôte —
  // même convention que `sheet()` juste au-dessus.
  capacity: (project) => get(WS + 'capacity', project ? { project } : undefined),
  // Agrégation mémoire multi-projets (#172, dernier volet de « du générateur
  // statique au portefeuille actif ») : `projects` vaut 'all', une liste
  // 'slug1,slug2', ou est omis pour « ce projet » seul — jamais toute la
  // flotte par défaut. Lecture seule, aucune fusion de stores.
  memoryOverview: (projects) => get(WS + 'memory/overview', projects ? { projects } : undefined),
  memorySearch: (q, projects, { includeSuperseded, includeStale } = {}) => {
    const params = projects ? { q, projects } : { q };
    if (includeSuperseded) params.include_superseded = '1';
    if (includeStale) params.include_stale = '1';
    return get(WS + 'memory/search', params);
  },
  // Espace Savoir (P24) : une page d'entrées d'un projet (`slug` du registre,
  // omis = le projet en portée), lue par tranches côté serveur — jamais tout
  // le store ; `offset` est le curseur `nextOffset` de la page précédente.
  memoryEntries: (slug, { kind, types, since, offset, limit, includeSuperseded } = {}) => {
    const params = {};
    if (slug) params.slug = slug;
    if (kind) params.kind = kind;
    if (types) params.types = types;
    if (since) params.since = since;
    if (offset) params.offset = String(offset);
    if (limit) params.limit = String(limit);
    if (includeSuperseded) params.include_superseded = '1';
    return get(WS + 'memory/entries', params);
  },
  // Le graphe complet (P81), en colonnes compactes : jamais échantillonné.
  memoryGraph: () => get(WS + 'memory/graph'),
  // La fiche d'une entrée : provenance, chaîne de remplacement, nœuds liés.
  memoryEntry: (id, slug) => get(WS + 'memory/entry', slug ? { id, slug } : { id }),
  // Santé de la mémoire et file d'hygiène (P25) : une mesure fraîche d'un projet
  // (`slug` du registre, omis = le projet en portée). Lecture seule.
  memoryQuality: (slug) => get(WS + 'memory/quality', slug ? { slug } : undefined),
  // Flotte › Données (P26) : services mémoire de la machine, conteneurs, propriétaires
  // et partages. Lecture seule, mesure fraîche ; demandée à l'ouverture de la vue seulement.
  dataMachine: () => get(WS + 'data/machine'),
  // Premier lancement (P36) : la machine vue par le guide — détection, profils
  // mémoire possibles ou non et pourquoi, plan, commandes à copier. Lecture seule.
  setupGuide: () => get(WS + 'setup/guide'),
  // Premier lancement (P45) : l'avancement enregistré du « Lancer » du guide, et ses
  // verbes (`start` avec `profile`, `next`, `retry`/`skip` avec `step`, `rollback`,
  // `reset`). Chaque étape passe par une action typée de P40 (plan puis apply).
  setupLaunchState: () => get(WS + 'setup/launch'),
  setupLaunch: (verb, body) => post(WS + 'setup/launch', { verb, ...(body || {}) }),
  // Reçu de rappel (P27) : ce que la mémoire a servi à une tâche (`tasks/<id>/receipts`),
  // ou les derniers reçus du projet (`memory/recall`, `task` pour filtrer). Lecture seule.
  taskReceipts: (id, project) =>
    get(WS + 'tasks/' + encodeURIComponent(id) + '/receipts', project ? { project } : undefined),
  memoryRecall: (slug, task) => {
    const params = {};
    if (slug) params.slug = slug;
    if (task) params.task = task;
    return get(WS + 'memory/recall', params);
  },
  // Rejouer (P27) : `receipt` (clé d'un reçu) ou `q` (requête libre) et `base`
  // (la liste du passage précédent, identifiants dans l'ordre servi). Un calcul
  // en GET : le serveur n'écrit rien, pas même un reçu de lecture.
  memoryReplay: ({ receipt, task, q, base, slug, project } = {}) => {
    const params = {};
    if (receipt) params.receipt = receipt;
    if (task) params.task = task;
    if (q) params.q = q;
    if (base != null) params.base = base.join(',');
    if (slug) params.slug = slug;
    if (project) params.project = project;
    return get(WS + 'memory/recall/replay', params);
  },
  // Suppression gouvernée du cockpit (`POST /api/memory`) : confirmation
  // explicite, et le serveur la refuse (403) hors du projet qu'il sert.
  memoryDelete: (id, project) => post('/api/memory', { action: 'delete', id, project, confirm: true }),
  // Correction directe d'une entrée (P34), même route et mêmes gardes :
  // `mode: 'preview'` rend l'avant/après et la version des entrées en jeu ;
  // `mode: 'apply'` renvoie cette version avec `confirm: true` (409 et aperçu
  // recalculé si une entrée a changé entre-temps).
  memoryGesture: (body, project) => post('/api/memory', { action: 'gesture', project, ...body }),

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
  // Apply d'assignation (P47 lot 3) : lance LA session que le plan a montrée —
  // l'empreinte du plan voyage dans le corps, le serveur refuse si l'état a
  // changé depuis (ADR-008). `post` : garde d'écriture commune comprise.
  taskLaunch: (id, empreinte) =>
    post(WS + 'tasks/' + encodeURIComponent(id) + '/launch', { empreinte }),
  // « Nouvelle tâche » (P35) : `TaskService.add` du projet servi, comme
  // `grimoire task add`. `post`, pas `postOpen` : aucune dérogation à P02.
  taskAdd: ({ title, acceptance, description }) =>
    post(WS + 'tasks/add', { title, acceptance, description }),
  // « Une idée ? » (I2) : propositions de l'intake déterministe (aucune écriture), puis création des cochées.
  taskIntake: (text) => post(WS + 'tasks/intake', { text }),
  taskIntakeCreate: (tasks) => post(WS + 'tasks/intake/create', { tasks }),
  // Action depuis le portefeuille (#638, lot C) : routée vers le projet
  // propriétaire par `?project=<slug>` explicite dans le chemin (donc jamais
  // doublé par `withProject`) et confirmée par `project` dans le corps —
  // le serveur refuse si les deux ne désignent pas la même racine. `postOpen`,
  // pas `post` : dérogation nommée (`is_registry_scoped_write`), ouverte à
  // tout projet du registre comme `migrateStandardTasks`, gate de preuve
  // compris côté `TaskService` du projet visé (ADR-007).
  portfolioTaskAction: (id, action, body, project) => {
    if (!project) {
      return Promise.reject(new Error('portfolioTaskAction: project manquant'));
    }
    return postOpen(
      WS + 'portfolio/tasks/' + encodeURIComponent(id) + '/' + action +
        '?project=' + encodeURIComponent(project),
      { ...(body || {}), project },
    );
  },
  // Bouton « Migrer les tâches » de l'espace Exécuter (ADR-007, issue #559,
  // #560) : importe dans le Mission Ledger les tâches d'un board du standard
  // scaffoldé avant le lot 4.1, ou jamais migré — `tasks_view()` ne l'expose
  // (`board.migration_available`) que quand c'est effectivement le cas.
  // `postOpen`, pas `post` : cette écriture ouvre la même porte que
  // `updateProject`/`proposalAction` pour n'importe quel projet du registre
  // (`is_registry_scoped_write` côté serveur), pas seulement celui de
  // lancement direct — le verrou `readOnly` général la refuserait par erreur.
  // Aucun `project` explicite dans la requête : `withProject` (ci-dessus)
  // ajoute déjà le slug du projet affiché tant qu'aucun n'est présent dans le
  // chemin, sans jamais le dupliquer (piège #507).
  migrateStandardTasks: () => postOpen(WS + 'tasks/migrate-standard', {}),
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
  // Les trois questions du wizard (#888) — pile, qui travaille, domaine —
  // pré-remplies par la détection du projet servi, et l'archétype déduit par
  // le même module que la CLI (`grimoire.cli.init_questions`). `answers`
  // ({ stack: 'a,b', who, domain }) remplace les défauts ; `prefill: false`
  // ignore la détection (formulaire « nouveau projet », autre dossier).
  setupQuestions: (answers, prefill = true) =>
    get('/api/setup/questions', { ...(answers || {}), ...(prefill ? {} : { prefill: '0' }) }),
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

  // Entretien du registre des projets (P35) : l'état de la machine, pas un
  // dépôt — d'où `postOpen`, comme `grimoire cockpit add|scan|remove|prune`.
  // Retirer et purger rendent un aperçu sans `confirm: true` et ne touchent
  // jamais au dossier d'un projet, seulement à son entrée du registre.
  registryAdd: (path) => postOpen('/api/projects/add', { path }),
  registryScan: (root, depth) => postOpen('/api/projects/scan', { root, ...(depth ? { depth } : {}) }),
  registryRemove: (slug, confirm = false) => postOpen('/api/projects/remove', { slug, confirm }),
  registryPrune: ({ confirm = false, stale = false, slugs } = {}) =>
    postOpen('/api/projects/prune', { confirm, stale, ...(slugs ? { slugs } : {}) }),
};

export default api;
