// La logique pure du mode conversation (mascotte, vocal) : une boucle écoute → (envoi humain) → réponse → écoute.
//
// Rien ici ne touche au micro, au DOM ni au réseau : ce fichier se teste sous Node (`tests/js/conversation.test.mjs`).
// `step(model, event)` rend le nouveau modèle et les EFFETS à exécuter ; le branchement (`conversation.js`) les exécute.
//
// Règles :
// - le mode ne s'ouvre que par un geste explicite (`start`) et ne s'ouvre jamais de lui-même ;
// - l'écoute s'arrête à la fin de la parole (un silence court après avoir parlé) ; un souffle bref est ignoré ;
// - rien n'est envoyé ici : le texte dicté attend l'envoi humain (`review`), puis `sent` ouvre l'attente de la réponse ;
// - l'arrêt est immédiat à tout moment (`stop`, Échap, perte de focus de l'onglet, une permission qui attend) ;
// - un silence prolongé (rien dit à l'écoute, ou rien envoyé après la dictée) ferme le mode.

/** Les états : `off`, `listen` (micro ouvert), `transcribe`, `review` (texte dicté, à relire et envoyer), `think`, `speak`. */
export const MODES = ['off', 'listen', 'transcribe', 'review', 'think', 'speak'];

/** Niveau (0..1, comme `levelFromSamples`) à partir duquel on tient pour de la parole. */
export const SPEECH_LEVEL = 0.08;
/** Silence qui clôt une phrase après avoir parlé. */
export const END_SILENCE_MS = 1400;
/** Une parole plus courte que cela (un bruit, un clic) n'est pas une phrase. */
export const MIN_SPEECH_MS = 300;
/** À l'écoute sans que personne ne parle : le mode se ferme. */
export const NO_SPEECH_MS = 15000;
/** Texte dicté, rien envoyé : le mode se ferme. */
export const REVIEW_MAX_MS = 60000;
/** Un tour qui ne rend jamais la main : le mode se ferme. */
export const THINK_MAX_MS = 600000;
/** Dictées vides de suite avant de fermer. */
export const EMPTY_LIMIT = 2;
/** Le raccourci : Alt+Maj+V. */
export const SHORTCUT_LABEL = 'Alt+Maj+V';

export function initial() {
  return { mode: 'off', since: 0, spokeAt: 0, lastVoiceAt: 0, empties: 0, pendingVoice: null, reason: '' };
}

/** Le raccourci Alt+Maj+V (sans Ctrl ni Méta), par la touche physique : indépendant de la disposition du clavier. */
export function isShortcut(event) {
  return Boolean(event) && event.altKey === true && event.shiftKey === true && !event.ctrlKey && !event.metaKey
    && (event.code === 'KeyV' || event.key === 'V' || event.key === 'v');
}

/** Pourquoi le mode s'est fermé, en clair (annoncé à la personne). */
export const REASONS = {
  stop: 'Conversation arrêtée.',
  escape: 'Conversation arrêtée (Échap).',
  focus: 'Conversation arrêtée : l’onglet a perdu le focus, le micro est fermé.',
  silence: 'Conversation fermée après un silence prolongé.',
  permission: 'Conversation arrêtée : une permission attend ton clic.',
  error: 'Conversation arrêtée : la dictée a échoué.',
  mic: 'Conversation arrêtée : le micro n’a pas pu s’ouvrir.',
  unavailable: 'Conversation arrêtée : le vocal n’est plus disponible.',
};

/** Le libellé visible de chaque état. */
export function captionFor(mode) {
  return {
    off: '', listen: 'je t’écoute', transcribe: 'transcription…', review: 'relis puis envoie', think: 'réfléchit', speak: 'parle',
  }[mode] || '';
}

/** L'action de la mascotte pendant la conversation (`null` : elle suit ses états habituels). */
export function mascotteActionFor(mode) {
  return { listen: 'ecoute', transcribe: 'reflechit', think: 'reflechit', speak: 'parle' }[mode] || null;
}

const to = (model, mode, now, extra = {}) => ({ ...model, mode, since: now, ...extra });
const closed = (model, reason) => ({ ...initial(), reason, pendingVoice: null, empties: 0, spokeAt: 0, lastVoiceAt: 0, since: model.since });
const listening = (model, now) => to(model, 'listen', now, { spokeAt: 0, lastVoiceAt: 0 });

/**
 * Un pas de la machine. Événements : `start {now, read}`, `level {now, level}`, `tick {now}`, `transcribed {now, text}`
 * (`text` vide : rien reconnu ; `null` : échec), `sent {now}`, `voice {id}` (un message parlé est arrivé),
 * `turn-ended {now, voice}`, `play-ended {now}`, `permission`, `stop {reason}`.
 * Effets : `{ do: 'record' | 'finish' | 'abort-recording' | 'read' | 'close', id?, reason? }`.
 */
export function step(model, event) {
  const m = model;
  const now = event.now ?? 0;
  const none = { model: m, effects: [] };
  if (event.type === 'start') {
    if (m.mode !== 'off') return none;
    if (event.read) return { model: to({ ...initial() }, 'speak', now), effects: [{ do: 'read' }] };
    return { model: listening({ ...initial() }, now), effects: [{ do: 'record' }] };
  }
  if (m.mode === 'off') return none;
  switch (event.type) {
    case 'stop':
      return { model: closed(m, event.reason || 'stop'), effects: [{ do: 'close', reason: event.reason || 'stop' }] };
    case 'permission':
      return { model: closed(m, 'permission'), effects: [{ do: 'close', reason: 'permission' }] };
    case 'level': {
      if (m.mode !== 'listen' || !(event.level >= SPEECH_LEVEL)) return none;
      return { model: { ...m, spokeAt: m.spokeAt || now, lastVoiceAt: now }, effects: [] };
    }
    case 'tick': {
      if (m.mode === 'listen') {
        if (m.spokeAt && now - m.lastVoiceAt >= END_SILENCE_MS) {
          if (m.lastVoiceAt - m.spokeAt < MIN_SPEECH_MS) return { model: { ...m, spokeAt: 0, lastVoiceAt: 0 }, effects: [] }; // un bruit
          return { model: to(m, 'transcribe', now), effects: [{ do: 'finish' }] };
        }
        if (!m.spokeAt && now - m.since >= NO_SPEECH_MS) return { model: closed(m, 'silence'), effects: [{ do: 'close', reason: 'silence' }] };
      } else if (m.mode === 'review' && now - m.since >= REVIEW_MAX_MS) {
        return { model: closed(m, 'silence'), effects: [{ do: 'close', reason: 'silence' }] };
      } else if (m.mode === 'think' && now - m.since >= THINK_MAX_MS) {
        return { model: closed(m, 'silence'), effects: [{ do: 'close', reason: 'silence' }] };
      }
      return none;
    }
    case 'transcribed': {
      if (m.mode !== 'transcribe' && m.mode !== 'listen') return none;
      if (event.text === null) return { model: closed(m, 'error'), effects: [{ do: 'close', reason: 'error' }] };
      if (String(event.text).trim()) return { model: to(m, 'review', now, { empties: 0 }), effects: [] };
      const empties = m.empties + 1;
      if (empties >= EMPTY_LIMIT) return { model: closed(m, 'silence'), effects: [{ do: 'close', reason: 'silence' }] };
      return { model: listening({ ...m, empties }, now), effects: [{ do: 'record' }] };
    }
    case 'sent': {
      // Un message parlé d'avant l'envoi n'est pas la réponse : on ne retient que ce qui arrive ensuite.
      if (m.mode === 'review') return { model: to({ ...m, pendingVoice: null }, 'think', now), effects: [] };
      if (m.mode === 'listen') return { model: to({ ...m, pendingVoice: null }, 'think', now), effects: [{ do: 'abort-recording' }] };
      return none;
    }
    case 'voice':
      return { model: { ...m, pendingVoice: event.id ?? null }, effects: [] };
    case 'turn-ended': {
      if (m.mode !== 'think') return none;
      const id = event.voice ?? m.pendingVoice;
      if (id !== null && id !== undefined) return { model: to({ ...m, pendingVoice: null }, 'speak', now), effects: [{ do: 'read', id }] };
      return { model: listening(m, now), effects: [{ do: 'record' }] };
    }
    case 'play-ended':
      if (m.mode !== 'speak') return none;
      return { model: listening(m, now), effects: [{ do: 'record' }] };
    default:
      return none;
  }
}
