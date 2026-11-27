import assert from 'node:assert/strict';
import { test } from 'node:test';
import { focusSelector, pickIndex, same, stable } from '../../web/workspace/spaces/atelier-focus.js';

test('stable : deux états égaux donnent le même texte, quel que soit l’ordre des clés', () => {
  assert.equal(stable({ a: 1, b: [1, 2] }), stable({ b: [1, 2], a: 1 }));
  assert.notEqual(stable({ a: 1 }), stable({ a: 2 }));
  assert.notEqual(stable([1, 2]), stable([2, 1]));
  assert.equal(stable({ a: 1, b: undefined }), stable({ a: 1 }));
  assert.equal(stable(null), 'null');
  assert.equal(stable(undefined), 'null');
});

test('same : rien à redessiner quand l’état est identique', () => {
  const trace = { requirements: [{ id: 'REQ-001', validated: false, gaps: [] }], moa: { declared: false } };
  assert.equal(same(trace, JSON.parse(JSON.stringify(trace))), true);
  assert.equal(same(trace, { ...trace, moa: { declared: true } }), false);
  assert.equal(same(null, null), true);
  assert.equal(same(null, {}), false);
});

test('focusSelector : l’élément équivalent se retrouve par son geste, sa phase ou son rôle, sinon par sa balise', () => {
  assert.equal(focusSelector({ 'data-act': 'split' }, 'BUTTON', 'at-btn'), 'button[data-act="split"]');
  assert.equal(focusSelector({ 'data-phase': 'brief', 'data-state': 'x' }, 'BUTTON', 'at-phase'), 'button[data-phase="brief"]');
  assert.equal(focusSelector({ 'aria-label': 'Ta réponse' }, 'TEXTAREA', 'at-asst-area'), 'textarea[aria-label="Ta réponse"]');
  assert.equal(focusSelector({}, 'SUMMARY', 'at-muted'), 'summary.at-muted');
  assert.equal(focusSelector({}, 'A'), 'a');
  assert.equal(focusSelector({}, ''), '');
  assert.equal(focusSelector({ 'data-act': 'a"b' }, 'BUTTON'), 'button[data-act="a\\"b"]');
});

test('pickIndex : le même rang parmi les semblables, sinon le dernier, sinon aucun', () => {
  assert.equal(pickIndex(1, 3), 1);
  assert.equal(pickIndex(5, 3), 2);
  assert.equal(pickIndex(-1, 3), 0);
  assert.equal(pickIndex(0, 0), -1);
});
