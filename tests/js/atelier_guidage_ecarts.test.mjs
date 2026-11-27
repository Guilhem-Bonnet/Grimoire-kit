import assert from 'node:assert/strict';
import { test } from 'node:test';
import { placeholderText } from '../../web/workspace/spaces/atelier-invites-logic.js';
import { usefulProgress } from '../../web/workspace/spaces/atelier-reste-a-faire-logic.js';
import { PHONE_MAX } from '../../web/workspace/spaces/atelier-layout-logic.js';

test('usefulProgress : une ligne par phase entamée, au pluriel de ce qui est rempli, et l’étape suivante', () => {
  const reste = {
    avancement: [
      { phase: 'brief', phase_title: 'Brief', utiles_remplies: 2, utiles_total: 4 },
      { phase: 'comprehension', phase_title: 'Compréhension', utiles_remplies: 1, utiles_total: 3 },
    ],
    etape: { phase: 'brief', phase_title: 'Brief', section: 'Le problème', quoi: 'Ce qui ne va pas.' },
  };
  const out = usefulProgress(reste);
  assert.deepEqual(out.lines.map((l) => l.text), ['Brief : 2 rubriques utiles sur 4 remplies', 'Compréhension : 1 rubrique utile sur 3 remplie']);
  assert.equal(out.step.place, 'Brief › Le problème');
});

test('usefulProgress : sans donnée (ancien serveur), rien', () => {
  assert.deepEqual(usefulProgress(null), { lines: [], step: null });
  assert.deepEqual(usefulProgress({}), { lines: [], step: null });
});

test('placeholderText : une invite humaine, jamais le gabarit', () => {
  assert.equal(placeholderText({ section: 'Le problème' }), 'Écris ici, avec tes mots : le problème.');
  assert.ok(!placeholderText(null).includes('À compléter'));
});

test('le seuil de repli est de 900 px, inclus (le test est « <= »)', () => {
  assert.equal(PHONE_MAX, 900);
});
