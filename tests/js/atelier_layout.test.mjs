import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  MAX_TOASTS, WIDTH_PREF_KEY, activePhase, capitalize, codeSegments, dropToast, focusForPref, focusStep, initialOpen, newOrphans,
  orphanNotice, phaseFromParams, phaseHash, phaseIndicator, pillSummary, prefKey, pushToast, readPref, readWidthPref, stepsOf,
  toneOf, ttlOf, writePref, writeWidthPref,
} from '../../web/workspace/spaces/atelier-layout-logic.js';

const memory = () => {
  const data = new Map();
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, v), data };
};

test('le choix replié / déplié est mémorisé par chantier, avec repli sûr sans stockage', () => {
  const store = memory();
  assert.equal(readPref(store, 'a'), null);
  assert.equal(writePref(store, 'a', false), true);
  assert.equal(writePref(store, 'b', true), true);
  assert.equal(readPref(store, 'a'), 'closed');
  assert.equal(readPref(store, 'b'), 'open');
  assert.equal(prefKey('a') === prefKey('b'), false);
  store.data.set(prefKey('c'), 'n’importe quoi');
  assert.equal(readPref(store, 'c'), null, 'une valeur inconnue est ignorée');
  const broken = { getItem() { throw new Error('bloqué'); }, setItem() { throw new Error('bloqué'); } };
  assert.equal(readPref(broken, 'a'), null);
  assert.equal(writePref(broken, 'a', true), false);
  assert.equal(readPref(null, 'a'), null);
  assert.equal(writePref(null, 'a', true), false);
});

test('sans choix mémorisé : déplié sur toile large, replié en tiroir ; le choix l’emporte', () => {
  assert.equal(initialOpen(null, true), true);
  assert.equal(initialOpen(null, false), false);
  assert.equal(initialOpen('closed', true), false);
  assert.equal(initialOpen('open', false), true);
});

test('la pastille dit ce qui attend', () => {
  assert.deepEqual(pillSummary({}), { text: 'Assistant', count: 0, tone: '' });
  assert.deepEqual(pillSummary({ busy: true }), { text: 'Assistant réfléchit', count: 0, tone: 'busy' });
  assert.deepEqual(pillSummary({ question: true }), { text: 'Question en cours', count: 1, tone: 'wait' });
  assert.deepEqual(pillSummary({ suggestions: 2 }), { text: '2 suggestions', count: 2, tone: 'wait' });
  assert.deepEqual(pillSummary({ question: true, suggestions: 1 }), { text: 'Question en cours, 1 suggestion', count: 2, tone: 'wait' });
  assert.equal(pillSummary({ permission: true, question: true }).text, 'Permission attendue, question en cours');
  assert.equal(pillSummary({ error: 'x', suggestions: 1 }).tone, 'error');
  assert.equal(pillSummary({ unavailable: true }).text, 'Assistant indisponible');
  assert.deepEqual(pillSummary({ unavailable: true, suggestions: 1 }), { text: '1 suggestion', count: 1, tone: 'wait' }, 'des suggestions qui attendent se disent même hors ligne');
});

test('le ton d’une notification : une erreur reste, un succès passe', () => {
  assert.equal(toneOf('Bloc enregistré.'), 'ok');
  assert.equal(toneOf('Commentaire ajouté.'), 'ok');
  assert.equal(toneOf('Challenge impossible : aucun agent d’hôte.'), 'error');
  assert.equal(toneOf('L’assistant n’a pas pu partir : refus'), 'error');
  assert.equal(toneOf('Le bloc a changé depuis la suggestion : elle est périmée, rien n’a été écrit.'), 'warn');
  assert.equal(ttlOf('error', false), 0, 'une erreur ne disparaît jamais seule');
  assert.ok(ttlOf('ok', false) > 0);
  assert.ok(ttlOf('ok', true) > ttlOf('ok', false), 'un lien d’action laisse plus de temps');
});

test('la file de notifications : pas de doublon, plafond, retrait par identifiant', () => {
  let list = [];
  list = pushToast(list, { id: 1, text: 'a' });
  list = pushToast(list, { id: 2, text: 'b' });
  list = pushToast(list, { id: 3, text: 'a' });
  assert.deepEqual(list.map((t) => t.id), [2, 3], 'le même texte remplace l’ancien');
  for (let i = 10; i < 10 + MAX_TOASTS + 2; i += 1) list = pushToast(list, { id: i, text: `t${i}` });
  assert.equal(list.length, MAX_TOASTS);
  assert.equal(list.at(-1).id, 10 + MAX_TOASTS + 1);
  assert.equal(dropToast(list, list[0].id).length, MAX_TOASTS - 1);
});

test('l’indicateur de phase vient de son état connu', () => {
  assert.equal(phaseIndicator('rempli'), 'complete');
  assert.equal(phaseIndicator('partiel'), 'en-cours');
  assert.equal(phaseIndicator('vide'), 'vide');
  assert.equal(phaseIndicator('absent'), 'vide');
  assert.equal(phaseIndicator(undefined), 'vide');
});

test('la phase active suit le défilement', () => {
  const order = ['brief', 'brainstorm', 'comprehension'];
  assert.equal(activePhase(order, new Set(['brainstorm']), 'brief'), 'brainstorm');
  assert.equal(activePhase(order, new Set(['brief', 'brainstorm']), 'brief'), 'brainstorm', 'la plus avancée dans la bande');
  assert.equal(activePhase(order, new Set(), 'brainstorm'), 'brainstorm', 'rien dans la bande : la phase courante reste');
  assert.equal(activePhase(order, new Set(['brainstorm']), 'brainstorm', true), 'comprehension', 'en bas du document : la dernière');
  assert.equal(activePhase([], new Set(), 'x', true), 'x');
  assert.equal(activePhase(order, new Set(), 'comprehension', false, true), order[0], 'tout en haut : la première phase');
  assert.equal(activePhase(order, new Set(['brainstorm']), 'brief', false, true), order[0]);
  assert.equal(activePhase(order, new Set(), 'brief', true, true), order[order.length - 1], 'document court : la fin l’emporte');
});

test('l’ancre d’URL d’une phase, relue au chargement', () => {
  assert.equal(phaseHash('mon-chantier', 'exigences'), '#atelier?chantier=mon-chantier&phase=exigences');
  assert.equal(phaseHash('a b', null), '#atelier?chantier=a+b');
  const ids = ['brief', 'exigences'];
  assert.equal(phaseFromParams({ phase: 'exigences' }, ids, 'brief'), 'exigences');
  assert.equal(phaseFromParams({ phase: 'inconnue' }, ids, 'brief'), 'brief');
  assert.equal(phaseFromParams(null, ids, 'brief'), 'brief');
});

test('la bande des quatre étapes : l’étape courante se déduit des données chargées', () => {
  const phases = (states) => ['brief', 'brainstorm', 'comprehension', 'exigences', 'cahier-des-charges'].map((id, i) => ({ id, state: states[i] }));
  const status = (list) => list.map((s) => s.status).join(',');
  assert.equal(status(stepsOf(phases(['vide', 'vide', 'vide', 'vide', 'vide']), null)), 'current,todo,todo,todo');
  assert.equal(status(stepsOf(phases(['rempli', 'rempli', 'partiel', 'vide', 'vide']), null)), 'current,todo,todo,todo');
  const cadre = phases(['rempli', 'rempli', 'rempli', 'rempli', 'vide']);
  assert.equal(status(stepsOf(cadre, { requirements: [] })), 'done,current,todo,todo', 'sans exigence reconnue, les exigences restent à faire');
  const reqs = [{ id: 'REQ-001', priority: 'must', validated: false, task_count: 0 }, { id: 'REQ-002', priority: 'should', validated: false, task_count: 0 }];
  assert.equal(status(stepsOf(cadre, { requirements: reqs })), 'done,done,current,todo');
  const validated = reqs.map((r) => ({ ...r, validated: true }));
  assert.equal(status(stepsOf(cadre, { requirements: validated })), 'done,done,done,current');
  const split = validated.map((r) => ({ ...r, task_count: 1 }));
  assert.equal(status(stepsOf(cadre, { requirements: split })), 'done,done,done,done');
  assert.deepEqual(stepsOf(cadre, null).map((s) => s.label), ['Cadrer', 'Exigences', 'Valider', 'Découper']);
  const early = phases(['vide', 'vide', 'vide', 'vide', 'vide']);
  assert.equal(status(stepsOf(early, { requirements: split })), 'current,todo,todo,todo', 'ordonné : rien de fait après l’étape courante');
});

test('les fils devenus orphelins après une réécriture', () => {
  const fil = (id, state) => ({ id, anchor: { state } });
  const before = [fil('a', 'ancre'), fil('b', 'orphelin'), fil('c', 'retrouve')];
  const after = [fil('a', 'orphelin'), fil('b', 'orphelin'), fil('c', 'ancre'), fil('d', 'orphelin')];
  assert.deepEqual(newOrphans(before, after), ['a', 'd'], 'b l’était déjà');
  assert.deepEqual(newOrphans(before, before), []);
  assert.deepEqual(newOrphans(undefined, undefined), []);
  assert.equal(orphanNotice(2), '2 commentaires n’ont plus leur passage');
  assert.equal(orphanNotice(1), '1 commentaire n’a plus son passage');
});

test('finitions : majuscule et accents graves rendus comme du code', () => {
  assert.equal(capitalize('agent'), 'Agent');
  assert.equal(capitalize(''), '');
  assert.deepEqual(codeSegments('Écris `REQ-001 : …` pour les suivre'), [
    { text: 'Écris ', code: false }, { text: 'REQ-001 : …', code: true }, { text: ' pour les suivre', code: false },
  ]);
  assert.deepEqual(codeSegments('sans code'), [{ text: 'sans code', code: false }]);
  assert.deepEqual(codeSegments('accent ` seul'), [{ text: 'accent ` seul', code: false }], 'un accent non fermé reste du texte');
  assert.deepEqual(codeSegments(''), []);
});

test('pleine largeur : le choix des panneaux se mémorise par navigateur, avec repli sûr sans stockage', () => {
  const store = memory();
  assert.equal(readWidthPref(store), null);
  assert.equal(writeWidthPref(store, 'panels'), true);
  assert.equal(readWidthPref(store), 'panels');
  assert.equal(writeWidthPref(store, 'wide'), true);
  assert.equal(readWidthPref(store), 'wide');
  assert.equal(writeWidthPref(store, 'autre'), false, 'une valeur inconnue n’est pas écrite');
  store.data.set(WIDTH_PREF_KEY, 'n’importe quoi');
  assert.equal(readWidthPref(store), null);
  const broken = { getItem() { throw new Error('bloqué'); }, setItem() { throw new Error('bloqué'); } };
  assert.equal(readWidthPref(broken), null);
  assert.equal(writeWidthPref(broken, 'wide'), false);
  assert.equal(readWidthPref(null), null);
  assert.equal(writeWidthPref(null, 'wide'), false);
});

test('pleine largeur : sans choix l’Atelier replie les panneaux, « panneaux » les garde ouverts', () => {
  assert.equal(focusForPref(null), 'on');
  assert.equal(focusForPref('wide'), 'on');
  assert.equal(focusForPref('panels'), 'off');
});

test('pleine largeur : l’arrivée retient l’état d’avant, la sortie le rend tel quel', () => {
  // Arrivée, panneaux ouverts avant : plein écran posé, l’ancien état retenu.
  assert.deepEqual(focusStep({ inAtelier: true, pref: null, held: null, focus: 'off' }), { focus: 'on', held: 'off' });
  // Un plein écran posé avant l’Atelier est retenu puis rendu.
  assert.deepEqual(focusStep({ inAtelier: true, pref: null, held: null, focus: 'on' }), { focus: 'on', held: 'on' });
  assert.deepEqual(focusStep({ inAtelier: false, pref: null, held: 'on', focus: 'on' }), { focus: 'on', held: null });
  assert.deepEqual(focusStep({ inAtelier: false, pref: null, held: 'off', focus: 'on' }), { focus: 'off', held: null });
  // Le choix mémorisé « panneaux » est respecté à l’arrivée.
  assert.deepEqual(focusStep({ inAtelier: true, pref: 'panels', held: null, focus: 'off' }), { focus: 'off', held: 'off' });
  // Une rubrique de plus dans l’Atelier ne touche à rien (le « Plein écran » de la barre garde la main).
  assert.deepEqual(focusStep({ inAtelier: true, pref: null, held: 'off', focus: 'off' }), { focus: 'off', held: 'off' });
  // Hors Atelier sans rien de retenu : inchangé, valeur normalisée.
  assert.deepEqual(focusStep({ inAtelier: false, pref: null, held: null, focus: 'on' }), { focus: 'on', held: null });
  assert.deepEqual(focusStep({ inAtelier: false, pref: null, held: undefined, focus: undefined }), { focus: 'off', held: null });
  assert.deepEqual(focusStep({ inAtelier: true, pref: null, held: undefined, focus: 'off' }), { focus: 'on', held: 'off' });
});
