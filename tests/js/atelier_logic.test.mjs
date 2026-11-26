import assert from 'node:assert/strict';
import { test } from 'node:test';
import { attachTarget, canAttach, diffRows, driftNote, historyLine, nearestStart } from '../../web/workspace/spaces/atelier-logic.js';

test('nearestStart départage les occurrences par la position approximative', () => {
  const text = 'aa bb aa bb aa';
  assert.equal(nearestStart(text, 'aa', 0), 0);
  assert.equal(nearestStart(text, 'aa', 7), 6);
  assert.equal(nearestStart(text, 'aa', 99), 12);
  assert.equal(nearestStart(text, 'zz', 3), -1);
});

test('attachTarget : la sélection l’emporte, sinon le bloc courant entier, sinon rien', () => {
  const pick = { blockId: 'b.1', quote: 'passage', start: 4 };
  assert.deepEqual(attachTarget(pick, { id: 'b.2' }), { blockId: 'b.1', quote: 'passage', start: 4, whole: false });
  assert.deepEqual(attachTarget(null, { id: 'b.2' }), { blockId: 'b.2', quote: '', start: null, whole: true });
  assert.deepEqual(attachTarget({ blockId: 'b.1', quote: '', start: 0 }, { id: 'b.2' }), { blockId: 'b.2', quote: '', start: null, whole: true });
  assert.equal(attachTarget(null, null), null);
  assert.equal(attachTarget(null, {}), null);
  assert.equal(attachTarget({ blockId: 'b.1', quote: 'x'.repeat(400), start: 0 }, null).quote.length, 300);
});

test('canAttach : seul un fil ouvert et orphelin se rattache', () => {
  assert.equal(canAttach({ etat: 'ouvert', anchor: { state: 'orphelin' } }), true);
  assert.equal(canAttach({ etat: 'ouvert', anchor: { state: 'ancre' } }), false);
  assert.equal(canAttach({ etat: 'ouvert', anchor: { state: 'retrouve' } }), false);
  assert.equal(canAttach({ etat: 'resolu', anchor: { state: 'orphelin' } }), false);
  assert.equal(canAttach(null), false);
});

test('diffRows classe chaque ligne du diff de dérive', () => {
  const rows = diffRows({ diff: ['- critère : a', '+ critère : b', '… 3 lignes de plus'] });
  assert.deepEqual(rows.map((r) => r.kind), ['del', 'add', 'more']);
  assert.deepEqual(diffRows(null), []);
  assert.deepEqual(diffRows({ diff: 'pas une liste' }), []);
});

test('driftNote rend la phrase du serveur, ou rien', () => {
  assert.equal(driftNote({ note: 'validée avant la conservation du texte validé : la dérive est signalée sans détail' }).startsWith('validée avant'), true);
  assert.equal(driftNote({ note: '' }), '');
  assert.equal(driftNote(null), '');
});

test('historyLine dit la date, l’ancien et le nouveau passage, bornés', () => {
  const entry = { date: '2026-11-26T10:00:00+00:00', avant: { quote: 'ancien' }, apres: { quote: 'x'.repeat(100) } };
  const line = historyLine(entry, () => '26 novembre');
  assert.match(line, /^Rattaché le 26 novembre : « ancien » → « x{59}… »$/);
  assert.equal(historyLine({ date: '', avant: { quote: 'a' }, apres: { quote: 'b' } }, () => ''), 'Rattaché : « a » → « b »');
});
