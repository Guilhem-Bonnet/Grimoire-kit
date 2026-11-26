// Pilotage — la logique pure (aucun DOM, aucune requête) : quels gestes proposer, ce que chaque dialogue dit.
// Séparée de `pilotage.js` pour se tester sous Node (`tests/js/pilotage.test.mjs`).

/** « 14 h 02 », heure locale ; `''` si la date n'est pas lisible. */
export function clockText(iso) {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return '';
  return `${moment.getHours()} h ${String(moment.getMinutes()).padStart(2, '0')}`;
}

/** La pause qui s'applique au projet : la sienne d'abord, sinon celle de la machine ; `null` sans pause. */
export function appliedPause(pause) {
  return (pause && (pause.project || pause.machine)) || null;
}

/** Qui, quand, pourquoi : une ligne lisible pour une pause. */
export function pauseMeta(flag) {
  if (!flag) return '';
  const parts = [`posée par ${flag.author}`];
  const at = clockText(flag.since);
  if (at) parts.push(`à ${at}`);
  if (flag.reason) parts.push(`motif : ${flag.reason}`);
  return parts.join(' · ');
}

/**
 * Les gestes proposés pour un état, dans l'ordre d'affichage. `scope` : 'project' (fiche) ou 'row' (Flotte).
 * Une pause de la machine ne se lève que depuis « Tous les projets » : la fiche le dit au lieu de proposer un geste
 * qui toucherait tous les projets.
 */
export function availableGestures(state, scope = 'project') {
  if (!state) return [];
  const out = [];
  const mine = state.pause && state.pause.project;
  if (mine) {
    out.push({ id: 'resume', label: 'Reprendre', icon: 'play' });
    if (mine.mode !== 'hold' && scope === 'project') out.push({ id: 'hold', label: 'Arrêter net', icon: 'stop' });
  } else if (!(state.pause && state.pause.machine)) {
    out.push({ id: 'drain', label: 'Mettre en pause', icon: 'hourglass' });
    if (scope === 'project') out.push({ id: 'hold', label: 'Arrêter net', icon: 'stop' });
  }
  if (scope === 'project' && state.breaker) {
    out.push(state.breaker.open
      ? { id: 'reset', label: 'Refermer le disjoncteur', icon: 'refresh' }
      : { id: 'trip', label: 'Ouvrir le disjoncteur', icon: 'bolt', quiet: true });
  }
  return out;
}

/** Le texte d'une confirmation : ce qui sera fait, et ce que la pause en place devient. */
export function confirmLines(gesture, state, name) {
  const mine = state && state.pause && state.pause.project;
  const lines = [];
  if (gesture === 'drain') lines.push(`Rien de nouveau n’est admis dans « ${name} » ; les sessions en cours finissent leur tâche.`);
  if (gesture === 'hold') {
    lines.push(`Rien de nouveau n’est admis dans « ${name} ». Sur un hôte dont le frein est prouvé, les outils qui modifient sont refusés aux sessions en cours (sauf le point de reprise) ; sur un hôte sans frein, le résultat est celui d’une pause.`);
  }
  if (gesture === 'resume' && mine) {
    lines.push(`Cette pause est ${pauseMeta(mine)}. La lever rend l’admission à « ${name} ».`);
    if (mine.usage_rule) lines.push('La règle d’usage peut la reposer si son seuil est encore dépassé.');
  }
  if ((gesture === 'drain' || gesture === 'hold') && mine) {
    lines.push(`Une pause ${mine.mode} est déjà en place, ${pauseMeta(mine)} : elle sera remplacée.`);
  }
  if (gesture === 'reset') {
    const breaker = state.breaker;
    lines.push(`Ouvert${breaker.source ? ` (${breaker.source})` : ''} : ${breaker.reason || 'motif non noté'}.`);
    lines.push('Refermer remet le compteur d’échecs à zéro et autorise de nouveaux lancements.');
  }
  if (gesture === 'trip') lines.push(`Plus aucune session ne sera lancée dans « ${name} » jusqu’à ce que vous refermiez le disjoncteur.`);
  return lines;
}

/**
 * La phrase que le serveur rend d'un geste accompli (« Pause posée par … levée. »), et la mention du journal quand il
 * n'a pas pu inscrire l'événement.
 */
export function gestureNotice(answer) {
  const said = (answer && answer.notice) || '';
  return answer && answer.journaled === false ? `${said} Le journal d’autorité n’a pas pu l’inscrire.`.trim() : said;
}

/**
 * La note du dernier geste. Elle appartient au bloc, pas à son rendu : un rafraîchissement au pouls repeint tout le
 * bloc, et la note doit y SURVIVRE jusqu'au geste suivant (sinon l'humain ne la lit jamais, le pouls arrive en
 * quelques dixièmes de seconde après un geste).
 */
export function noteKeeper() {
  let text = '';
  return { set(value) { text = value || ''; }, clear() { text = ''; }, get() { return text; } };
}

/** L'état du pilote automatique et du mandat d'un projet, en une ligne (`authority` : la lecture de la ligne). */
export function authorityLine(authority) {
  if (!authority || authority.error || !authority.autopilot || !authority.mandate) {
    return 'Pilote automatique et mandat : lecture impossible, rien n’est deviné.';
  }
  const { autopilot, mandate } = authority;
  const pilot = autopilot.active
    ? `Pilote automatique : activé${clockText(autopilot.since) ? ` depuis ${clockText(autopilot.since)}` : ''}`
    : 'Pilote automatique : coupé';
  const until = mandate.until ? ` jusqu’au ${String(mandate.until).slice(0, 10)}` : '';
  const grant = mandate.active ? `Mandat : actif${until}` : `Mandat : inactif${mandate.reason ? ` (${mandate.reason})` : ''}`;
  return `${pilot} · ${grant}`;
}

/**
 * Le bouton du pilote d'une ligne : `{ on, label, disabled, why }`. `on` est l'état visé par le clic. Le mandat n'est
 * jamais accordé ni révoqué d'ici : sans mandat actif on ne peut qu'éteindre (ou rien), et la raison est écrite.
 */
export function pilotToggle(authority, { readOnly = false } = {}) {
  if (readOnly) return { on: false, label: '', disabled: true, why: 'Cockpit en lecture seule.' };
  if (!authority || authority.error || !authority.autopilot || !authority.mandate) {
    return { on: false, label: '', disabled: true, why: 'Pilote indisponible : état illisible.' };
  }
  if (authority.autopilot.active) return { on: false, label: 'Éteindre le pilote automatique', disabled: false, why: '' };
  if (!authority.mandate.active) {
    return { on: true, label: 'Allumer le pilote automatique', disabled: true, why: 'Aucun mandat actif : il s’accorde depuis le projet.' };
  }
  return { on: true, label: 'Allumer le pilote automatique', disabled: false, why: '' };
}

/** Ce que le dialogue du pilote dit avant de demander la clé : qui est visé, ce qui change, ce qui ne change pas. */
export function pilotConfirmLines(on, name) {
  return on
    ? [`Le pilote automatique de « ${name} » tranchera les cas bloqués de ce projet, sauf la liste réservée à l’humain.`,
      'Il reste lié au mandat actuel ; le mandat lui-même ne change pas.']
    : [`Le pilote automatique de « ${name} » sera coupé : les cas bloqués attendront de nouveau votre réponse.`];
}

/** Ce que la pause de toute la machine arrête, en une phrase qui nomme les projets. */
export function machineStops(rows) {
  const names = rows.map((r) => r.name || r.slug);
  if (!names.length) return 'Aucun projet enregistré : rien à arrêter.';
  return `Seront mis en pause : ${names.join(', ')} (${names.length} projet${names.length > 1 ? 's' : ''}). Les sessions en cours finissent leur tâche.`;
}
