// Le mode conversation de la mascotte (chat d'un projet et Atelier) : parler sans clavier, quand le vocal est actif.
//
// Un geste explicite l'ouvre (le bouton « Converser » ou Alt+Maj+V) ; ensuite la boucle est écoute (la dictée
// existante, `voice.js`) → texte dicté dans la saisie → ENVOI PAR LA PERSONNE (clic ou Entrée : rien n'est envoyé
// ici, et une dictée ne déclenche jamais un geste de l'assistant) → réponse lue (la synthèse existante) → écoute.
// La machine d'états est pure (`conversation_logic.js`, testée sous Node) ; ce fichier l'exécute.
//
// Garde-fous :
// - le micro ne s'ouvre qu'après le geste, par la dictée existante ; un indicateur « Micro ouvert » reste visible
//   tant qu'il l'est ; rien n'est enregistré hors du mode ; ce module n'ouvre jamais de flux lui-même : son analyseur
//   de niveau se branche sur celui de la dictée (`micOpened`) ;
// - arrêt immédiat : le bouton, Échap, la perte de focus de l'onglet (onglet masqué ou fenêtre qui perd le focus), une
//   permission qui attend ; sortie automatique après un silence prolongé ;
// - états annoncés poliment (`aria-live="polite"`), tout au clavier, aucune route réseau nouvelle.

import { MODES, REASONS, SHORTCUT_LABEL, captionFor, initial, isShortcut, mascotteActionFor, step } from './conversation_logic.js';

const POLL_MS = 100;
const registry = new Set();
let shortcutBound = false;
let startedAt = -1e9; // le dernier démarrage, pour ne pas confondre l'invite du micro avec une perte de focus

function visible(node) {
  return node.isConnected && node.getClientRects().length > 0;
}

function bindShortcut() {
  if (shortcutBound) return;
  shortcutBound = true;
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      for (const conversation of registry) if (conversation.active()) conversation.stop('escape');
      return;
    }
    if (!isShortcut(event)) return;
    const owner = [...registry].reverse().find((conversation) => conversation.available() && visible(conversation.element));
    if (!owner) return;
    event.preventDefault();
    owner.toggle();
  });
  const lost = () => { for (const conversation of registry) if (conversation.active()) conversation.stop('focus'); };
  document.addEventListener('visibilitychange', () => { if (document.hidden) lost(); });
  // L'invite d'autorisation du micro peut faire perdre le focus un instant : la fenêtre qui s'ouvre n'arrête pas le mode.
  window.addEventListener('blur', () => {
    if (performance.now() - startedAt < 2000) return;
    lost();
  });
}

/**
 * `recorder` : la dictée existante (`start()` rend vrai si le micro s'est ouvert, `stop()` transcrit, `abort()` coupe sans
 * transcrire, `isRecording()`, `available()`). `read(id)` lit la réponse et rend vrai si la lecture a démarré (la fin est
 * rapportée par `event({ type: 'play-ended' })`) ; `stopReading()` la coupe. `firstRead()` : vrai si le mode doit d'abord
 * lire quelque chose (la question de l'assistant de l'Atelier). `onMode(mode, action)` : l'état, et l'action de la mascotte.
 */
export function createConversation({
  recorder, read, stopReading = () => {}, firstRead = () => false, onMode = () => {}, onClose = () => {},
  unavailableReason = () => '',
}) {
  const element = document.createElement('div');
  element.className = 'conv';
  element.setAttribute('data-role', 'conversation');
  const toggleButton = document.createElement('button');
  toggleButton.type = 'button';
  toggleButton.className = 'conv-toggle';
  toggleButton.setAttribute('data-act', 'conversation');
  toggleButton.setAttribute('aria-pressed', 'false');
  toggleButton.setAttribute('aria-keyshortcuts', 'Alt+Shift+V');
  toggleButton.title = `Mode conversation : parler sans clavier (${SHORTCUT_LABEL}). Échap arrête.`;
  toggleButton.textContent = 'Converser';
  const mic = document.createElement('span');
  mic.className = 'conv-mic';
  mic.setAttribute('data-role', 'conversation-mic');
  mic.textContent = 'Micro ouvert';
  mic.hidden = true;
  const status = document.createElement('span');
  status.className = 'conv-status';
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('data-role', 'conversation-status');
  // Vocal indisponible : le bouton reste, désactivé (`aria-disabled`, donc atteignable au clavier), avec la raison et le geste.
  const note = document.createElement('span');
  note.className = 'conv-note';
  note.id = `conv-note-${Math.random().toString(36).slice(2, 8)}`;
  note.setAttribute('data-role', 'conversation-note');
  note.hidden = true;
  element.append(toggleButton, mic, status, note);

  let model = initial();
  let analyser = null;
  let samples = null;
  let timer = 0;
  let destroyed = false;
  const api = {};

  function paint() {
    const on = model.mode !== 'off';
    toggleButton.setAttribute('aria-pressed', String(on));
    toggleButton.textContent = on ? 'Arrêter la conversation' : 'Converser';
    element.dataset.mode = model.mode;
    const text = on ? captionFor(model.mode) : '';
    if (on && status.textContent !== text) status.textContent = text;
  }

  function sampleLevel() {
    if (!analyser) return 0;
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    return Math.min(1, Math.sqrt(sum / samples.length) * 5);
  }

  async function run(effects) {
    for (const effect of effects) {
      if (effect.do === 'record') {
        const ok = await recorder.start();
        if (!ok) api.event({ type: 'stop', reason: 'mic' });
      } else if (effect.do === 'finish') {
        recorder.stop();
      } else if (effect.do === 'abort-recording') {
        recorder.abort();
      } else if (effect.do === 'read') {
        let started = false;
        try { started = Boolean(await read(effect.id)); } catch { started = false; }
        if (!started) api.event({ type: 'play-ended' });
      } else if (effect.do === 'close') {
        clearInterval(timer);
        timer = 0;
        recorder.abort();
        stopReading();
        status.textContent = REASONS[effect.reason] || REASONS.stop;
        try { onClose(effect.reason); } catch { /* sans effet sur l'arrêt */ }
      }
    }
  }

  api.event = (event) => {
    if (destroyed) return;
    const out = step(model, { now: performance.now(), ...event });
    const before = model.mode;
    model = out.model;
    if (model.mode !== before) {
      paint();
      try { onMode(model.mode, mascotteActionFor(model.mode)); } catch { /* l'affichage n'arrête pas la boucle */ }
    }
    run(out.effects);
  };

  api.element = element;
  api.active = () => model.mode !== 'off';
  api.mode = () => model.mode;
  api.available = () => Boolean(recorder.available());
  api.start = () => {
    if (api.active() || !api.available()) return;
    for (const other of registry) if (other !== api && other.active()) other.stop('stop'); // un seul micro à la fois
    status.textContent = '';
    startedAt = performance.now();
    api.event({ type: 'start', read: Boolean(firstRead()) });
    clearInterval(timer);
    timer = setInterval(() => {
      if (model.mode === 'listen') api.event({ type: 'level', level: sampleLevel() });
      api.event({ type: 'tick' });
    }, POLL_MS);
  };
  api.stop = (reason = 'stop') => { if (api.active()) api.event({ type: 'stop', reason }); };
  api.toggle = () => (api.active() ? api.stop('stop') : api.start());
  /** À appeler par l'hôte : la dictée vient de rendre son texte (`''` rien, `null` échec). */
  api.text = (text) => api.event({ type: 'transcribed', text });
  /** Le micro de la dictée vient de s'ouvrir : un analyseur de niveau se branche sur CE flux (jamais un autre). */
  api.micOpened = ({ context, source }) => {
    mic.hidden = false;
    try {
      analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      samples = new Float32Array(analyser.fftSize);
    } catch { analyser = null; }
  };
  api.micClosed = () => {
    mic.hidden = true;
    if (analyser) { try { analyser.disconnect(); } catch { /* déjà détaché */ } }
    analyser = null;
  };
  /** Montre ou cache le bouton selon que le vocal est actif. */
  api.sync = () => {
    const ok = api.available();
    toggleButton.setAttribute('aria-disabled', String(!ok));
    if (ok) toggleButton.removeAttribute('aria-describedby');
    else toggleButton.setAttribute('aria-describedby', note.id);
    note.hidden = ok;
    note.textContent = ok ? '' : `Vocal indisponible : ${unavailableReason() || 'raison inconnue'}. `
      + 'Pour l’activer : interaction.mode: voice dans project-context.yaml, puis installer le moteur '
      + '(pip install \'grimoire-kit[voice]\').';
    if (!ok && api.active()) api.stop('unavailable');
  };
  api.destroy = () => {
    api.stop('stop');
    destroyed = true;
    clearInterval(timer);
    registry.delete(api);
  };
  api.modes = MODES;

  toggleButton.addEventListener('click', () => { if (api.available() || api.active()) api.toggle(); });
  registry.add(api);
  bindShortcut();
  return api;
}
