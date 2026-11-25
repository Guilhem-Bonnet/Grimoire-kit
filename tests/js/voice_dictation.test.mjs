import assert from 'node:assert/strict';
import { test } from 'node:test';

// Un DOM minimal : createVoiceInput ne touche qu'à quelques méthodes des éléments.
function element() {
  const listeners = {};
  return {
    hidden: false, disabled: false, textContent: '', value: '', isConnected: true,
    setAttribute() {}, append() {}, remove() {}, focus() {},
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    fire(type) { for (const fn of listeners[type] || []) fn({}); },
  };
}
globalThis.document = { createElement: () => element() };

const { createVoiceInput, stillDictated } = await import('../../web/workspace/voice.js');

/** Monte la saisie avec une transcription simulée, puis dicte `said` : rien n'enregistre vraiment. */
async function dictate(said, before = '') {
  const input = element();
  input.value = before;
  const recorded = [new Float32Array(1600)];
  const fakeContext = {
    sampleRate: 16000,
    createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
    createScriptProcessor: () => {
      const node = { connect() {}, disconnect() {} };
      queueMicrotask(() => node.onaudioprocess && node.onaudioprocess({ inputBuffer: { getChannelData: () => recorded[0] } }));
      return node;
    },
    close: async () => {},
  };
  globalThis.window = { AudioContext: function AudioContext() { return fakeContext; } };
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
  });
  const buttons = [];
  globalThis.document = { createElement: () => { const b = element(); buttons.push(b); return b; } };
  const api = { voiceConfig: async () => ({ available: true, max_seconds: 5 }), voiceTranscribe: async () => ({ text: said }) };
  const voice = createVoiceInput({ api, input, slot: element(), note() {}, setStatus() {} });
  await voice.refresh();
  const button = buttons[0];
  button.fire('click'); // start
  await new Promise((r) => setTimeout(r, 5));
  button.fire('click'); // stop -> transcription
  await new Promise((r) => setTimeout(r, 5));
  return { voice, input };
}

test('stillDictated : une correction partielle garde la dictée, une réécriture complète la fait tomber', () => {
  const parts = ['ouvre la tâche douze'];
  assert.equal(stillDictated('ouvre la tâche 12', parts), true);
  assert.equal(stillDictated('Ouvre LA tâche douze !', parts), true);
  assert.equal(stillDictated('ferme tout', parts), false);
  assert.equal(stillDictated('', parts), false);
  assert.equal(stillDictated('ouvre', []), false);
});

test('la dictée survit à un envoi échoué et n’est consommée qu’après un envoi réussi', async () => {
  const { voice, input } = await dictate('ouvre la tâche douze');
  assert.equal(input.value, 'ouvre la tâche douze');
  assert.equal(voice.dictationPending(input.value), true);
  // envoi refusé : le texte revient à la saisie, la mention doit le suivre
  const text = input.value;
  input.value = '';
  input.value = text;
  assert.equal(voice.dictationPending(input.value), true);
  voice.commitDictation(); // envoi réussi
  assert.equal(voice.dictationPending(input.value), false);
});

test('un texte dicté puis entièrement retapé au clavier part sans mention', async () => {
  const { voice, input } = await dictate('supprime la branche');
  input.value = 'garder';
  input.fire('input');
  assert.equal(voice.dictationPending(), false);
  input.value = 'supprime la branche'; // retaper les mêmes mots ne rend pas la dictée
  input.fire('input');
  assert.equal(voice.dictationPending(), false);
});

test('une correction partielle au clavier garde la mention', async () => {
  const { voice, input } = await dictate('ouvre la tâche douze');
  input.value = 'ouvre la tâche 12';
  input.fire('input');
  assert.equal(voice.dictationPending(), true);
});
