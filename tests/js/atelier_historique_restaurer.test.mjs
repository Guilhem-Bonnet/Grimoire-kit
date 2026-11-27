import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  blockCounts, countsText, findOnlyBlock, orderPair, pairLabel, restoreCounts, restoreNote, restoredText, toggleSelection, versionName,
} from '../../web/workspace/spaces/atelier-historique-logic.js';

test('versionName et pairLabel : « Tu compares A et B. »', () => {
  assert.equal(versionName({ kind: 'working' }), 'la version actuelle');
  assert.equal(versionName(null), 'la version actuelle');
  assert.equal(versionName({ kind: 'commit', date: 'pas une date', short: 'abc1234' }), 'la version abc1234');
  assert.match(versionName({ kind: 'commit', date: '2026-11-27T14:32:00+01:00' }), /^la version du .*2026 à \d{2}:\d{2}$/);
  assert.match(pairLabel({ kind: 'commit', date: '2026-11-27T14:32:00+01:00' }, { kind: 'working' }), /^Tu compares la version du .* et la version actuelle\.$/);
});

test('orderPair : plus ancienne d’abord, d’après la liste (plus récente en tête)', () => {
  const versions = [{ id: 'w' }, { id: 'c3' }, { id: 'c2' }, { id: 'c1' }];
  assert.deepEqual(orderPair(versions, 'c3', 'c1'), ['c1', 'c3']);
  assert.deepEqual(orderPair(versions, 'c1', 'c3'), ['c1', 'c3']);
  assert.deepEqual(orderPair(versions, 'w', 'c2'), ['c2', 'w']);
  assert.equal(orderPair(versions, 'c1', 'c1'), null);
  assert.equal(orderPair(versions, 'c1', 'inconnu'), null);
  assert.equal(orderPair(null, 'a', 'b'), null);
});

test('toggleSelection : deux au plus, la plus ancienne choisie sort', () => {
  assert.deepEqual(toggleSelection([], 'a'), ['a']);
  assert.deepEqual(toggleSelection(['a'], 'b'), ['a', 'b']);
  assert.deepEqual(toggleSelection(['a', 'b'], 'c'), ['b', 'c']);
  assert.deepEqual(toggleSelection(['a', 'b'], 'a'), ['b']);
});

test('restoreCounts : l’inverse du diff affiché ; countsText porte le signe', () => {
  assert.deepEqual(restoreCounts({ added: 4, removed: 1 }), { plus: 1, minus: 4 });
  assert.deepEqual(restoreCounts(null), { plus: 0, minus: 0 });
  assert.equal(countsText({ plus: 1, minus: 4 }), '+1 −4 lignes');
  assert.equal(countsText({ plus: 1, minus: 0 }), '+1 −0 ligne');
});

test('blockCounts : les lignes qui reviennent (+) et celles qui partent (−)', () => {
  assert.deepEqual(blockCounts({ old_text: 'a\nb', text: 'a\nc\nd' }), { plus: 1, minus: 2 });
  assert.deepEqual(blockCounts({ old_text: 'x', text: 'x' }), { plus: 0, minus: 0 });
  assert.deepEqual(blockCounts({ old_text: 'a\na', text: 'a' }), { plus: 1, minus: 0 });
  assert.deepEqual(blockCounts(null), { plus: 0, minus: 0 });
});

test('restoreNote : le refus du serveur, sinon sa note, vide si la phase se restaure', () => {
  assert.equal(restoreNote({ restorable: true }), '');
  assert.equal(restoreNote(null), '');
  assert.equal(restoreNote({ restorable: false, refusal: 'document accepté', restore_note: 'x' }), 'document accepté');
  assert.equal(restoreNote({ restorable: false, refusal: '', restore_note: 'pas ce fichier' }), 'pas ce fichier');
});

test('findOnlyBlock : seulement si un seul bloc porte ce texte (sinon pas d’annulation sûre)', () => {
  const blocks = [{ id: 'a', text: 'un' }, { id: 'b', text: 'deux' }, { id: 'c', text: 'deux' }];
  assert.equal(findOnlyBlock(blocks, 'un').id, 'a');
  assert.equal(findOnlyBlock(blocks, 'deux'), null);
  assert.equal(findOnlyBlock(blocks, 'absent'), null);
  assert.equal(findOnlyBlock(null, 'un'), null);
});

test('restoredText : l’accusé dit que rien n’est commité', () => {
  assert.equal(restoredText('La phase Brief', { plus: 2, minus: 3 }), 'La phase Brief restaurée (+2 −3 lignes). Ce n’est pas commité : c’est une modification de plus dans tes fichiers.');
  assert.match(restoredText('Le bloc', { plus: 1, minus: 1 }), /^Le bloc restauré \(\+1 −1 lignes\)\./);
});
