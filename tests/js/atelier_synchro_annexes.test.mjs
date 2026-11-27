import assert from 'node:assert/strict';
import { test } from 'node:test';
import { annexMessage, staleAnnexes } from '../../web/workspace/spaces/atelier-synchro-logic.js';

const doc = (phases, annexes) => ({ phases, annexes });
const phase = (suggestions = [], comments = []) => ({ id: 'brainstorm', suggestions: suggestions.map((id) => ({ id })), comments: comments.map((id) => ({ id })) });

test('staleAnnexes : les magasins dont l’empreinte ne correspond plus à ce que la page affiche', () => {
  const shown = doc([], { suggestions: 'aa', commentaires: 'bb' });
  assert.deepEqual(staleAnnexes(shown, { suggestions: 'aa', commentaires: 'bb' }), []);
  assert.deepEqual(staleAnnexes(shown, { suggestions: 'zz', commentaires: 'bb' }), ['suggestions']);
  assert.deepEqual(staleAnnexes(shown, { suggestions: 'x', commentaires: 'y' }), ['suggestions', 'commentaires']);
});

test('staleAnnexes : un magasin créé (vide puis rempli) ou supprimé est un changement', () => {
  assert.deepEqual(staleAnnexes(doc([], { suggestions: '', commentaires: '' }), { suggestions: 'aa', commentaires: '' }), ['suggestions']);
  assert.deepEqual(staleAnnexes(doc([], { suggestions: 'aa', commentaires: '' }), { suggestions: '', commentaires: '' }), ['suggestions']);
});

test('staleAnnexes : sans relevé exploitable d’un côté ou de l’autre, rien à recharger', () => {
  assert.deepEqual(staleAnnexes(null, { suggestions: 'a' }), []);
  assert.deepEqual(staleAnnexes(doc([]), { suggestions: 'a' }), [], 'serveur plus ancien : le document ne porte pas annexes');
  assert.deepEqual(staleAnnexes(doc([], { suggestions: 'a' }), undefined), []);
  assert.deepEqual(staleAnnexes(doc([], { suggestions: 'a' }), 'x'), []);
  assert.deepEqual(staleAnnexes(doc([], { suggestions: 'a' }), { commentaires: 'b' }), [], 'clé non affichée');
});

test('annexMessage : une suggestion, un commentaire, les deux, au pluriel', () => {
  const before = doc([phase(['s1'], ['c1'])]);
  assert.equal(annexMessage(before, before), '');
  assert.equal(annexMessage(before, doc([phase(['s1', 's2'], ['c1'])])), '1 suggestion de l’assistant');
  assert.equal(annexMessage(before, doc([phase(['s1'], ['c1', 'c2'])])), '1 nouveau commentaire');
  assert.equal(annexMessage(before, doc([phase(['s1', 's2', 's3'], ['c1', 'c2', 'c3'])])), '2 suggestions de l’assistant et 2 nouveaux commentaires');
  assert.equal(annexMessage(before, doc([phase(['s2'], ['c2'])])), '1 suggestion de l’assistant et 1 nouveau commentaire');
});

test('annexMessage : ce qui disparaît (tranchée, résolue) n’annonce rien ; documents absents sans erreur', () => {
  assert.equal(annexMessage(doc([phase(['s1'], ['c1'])]), doc([phase([], [])])), '');
  assert.equal(annexMessage(null, null), '');
  assert.equal(annexMessage(null, doc([phase(['s1'])])), '1 suggestion de l’assistant');
});
