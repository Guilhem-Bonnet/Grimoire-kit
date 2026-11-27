import assert from 'node:assert/strict';
import { test } from 'node:test';
import { dotLabel, isReadingShortcut, readingButtonText, readingFrom } from '../../web/workspace/spaces/atelier-lecture-logic.js';

test('readingFrom : l’adresse prime, sinon le choix mémorisé du navigateur', () => {
  assert.equal(readingFrom({ lecture: '1' }, null), true);
  assert.equal(readingFrom({ lecture: '1' }, '0'), true);
  assert.equal(readingFrom({ lecture: '0' }, '1'), false);
  assert.equal(readingFrom({}, '1'), true);
  assert.equal(readingFrom(null, '1'), true);
  assert.equal(readingFrom({}, '0'), false);
  assert.equal(readingFrom({}, null), false);
  assert.equal(readingFrom({ lecture: 'oui' }, null), false, 'une valeur inconnue ne compte pas');
});

test('isReadingShortcut : Alt+L seul, par code ou par touche', () => {
  assert.equal(isReadingShortcut({ altKey: true, code: 'KeyL', key: '¬' }), true);
  assert.equal(isReadingShortcut({ altKey: true, key: 'l' }), true);
  assert.equal(isReadingShortcut({ altKey: true, key: 'L' }), true);
  assert.equal(isReadingShortcut({ altKey: false, code: 'KeyL', key: 'l' }), false);
  assert.equal(isReadingShortcut({ altKey: true, ctrlKey: true, code: 'KeyL', key: 'l' }), false);
  assert.equal(isReadingShortcut({ altKey: true, metaKey: true, code: 'KeyL', key: 'l' }), false);
  assert.equal(isReadingShortcut({ altKey: true, shiftKey: true, code: 'KeyL', key: 'l' }), false, '⇧⌘L reste celui de la coque');
  assert.equal(isReadingShortcut({ altKey: true, code: 'KeyK', key: 'k' }), false);
  assert.equal(isReadingShortcut(null), false);
});

test('libellés : le bouton dit où l’on va, la pastille compte les fils', () => {
  assert.equal(readingButtonText(false), 'Lecture');
  assert.equal(readingButtonText(true), 'Modifier');
  assert.equal(dotLabel(1), '1 commentaire ouvert : ouvrir le fil');
  assert.equal(dotLabel(3), '3 commentaires ouverts : ouvrir le fil');
});
