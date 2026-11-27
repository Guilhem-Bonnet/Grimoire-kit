import assert from 'node:assert/strict';
import { test } from 'node:test';
import { KINDS, MAX_DRAFT, decodeDraft, draftKey, encodeDraft, isDirty, worthKeeping } from '../../web/workspace/spaces/atelier-brouillons-logic.js';

test('draftKey : projet, chantier, genre et bloc ou fil distinguent les brouillons', () => {
  const base = draftKey('p1', 'spec', 'bloc', 'brainstorm.abc');
  assert.notEqual(base, draftKey('p2', 'spec', 'bloc', 'brainstorm.abc'));
  assert.notEqual(base, draftKey('p1', 'autre', 'bloc', 'brainstorm.abc'));
  assert.notEqual(base, draftKey('p1', 'spec', 'comment', 'brainstorm.abc'));
  assert.notEqual(base, draftKey('p1', 'spec', 'bloc', 'brainstorm.def'));
  assert.equal(base, draftKey('p1', 'spec', 'bloc', 'brainstorm.abc'));
  assert.match(draftKey('', 'spec', 'bloc', 'x'), /\|-\|spec\|/); // sans projet : une valeur de repli, pas « undefined »
  assert.deepEqual(KINDS, ['bloc', 'comment', 'reply', 'guide']);
});

test('worthKeeping et isDirty : un champ vide ou inchangé n’est pas une saisie', () => {
  assert.equal(worthKeeping('Un texte', ''), true);
  assert.equal(worthKeeping('  ', ''), false);
  assert.equal(worthKeeping('Même', 'Même'), false);
  assert.equal(worthKeeping('x'.repeat(MAX_DRAFT + 1), ''), false);
  assert.equal(worthKeeping(null, ''), false);
  assert.equal(isDirty('Modifié', 'Original'), true);
  assert.equal(isDirty('Original', 'Original'), false);
  assert.equal(isDirty('', 'Original'), false);
});

test('encodeDraft et decodeDraft : aller-retour, et tout le reste est ignoré', () => {
  assert.deepEqual(decodeDraft(encodeDraft('Mon brouillon', 42)), { text: 'Mon brouillon', at: 42 });
  assert.equal(decodeDraft(null), null);
  assert.equal(decodeDraft(''), null);
  assert.equal(decodeDraft('pas du json'), null);
  assert.equal(decodeDraft('{"text":3}'), null);
  assert.equal(decodeDraft('{"text":"  "}'), null);
  assert.equal(decodeDraft('null'), null);
  assert.deepEqual(decodeDraft('{"text":"a"}'), { text: 'a', at: 0 });
});
