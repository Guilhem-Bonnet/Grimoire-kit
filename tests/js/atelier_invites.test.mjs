import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  cleanReason, isPageMarker, markerText, notApplicableNote, rubricOf, rubricSig, statusInfo,
} from '../../web/workspace/spaces/atelier-invites-logic.js';
import { itemLabel, restSummary } from '../../web/workspace/spaces/atelier-reste-a-faire-logic.js';
import {
  BIG_KEY, acknowledgement, areaHeight, destination, readBig, remainingText, writeBig,
} from '../../web/workspace/spaces/atelier-entretien-logic.js';

const RUB = {
  section: 'Le problème', statut: 'utile', statut_libelle: 'utile', pourquoi: 'L’entretien pose des questions.', etat: 'vide', raison: '',
  marqueur: '> À compléter — sans objet pour ce besoin',
};

test('rubricOf : la rubrique du bloc par son nom, ou rien', () => {
  const phase = { rubriques: [RUB, { ...RUB, section: 'Faits' }] };
  assert.equal(rubricOf(phase, { section: 'Faits' }).section, 'Faits');
  assert.equal(rubricOf(phase, { section: 'Inconnue' }), null);
  assert.equal(rubricOf({}, { section: 'Faits' }), null);
  assert.equal(rubricOf(null, null), null);
});

test('rubricSig : un changement d’état redessine le bloc', () => {
  assert.notDeepEqual(rubricSig(RUB), rubricSig({ ...RUB, etat: 'sans-objet' }));
  assert.equal(rubricSig(null), null);
});

test('markerText : le gabarit « sans objet » du kit, avec la raison si elle est donnée', () => {
  assert.equal(markerText(RUB, ''), '> À compléter — sans objet pour ce besoin (à confirmer)');
  assert.equal(markerText(RUB, '  pas de date \n imposée '), '> À compléter — sans objet pour ce besoin : pas de date imposée (à confirmer)');
  assert.ok(markerText(RUB, 'x').startsWith('> À compléter —'), 'commence par le gabarit : le contrôle l’ignore');
});

test('cleanReason : une ligne, bornée, sans citation en tête', () => {
  assert.equal(cleanReason('> : a\nb'), 'a b');
  assert.equal(cleanReason(null), '');
  assert.equal(cleanReason('x'.repeat(500)).length, 200);
});

test('isPageMarker : seule la ligne de la page se remet au gabarit d’ici', () => {
  assert.equal(isPageMarker(RUB, markerText(RUB, 'raison')), true);
  assert.equal(isPageMarker(RUB, '> À compléter — sans objet d’après l’entretien : x (à confirmer)'), false);
  assert.equal(isPageMarker(RUB, ''), false);
});

test('statusInfo : le niveau d’importance suit le statut du serveur', () => {
  assert.equal(statusInfo({ ...RUB, statut: 'necessaire', statut_libelle: 'nécessaire pour valider' }).level, 'high');
  assert.equal(statusInfo(RUB).level, 'mid');
  assert.equal(statusInfo({ ...RUB, statut: 'facultatif', statut_libelle: 'peut rester vide' }).label, 'peut rester vide');
});

test('notApplicableNote : une rubrique nécessaire sans objet reste à confirmer pour valider', () => {
  assert.match(notApplicableNote({ ...RUB, statut: 'necessaire' }), /restera à confirmer pour valider/);
  assert.match(notApplicableNote({ ...RUB, statut: 'facultatif', raison: 'hors sujet' }), /plus comptée.*Raison : hors sujet/);
});

test('itemLabel : où, et ce qui reste à dire', () => {
  const base = { phase: 'brief', phase_title: 'Brief — l’intention', section: 'Le problème', kind: 'rubrique', etat: 'vide' };
  assert.deepEqual(itemLabel(base), { place: 'Brief › Le problème', note: '' });
  assert.match(itemLabel({ ...base, etat: 'sans-objet' }).note, /sans objet d’après toi ; à confirmer/);
  assert.match(itemLabel({ ...base, kind: 'angle', angle: 'Utilisateurs' }).note, /question « Utilisateurs »/);
  assert.match(itemLabel({ ...base, etat: 'je-ne-sais-pas' }).note, /je ne sais pas/);
});

test('restSummary : manque N choses, puis le facultatif', () => {
  assert.equal(restSummary(null), null);
  const need = (section) => ({ phase: 'exigences', phase_title: 'Exigences', section, kind: 'rubrique', etat: 'vide' });
  const s = restSummary({ necessaires: [need('A'), need('B'), need('C')], utiles: [1, 2], facultatives: [1], sans_objet: 0, erreur: '' });
  assert.equal(s.ready, false);
  assert.equal(s.headline, 'Il manque 3 choses pour pouvoir valider :');
  assert.equal(s.items.length, 3);
  assert.equal(s.items[0].place, 'Exigences › A');
  assert.equal(s.tail, 'Le reste est facultatif : 2 rubriques utiles et 1 rubrique qui peut rester vide.');
  assert.equal(restSummary({ necessaires: [need('A')], utiles: [], facultatives: [] }).headline, 'Il manque 1 chose pour pouvoir valider :');
});

test('restSummary : tout le nécessaire est là, le geste suivant est dit', () => {
  const s = restSummary({ necessaires: [], utiles: [], facultatives: [], sans_objet: 2, erreur: '' });
  assert.equal(s.ready, true);
  assert.match(s.headline, /Tout le nécessaire est rempli/);
  assert.match(s.next, /Valider et découper/);
  assert.match(s.next, /MOA, la personne qui valide/);
  assert.match(s.tail, /2 rubriques marquées sans objet/);
});

test('restSummary : un entretien illisible ne se dit pas « prêt »', () => {
  const s = restSummary({ necessaires: [], utiles: [], facultatives: [], erreur: 'entretien.json illisible' });
  assert.equal(s.ready, false);
  assert.equal(s.broken, true);
});

test('remainingText : ce qu’il reste en mots, jamais « Question 3 sur 10 »', () => {
  assert.equal(remainingText({ total: 10, position: 6 }), 'Encore 4 questions, environ 2 minutes');
  assert.equal(remainingText({ total: 10, position: 9 }), 'Dernière question, moins d’une minute');
  assert.equal(remainingText({ total: 3, position: 1 }), 'Encore 2 questions, environ 1 minute');
  assert.equal(remainingText({ total: 4, position: 4 }), 'Plus aucune question');
  assert.doesNotMatch(remainingText({ total: 10, position: 2 }), /sur 10/);
});

test('destination : la rubrique que vise CETTE question, pas celle de l’angle', () => {
  const cur = { target_section: 'Le problème', target_phase: 'brief', target_phase_title: 'Brief — l’intention', section: 'L’utilisateur', phase: 'brief' };
  assert.deepEqual(destination(cur), { section: 'Le problème', phase: 'Brief', phaseId: 'brief' });
  assert.equal(destination({ section: 'Faits', phase: 'comprehension', phase_title: 'Compréhension — x' }).section, 'Faits');
});

test('acknowledgement : où la réponse a été rangée, selon le geste', () => {
  const stored = (kind) => ({ stored: { kind, section: 'Le problème', phase: 'brief', phase_title: 'Brief — l’intention' } });
  assert.equal(acknowledgement(null), null);
  assert.equal(acknowledgement({}), null);
  assert.equal(acknowledgement(stored('answer')).text, 'Ta réponse est rangée dans : Le problème (Brief)');
  assert.match(acknowledgement(stored('unknown')).text, /question à préciser/);
  assert.match(acknowledgement(stored('skip')).text, /« sans objet »/);
});

test('areaHeight : grandit avec le texte, bornée', () => {
  assert.equal(areaHeight(50, 120, 400), 120);
  assert.equal(areaHeight(250, 120, 400), 250);
  assert.equal(areaHeight(900, 120, 400), 400);
});

test('readBig / writeBig : mémorisé par navigateur, sans stockage ça marche de même', () => {
  const store = new Map();
  const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => { store.set(k, v); } };
  assert.equal(readBig(storage), false);
  assert.equal(writeBig(storage, true), true);
  assert.equal(store.get(BIG_KEY), '1');
  assert.equal(readBig(storage), true);
  writeBig(storage, false);
  assert.equal(readBig(storage), false);
  assert.equal(readBig(null), false);
  assert.equal(writeBig(null, true), false);
  const broken = { getItem() { throw new Error('refusé'); }, setItem() { throw new Error('refusé'); } };
  assert.equal(readBig(broken), false);
  assert.equal(writeBig(broken, true), false);
});
