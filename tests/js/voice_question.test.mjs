import assert from 'node:assert/strict';
import { test } from 'node:test';

import { clickableOptions, displayOrder, needsFold, shownText } from '../../web/workspace/voice_outbox.js';

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
  kind: 'question', answered: false, reserved: false, ref: { task_id: 't', question_id: 'q' }, options: ['Oui', 'Non'],
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
