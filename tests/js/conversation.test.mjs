import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  EMPTY_LIMIT, END_SILENCE_MS, MIN_SPEECH_MS, NO_SPEECH_MS, REVIEW_MAX_MS, SPEECH_LEVEL, THINK_MAX_MS,
  captionFor, initial, isShortcut, mascotteActionFor, step,
} from '../../web/workspace/conversation_logic.js';

/** Joue une suite d'événements ; rend le modèle final et tous les effets, dans l'ordre. */
function run(events, from = initial()) {
  let model = from;
  const effects = [];
  for (const event of events) {
    const out = step(model, event);
    model = out.model;
    effects.push(...out.effects);
  }
  return { model, effects };
}
const speak = (from, to, level = 0.5) => [{ type: 'level', now: from, level }, { type: 'level', now: to, level }];

test('le mode ne s’ouvre que par un geste explicite : tout événement à l’arrêt est sans effet', () => {
  for (const type of ['level', 'tick', 'transcribed', 'sent', 'voice', 'turn-ended', 'play-ended', 'permission', 'stop']) {
    const out = step(initial(), { type, now: 1, level: 1, text: 'x' });
    assert.equal(out.model.mode, 'off', type);
    assert.deepEqual(out.effects, []);
  }
});

test('start ouvre l’écoute (micro), ou lit d’abord (Atelier : la question de l’assistant)', () => {
  const a = step(initial(), { type: 'start', now: 10 });
  assert.equal(a.model.mode, 'listen');
  assert.deepEqual(a.effects, [{ do: 'record' }]);
  const b = step(initial(), { type: 'start', now: 10, read: true });
  assert.equal(b.model.mode, 'speak');
  assert.deepEqual(b.effects, [{ do: 'read' }]);
  assert.deepEqual(step(a.model, { type: 'start', now: 11 }).effects, [], 'un second geste ne rouvre rien');
});

test('écoute : la fin de la parole (silence court après avoir parlé) lance la transcription', () => {
  const { model, effects } = run([
    { type: 'start', now: 0 }, ...speak(500, 1500),
    { type: 'tick', now: 1500 + END_SILENCE_MS - 1 },
  ]);
  assert.equal(model.mode, 'listen', 'pas encore : le silence est trop court');
  const done = run([{ type: 'tick', now: 1500 + END_SILENCE_MS }], model);
  assert.equal(done.model.mode, 'transcribe');
  assert.deepEqual(done.effects, [{ do: 'finish' }]);
  assert.deepEqual(effects, [{ do: 'record' }]);
});

test('écoute : un souffle plus bref qu’une phrase est ignoré, l’écoute continue', () => {
  const { model } = run([
    { type: 'start', now: 0 }, { type: 'level', now: 1000, level: 0.9 }, { type: 'tick', now: 1000 + MIN_SPEECH_MS - 1 + END_SILENCE_MS },
  ]);
  assert.equal(model.mode, 'listen');
  assert.equal(model.spokeAt, 0);
});

test('écoute : un niveau sous le seuil n’est pas de la parole', () => {
  const { model } = run([{ type: 'start', now: 0 }, { type: 'level', now: 100, level: SPEECH_LEVEL - 0.01 }]);
  assert.equal(model.spokeAt, 0);
});

test('une dictée reconnue attend l’envoi humain : rien ne part tout seul', () => {
  const { model, effects } = run([
    { type: 'start', now: 0 }, ...speak(500, 1500), { type: 'tick', now: 4000 }, { type: 'transcribed', now: 4500, text: 'bonjour' },
  ]);
  assert.equal(model.mode, 'review');
  assert.ok(!effects.some((e) => e.do === 'send'), 'aucun effet d’envoi n’existe');
  assert.deepEqual(effects.map((e) => e.do), ['record', 'finish']);
});

test('boucle complète : écoute, dictée, envoi humain, réflexion, réponse lue, écoute', () => {
  const { model, effects } = run([
    { type: 'start', now: 0 }, ...speak(500, 1500), { type: 'tick', now: 4000 },
    { type: 'transcribed', now: 4500, text: 'bonjour' },
    { type: 'sent', now: 6000 },
    { type: 'voice', id: 'm1' },
    { type: 'turn-ended', now: 9000 },
    { type: 'play-ended', now: 12000 },
  ]);
  assert.equal(model.mode, 'listen');
  assert.deepEqual(effects, [{ do: 'record' }, { do: 'finish' }, { do: 'read', id: 'm1' }, { do: 'record' }]);
});

test('sans message parlé à la fin du tour, on retourne directement à l’écoute', () => {
  const { model, effects } = run([
    { type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: 'ok' }, { type: 'sent', now: 2 }, { type: 'turn-ended', now: 3 },
  ]);
  assert.equal(model.mode, 'listen');
  assert.equal(effects.at(-1).do, 'record');
});

test('fin de tour : le message parlé donné par l’événement prime sur celui retenu', () => {
  const { effects } = run([
    { type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: 'ok' }, { type: 'sent', now: 2 },
    { type: 'voice', id: 'old' }, { type: 'turn-ended', now: 3, voice: 'new' },
  ]);
  assert.deepEqual(effects.at(-1), { do: 'read', id: 'new' });
});

test('un message parlé arrivé avant l’envoi n’est pas la réponse', () => {
  const { effects } = run([
    { type: 'start', now: 0 }, { type: 'voice', id: 'avant' }, { type: 'transcribed', now: 1, text: 'ok' },
    { type: 'sent', now: 2 }, { type: 'turn-ended', now: 3 },
  ]);
  assert.deepEqual(effects.at(-1), { do: 'record' });
});

test('un envoi pendant l’écoute (frappé au clavier) coupe le micro sans transcrire', () => {
  const { model, effects } = run([{ type: 'start', now: 0 }, { type: 'sent', now: 5 }]);
  assert.equal(model.mode, 'think');
  assert.deepEqual(effects.at(-1), { do: 'abort-recording' });
});

test('arrêt immédiat à tout moment, avec sa raison', () => {
  const states = [
    [{ type: 'start', now: 0 }],
    [{ type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: 'a' }],
    [{ type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: 'a' }, { type: 'sent', now: 2 }],
    [{ type: 'start', now: 0, read: true }],
  ];
  for (const events of states) {
    for (const reason of ['stop', 'escape', 'focus']) {
      const { model, effects } = run([...events, { type: 'stop', reason }]);
      assert.equal(model.mode, 'off');
      assert.deepEqual(effects.at(-1), { do: 'close', reason });
    }
  }
});

test('une permission en attente ferme le mode : seul un clic la tranche', () => {
  const { model, effects } = run([{ type: 'start', now: 0 }, { type: 'permission' }]);
  assert.equal(model.mode, 'off');
  assert.deepEqual(effects.at(-1), { do: 'close', reason: 'permission' });
});

test('silence prolongé : personne ne parle à l’écoute, ou rien n’est envoyé après la dictée', () => {
  const a = run([{ type: 'start', now: 0 }, { type: 'tick', now: NO_SPEECH_MS - 1 }]);
  assert.equal(a.model.mode, 'listen');
  const b = run([{ type: 'tick', now: NO_SPEECH_MS }], a.model);
  assert.equal(b.model.mode, 'off');
  assert.deepEqual(b.effects, [{ do: 'close', reason: 'silence' }]);
  const review = run([{ type: 'start', now: 0 }, { type: 'transcribed', now: 100, text: 'a' }]).model;
  assert.equal(run([{ type: 'tick', now: 100 + REVIEW_MAX_MS - 1 }], review).model.mode, 'review');
  assert.equal(run([{ type: 'tick', now: 100 + REVIEW_MAX_MS }], review).model.mode, 'off');
  const think = run([{ type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: 'a' }, { type: 'sent', now: 2 }]).model;
  assert.equal(run([{ type: 'tick', now: 2 + THINK_MAX_MS }], think).model.mode, 'off');
});

test('l’attente de la réponse n’est pas un silence : un tour de quelques minutes ne ferme pas le mode', () => {
  const think = run([{ type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: 'a' }, { type: 'sent', now: 2 }]).model;
  assert.equal(run([{ type: 'tick', now: 5 * 60 * 1000 }], think).model.mode, 'think');
});

test('dictées vides : on réécoute une fois, puis le mode se ferme', () => {
  assert.equal(EMPTY_LIMIT, 2);
  const first = run([{ type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: '  ' }]);
  assert.equal(first.model.mode, 'listen');
  assert.deepEqual(first.effects.at(-1), { do: 'record' });
  const second = run([{ type: 'transcribed', now: 2, text: '' }], first.model);
  assert.equal(second.model.mode, 'off');
  assert.deepEqual(second.effects, [{ do: 'close', reason: 'silence' }]);
});

test('échec de la dictée : le mode se ferme avec sa raison', () => {
  const out = run([{ type: 'start', now: 0 }, { type: 'transcribed', now: 1, text: null }]);
  assert.equal(out.model.mode, 'off');
  assert.deepEqual(out.effects.at(-1), { do: 'close', reason: 'error' });
});

test('la lecture finie (ou en échec) rend l’écoute ; hors lecture, play-ended est ignoré', () => {
  const out = run([{ type: 'start', now: 0, read: true }, { type: 'play-ended', now: 5 }]);
  assert.equal(out.model.mode, 'listen');
  assert.deepEqual(out.effects, [{ do: 'read' }, { do: 'record' }]);
  assert.deepEqual(step(run([{ type: 'start', now: 0 }]).model, { type: 'play-ended', now: 1 }).effects, []);
});

test('un mode refermé repart de zéro au prochain geste', () => {
  const { model } = run([{ type: 'start', now: 0 }, ...speak(1, 2), { type: 'stop', reason: 'stop' }, { type: 'start', now: 50 }]);
  assert.equal(model.mode, 'listen');
  assert.equal(model.spokeAt, 0);
  assert.equal(model.empties, 0);
});

test('libellés visibles et actions de la mascotte par état', () => {
  assert.equal(captionFor('listen'), 'je t’écoute');
  assert.equal(captionFor('review'), 'relis puis envoie');
  assert.equal(captionFor('speak'), 'parle');
  assert.equal(captionFor('off'), '');
  assert.equal(mascotteActionFor('listen'), 'ecoute');
  assert.equal(mascotteActionFor('think'), 'reflechit');
  assert.equal(mascotteActionFor('transcribe'), 'reflechit');
  assert.equal(mascotteActionFor('speak'), 'parle');
  assert.equal(mascotteActionFor('review'), null);
  assert.equal(mascotteActionFor('off'), null);
});

test('isShortcut : Alt+Maj+V seulement, sans Ctrl ni Méta', () => {
  assert.equal(isShortcut({ altKey: true, shiftKey: true, code: 'KeyV' }), true);
  assert.equal(isShortcut({ altKey: true, shiftKey: true, key: 'V' }), true);
  assert.equal(isShortcut({ altKey: true, shiftKey: false, code: 'KeyV' }), false);
  assert.equal(isShortcut({ altKey: true, shiftKey: true, ctrlKey: true, code: 'KeyV' }), false);
  assert.equal(isShortcut({ altKey: true, shiftKey: true, metaKey: true, code: 'KeyV' }), false);
  assert.equal(isShortcut({ altKey: true, shiftKey: true, code: 'KeyB' }), false);
  assert.equal(isShortcut(null), false);
});
