import assert from 'node:assert/strict';
import { test } from 'node:test';

import { choicesVisible, clickableOptions, displayOrder, liveTarget, needsFold, shownText } from '../../web/workspace/voice_outbox.js';

const msg = (id, order, kind = 'report', answered = false) => ({ id, order, kind, answered });

test('une question sans réponse passe en tête, quel que soit son rang d’arrivée', () => {
  const ordered = displayOrder([msg('a', 1), msg('b', 2), msg('q', 3, 'question'), msg('c', 4)]);
  assert.deepEqual(ordered.map((m) => m.id), ['q', 'a', 'b', 'c']);
});

test('les questions en attente gardent leur ordre, une question répondue rentre dans le rang', () => {
  const ordered = displayOrder([msg('q1', 1, 'question'), msg('r', 2), msg('q2', 3, 'question'), msg('old', 0, 'question', true)]);
  assert.deepEqual(ordered.map((m) => m.id), ['q1', 'q2', 'old', 'r']);
});

const question = {
  kind: 'question', answered: false, reserved: false, truncated: false, ref: { task_id: 't', question_id: 'q' }, options: ['Oui', 'Non'],
  text: 'Puis-je lancer et pousser en force sur ? Options : Oui ou Non.',
  full_text: 'Puis-je lancer `rm -rf ~/projets` et pousser en force sur /home/guilhem/prod/repo ?',
};

test('la boîte affiche le texte complet d’origine d’une question, jamais le texte parlé', () => {
  assert.equal(shownText(question), question.full_text);
  assert.match(shownText(question), /rm -rf ~\/projets/);
  assert.equal(shownText({ kind: 'report', text: 'Fait.', full_text: 'autre' }), 'Fait.');
});

test('une question réservée à l’humain n’a aucune option cliquable', () => {
  assert.deepEqual(clickableOptions(question), ['Oui', 'Non']);
  assert.deepEqual(clickableOptions({ ...question, reserved: true }), []);
  assert.deepEqual(clickableOptions({ ...question, answered: true }), []);
  assert.deepEqual(clickableOptions({ ...question, ref: null }), []);
});

test('un texte long ou sur beaucoup de lignes est replié', () => {
  assert.equal(needsFold('court'), false);
  assert.equal(needsFold('x'.repeat(400)), true);
  assert.equal(needsFold('a\nb\nc\nd\ne'), true);
});

test('un clic sur une option n’appelle pas l’envoi : le module ne référence ni chatSend ni voiceTranscribe', async () => {
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(new URL('../../web/workspace/voice_outbox.js', import.meta.url), 'utf8');
  assert.equal(/chatSend/.test(src), false);
  const chat = await readFile(new URL('../../web/workspace/chat.js', import.meta.url), 'utf8');
  const fn = chat.slice(chat.indexOf('function answerVoiceQuestion'), chat.indexOf('function questionResolved'));
  assert.equal(/chatSend|submit\(/.test(fn), false);
  assert.match(fn, /input\.value = option/);
});

test('réservé sauf si la valeur est exactement false : l’absence ou une valeur vague ne donnent aucun bouton', () => {
  for (const reserved of [undefined, null, 0, '', 'false', true]) {
    assert.deepEqual(clickableOptions({ ...question, reserved }), [], String(reserved));
  }
  assert.deepEqual(clickableOptions({ ...question, reserved: false }), ['Oui', 'Non']);
});

test('un texte tronqué, ou sans indicateur truncated, n’a aucun bouton d’option', () => {
  assert.deepEqual(clickableOptions({ ...question, truncated: true }), []);
  assert.deepEqual(clickableOptions({ ...question, truncated: undefined }), []);
});

test('les boutons d’option ne s’affichent que si le texte est entièrement visible', () => {
  assert.equal(choicesVisible(2, false, false), true); // texte court
  assert.equal(choicesVisible(2, true, false), false); // replié : fin cachée
  assert.equal(choicesVisible(2, true, true), true); // déplié
  assert.equal(choicesVisible(0, false, false), false);
});

test('la cible d’une question vocale n’existe que si sa carte est encore posée dans le fil', () => {
  const card = (done) => ({ box: { classList: { contains: (c) => c === 'is-done' && done } } });
  const message = { ref: { task_id: 't', question_id: 'q' } };
  assert.equal(liveTarget(message, new Map()), null); // carte disparue
  assert.equal(liveTarget(message, new Map([['t/q', card(true)]])), null); // déjà tranchée
  const live = card(false);
  assert.equal(liveTarget(message, new Map([['t/q', live]])), live);
  assert.equal(liveTarget({ ref: null }, new Map()), null);
});
