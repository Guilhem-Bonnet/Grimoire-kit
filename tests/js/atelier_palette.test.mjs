import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CHANTIER_SECTION, OUTLINE_SECTION, chantierEntries, outlineEntries } from '../../web/workspace/spaces/atelier-palette-logic.js';

test('chantierEntries : titre, état et slug ; les chantiers à réparer ne s’ouvrent pas', () => {
  const entries = chantierEntries([
    { slug: 'a', titre: 'Refonte', etat: 'en-discussion' },
    { slug: 'b', titre: '', etat: 'archive' },
    { slug: 'c', titre: 'Cassé', error: 'illisible' },
    { slug: 'd', titre: 'Sans état' },
    null,
    { titre: 'Sans slug' },
  ]);
  assert.deepEqual(entries, [
    { slug: 'a', label: 'Refonte', hint: 'Chantier · En discussion' },
    { slug: 'b', label: 'b', hint: 'Chantier · Archivé' },
    { slug: 'd', label: 'Sans état', hint: 'Chantier · sans état' },
  ]);
  assert.deepEqual(chantierEntries(undefined), []);
});

test('outlineEntries : chaque phase, puis ses rubriques, avec leur rang', () => {
  const entries = outlineEntries([
    { id: 'brief', title: 'Brief', rubriques: [{ name: 'Le problème' }, { name: 'Les utilisateurs' }] },
    { id: 'exigences', title: 'Exigences', rubriques: [] },
  ]);
  assert.deepEqual(entries.map((e) => e.label), [
    'Aller à : Brief', 'Aller à : Brief › Le problème', 'Aller à : Brief › Les utilisateurs', 'Aller à : Exigences',
  ]);
  assert.deepEqual(entries.map((e) => [e.phase, e.rub]), [['brief', -1], ['brief', 0], ['brief', 1], ['exigences', -1]]);
  assert.deepEqual(outlineEntries(null), []);
  assert.deepEqual(outlineEntries([{ id: 'x', title: 'X' }]).map((e) => e.rub), [-1]);
});

test('les noms de section sont stables (l’ordre de la palette s’y réfère)', () => {
  assert.equal(CHANTIER_SECTION, 'Chantiers');
  assert.equal(OUTLINE_SECTION, 'Dans ce chantier');
});
