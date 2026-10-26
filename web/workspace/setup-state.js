// L'état d'un projet servi, S0 à S3 (P36) — lu, jamais deviné.
//
// Module pur : aucun `fetch`, aucun DOM. Le guide du cockpit (`spaces/
// demarrer.js`) lui passe les réponses de `api.js` ; la vitrine (`web/
// index.html`), servie par le cockpit, lui passe les siennes. Les deux pages
// disent donc le même état, avec la même source.
//
// Quatre lectures, toutes existantes et toutes sans écriture :
//   /api/status         l'hôte (cockpit), le projet servi, la lecture seule ;
//   /api/health         `kit.scaffolded` : le dossier est-il un projet Grimoire ;
//   /api/setup/run      le dernier journal du wizard (`ok`, `doctorOk`) ;
//   /api/memory/status  SANS sonde : le dernier statut sondé, ou la config seule.
//
// Les états, du plus grave au plus calme — le premier qui s'établit gagne :
//   S0  pas initialisé        `kit.scaffolded` faux ;
//   S1  initialisation en échec  le journal dit `ok` faux ou `doctorOk` faux ;
//   S2  mémoire non liée      aucune recherche sémantique en service : backend
//                             injoignable, ou lexical par configuration ou de fait ;
//   S3  prêt                  la mémoire sondée répond et fusionne vecteurs et lexical.
// Et un cinquième, qui n'est pas un repli poli : `inconnu`. Une lecture ratée,
// ou une mémoire jamais sondée, ne vaut jamais S0 ni S3 — on dit ce qui manque.

/** Backends lexicaux par construction : aucun service à joindre, aucune fusion. */
const LEXICAL = new Set(['local', 'lexical', 'tantivy-local']);

export const ROUTES = {
  status: '/api/status',
  health: '/api/health',
  setupRun: '/api/setup/run',
  memory: '/api/memory/status',
};

export const STATES = {
  S0: { label: 'Pas initialisé', tone: 'warn', title: "Ce dossier n'est pas encore un projet Grimoire." },
  S1: { label: 'Initialisation en échec', tone: 'bad', title: "La dernière initialisation n'est pas allée au bout." },
  S2: { label: 'Mémoire non liée', tone: 'acc', title: 'Ta mémoire tourne en lexical seul.' },
  S3: { label: 'Prêt', tone: 'ok', title: 'Le projet est initialisé et sa mémoire répond.' },
  inconnu: { label: 'Inconnu', tone: '', title: "L'état de ce projet n'a pas pu être lu." },
};

const failed = (read) => !read || read.ok !== true;
const why = (read) => (read && read.error ? String(read.error) : 'pas de réponse');

function make(id, source, extra = {}) {
  return { id, ...STATES[id], source, ...extra };
}

function unknown(route, reason, extra = {}) {
  return make('inconnu', `${route} : ${reason}`, { reason, ...extra });
}

/** Un horodatage ISO dit à la française, à l'heure locale ; tel quel s'il est illisible. */
function when(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
}

/** S2 ou S3 d'après la mémoire lue sans sonde ; `null` si on ne peut pas trancher. */
function memoryState(memory) {
  const configured = memory.configuredBackend;
  if (memory.probed === true) {
    const at = memory.probedAt ? `, sondée le ${when(memory.probedAt)}${memory.stale ? ' (périmée)' : ''}` : '';
    if (memory.state !== 'ok' || memory.available !== true) {
      return make('S2', `${ROUTES.memory} · state = ${memory.state}${at}`, {
        sentence: 'Aucun backend mémoire ne répond : seule la recherche lexicale sert.',
      });
    }
    const served = memory.retrieval && memory.retrieval.in_service;
    if (served === 'lexical' || LEXICAL.has(memory.resolvedBackend)) {
      return make('S2', `${ROUTES.memory} · retrieval.in_service = lexical${at}`, {
        sentence: 'La mémoire répond, mais ne fusionne aucun classement vectoriel.',
      });
    }
    return make('S3', `${ROUTES.memory} · state = ok, backend ${memory.resolvedBackend || configured}${at}`);
  }
  if (configured && LEXICAL.has(configured)) {
    return make('S2', `${ROUTES.memory} · configuredBackend = ${configured}`, {
      sentence: 'Le backend configuré est lexical : aucun service à joindre, aucune recherche sémantique.',
    });
  }
  return null;
}

/**
 * Classe un projet d'après ses quatre lectures. Chaque lecture vaut
 * `{ ok: true, data }` ou `{ ok: false, error }`.
 * Rend `{ id, label, tone, title, source, sentence?, reason?, failedSteps? }`.
 */
export function classify({ status, health, setupRun, memory } = {}) {
  if (failed(status)) return unknown(ROUTES.status, why(status));
  if (failed(health)) return unknown(ROUTES.health, why(health));
  const kit = health.data && health.data.kit;
  if (!kit || typeof kit.scaffolded !== 'boolean') {
    return unknown(ROUTES.health, 'réponse sans kit.scaffolded');
  }
  if (kit.scaffolded === false) {
    return make('S0', `${ROUTES.health} · kit.scaffolded = false`, {
      sentence: 'Rien ne sera écrit tant que tu ne lances pas toi-même la première commande.',
    });
  }
  if (failed(setupRun)) return unknown(ROUTES.setupRun, why(setupRun));
  const run = setupRun.data || {};
  if (run.available === true && (run.ok === false || run.doctorOk === false)) {
    const field = run.ok === false ? 'ok = false' : 'doctorOk = false';
    const failedSteps = (run.steps || []).filter((s) => s && s.status === 'failed');
    return make('S1', `${ROUTES.setupRun} · ${field}`, {
      sentence: run.ok === false
        ? `${failedSteps.length || 'Des'} étape${failedSteps.length > 1 ? 's' : ''} en échec au dernier passage.`
        : 'Le projet est écrit, mais doctor signale des écarts.',
      failedSteps,
    });
  }
  if (failed(memory)) return unknown(ROUTES.memory, why(memory));
  const mem = memory.data || {};
  if (mem.error && mem.probed !== true) return unknown(ROUTES.memory, `config illisible : ${mem.error}`);
  const decided = memoryState(mem);
  if (decided) return decided;
  return unknown(ROUTES.memory, 'mémoire jamais sondée depuis le démarrage du cockpit (lecture sans sonde)', {
    sentence: `Backend configuré : ${mem.configuredBackend || 'aucun'}. « Sonder la mémoire » (Flotte, fiche du projet) tranche entre S2 et S3.`,
  });
}

/** La commande à copier qui fait avancer chaque état. */
export const NEXT_COMMAND = {
  S0: 'grimoire up .',
  S1: 'grimoire doctor . --fix',
  S2: 'grimoire memory up',
  S3: 'grimoire cockpit serve',
  inconnu: 'grimoire doctor .',
};

/**
 * Vrai seulement quand la page est servie par le cockpit du kit : il injecte
 * dans chaque page HTML `<meta name="grimoire-session">`. Sans ce marqueur
 * (vitrine publiée, fichier ouvert depuis le disque, serveur statique
 * quelconque), la page ne fait AUCUNE requête : ni vers localhost, ni vers une API.
 */
export function servedByCockpit(doc = document, loc = location) {
  if (loc.protocol !== 'http:' && loc.protocol !== 'https:') return false;
  return Boolean(doc.querySelector('meta[name="grimoire-session"]'));
}

/** Les quatre lectures, par un `get(chemin)` qui rend une promesse de JSON. */
export async function readAll(get) {
  const keys = Object.keys(ROUTES);
  const settled = await Promise.allSettled(keys.map((k) => get(ROUTES[k])));
  const reads = {};
  keys.forEach((k, i) => {
    const r = settled[i];
    reads[k] = r.status === 'fulfilled'
      ? { ok: true, data: r.value }
      : { ok: false, error: (r.reason && r.reason.message) || String(r.reason) };
  });
  return reads;
}
