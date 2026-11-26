// Atelier de spec : la mascotte incarne l'assistant du cadrage (épique #1197, assistant #1409, mascotte #1408).
//
// C'est le MÊME composant que celle du chat (`mascotte.js` pour le rendu, `mascotte_logic.js` pour les états) : ici
// seul le branchement change. Ce module n'est chargé que si la fonction en test « Mascotte » est allumée
// (`atelier-assistant.js` lit le drapeau), jamais autrement. Il ne dépend pas du vocal.
//
// Les états viennent de ce que l'assistant dit réellement (`GET atelier/chantiers/<slug>/entretien`, relu par la
// barre) et des suggestions en attente du document : repos, réfléchit (requête à l'agent en cours), question ou
// suggestion en attente (avec compteur), erreur. Un clic (ou Entrée / Espace) met la barre de l'assistant au premier
// plan sur ce qui attend : la question, la permission, ou la première suggestion.
//
// Voix : seulement si le vocal est actif (`voice/config` : disponible et en mode voix), un bouton lit la question de
// l'assistant avec la synthèse déjà employée par la boîte vocale du chat (`speakText`, le moteur du navigateur) :
// aucune route nouvelle, aucun son envoyé. Sans vocal, rien de vocal n'apparaît.

import { createMascotte } from '../mascotte.js';
import { assistantState, assistantTarget, simulatedBands, simulatedVoice } from '../mascotte_logic.js';
import { speakText } from '../voice_outbox.js';

function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

/** Le vocal est-il actif ? (`available` et mode voix, comme pour la dictée du chat.) */
async function voiceActive(api) {
  try {
    const config = await api.voiceConfig();
    return Boolean(config) && config.available === true && config.mode === 'voice';
  } catch { return false; }
}

/**
 * `hooks.activate(cible)` : met au premier plan ce que vise le clic (`question`, `permission`, `suggestion`, `panel`).
 * Rend `{ element, update(faits), destroy() }` ; `faits` : `{ error, unavailable, question, permission, suggestions, busy, text }`.
 */
export function createAtelierMascotte(ctx, hooks) {
  const element = el('div', 'at-mascotte', null, { 'data-role': 'assistant-mascotte' });
  const button = el('button', 'at-mascotte-btn', null, { type: 'button', 'data-act': 'mascotte' });
  let stage = el('span', 'at-mascotte-stage', null, { 'aria-hidden': 'true' });
  const count = el('span', 'at-mascotte-count', null, { 'aria-hidden': 'true', 'data-role': 'mascotte-count' });
  count.hidden = true;
  button.append(stage, count);
  const caption = el('span', 'at-mascotte-caption', '', { 'aria-live': 'polite', 'data-role': 'mascotte-caption' });
  const speak = el('button', 'at-btn at-mascotte-speak', 'Lire la question', { type: 'button', 'data-act': 'speak', 'aria-pressed': 'false' });
  speak.hidden = true;
  element.append(button, caption, speak);

  let facts = {};
  let shown = { action: 'repos', caption: '', tone: '' };
  let mascotte = null;
  let voice = false;
  let speaking = null; // { stop } de la lecture en cours
  let destroyed = false;
  const simBands = new Float32Array(8);

  const fallback = (reason) => {
    if (mascotte) { mascotte.destroy(); mascotte = null; }
    const dot = el('span', 'at-mascotte-dot', null, { 'aria-hidden': 'true', title: `Rendu indisponible : ${reason}` });
    stage.replaceWith(dot);
    stage = dot;
    element.dataset.fallback = reason;
  };

  const levels = (t) => {
    if (!speaking) return null;
    const level = simulatedVoice(t) * 0.8;
    return { level, bands: simulatedBands(level, t, simBands) };
  };

  function paint() {
    const state = assistantState(facts);
    const action = speaking ? 'parle' : state.action;
    const text = speaking ? 'lit la question' : state.caption;
    if (caption.textContent !== text) caption.textContent = text; // annoncé seulement quand l'état change
    element.dataset.tone = speaking ? 'speak' : state.tone;
    element.dataset.action = action;
    count.hidden = state.count < 1;
    count.textContent = String(state.count);
    button.setAttribute('aria-label', `Assistant du cadrage : ${text}${state.count > 0 ? ` (${state.count} en attente)` : ''}. Ouvrir l’assistant.`);
    speak.hidden = !(voice && facts.question && facts.text);
    shown = { action, caption: text, tone: state.tone };
    if (mascotte) mascotte.setAction(action);
  }

  function stopSpeaking() {
    const current = speaking;
    speaking = null;
    if (current) { try { current.stop(); } catch { /* déjà arrêtée */ } }
    speak.setAttribute('aria-pressed', 'false');
    speak.textContent = 'Lire la question';
  }

  button.addEventListener('click', () => hooks.activate(assistantTarget(facts)));

  speak.addEventListener('click', () => {
    if (speaking) { stopSpeaking(); paint(); return; }
    if (!facts.text || typeof window.speechSynthesis === 'undefined') return;
    const player = speakText(String(facts.text), 'fr');
    const mine = { stop: player.stop };
    speaking = mine;
    speak.setAttribute('aria-pressed', 'true');
    speak.textContent = 'Arrêter la lecture';
    player.done.then(() => { if (speaking === mine) { stopSpeaking(); paint(); } });
    paint();
  });

  // Rendu : monté seulement si WebGL 2 répond, sinon une pastille et le même libellé.
  const canvas = el('canvas', 'at-mascotte-canvas', null, { 'aria-hidden': 'true' });
  stage.replaceWith(canvas);
  stage = canvas;
  let made = null;
  made = createMascotte(canvas, {
    sample: levels,
    onFallback: (reason) => { fallback(reason); if (made) made.destroy(); },
  });
  if (made.supported) { mascotte = made; made.start(); }

  voiceActive(ctx.api).then((on) => { if (!destroyed) { voice = on; paint(); } });
  ctx.signal.addEventListener('abort', () => {
    destroyed = true;
    stopSpeaking();
    if (mascotte) { mascotte.destroy(); mascotte = null; }
  });

  return {
    element,
    update(next) {
      const before = facts.text;
      facts = next || {};
      if (speaking && before !== facts.text) stopSpeaking(); // la question a changé : on ne lit pas l'ancienne
      paint();
    },
    state: () => shown,
  };
}
