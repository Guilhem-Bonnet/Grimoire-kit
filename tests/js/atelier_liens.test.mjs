import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  atelierHash, cardStateText, exigenceBlocks, exigenceCard, hasRefs, reqFromParams, resolveChantier, splitRefs,
} from '../../web/workspace/spaces/atelier-liens-logic.js';

const kinds = (text) => splitRefs(text).map((p) => `${p.type}:${p.type === 'text' ? p.text : p.value}`);

test('splitRefs : exigence, tâche du kit et chantier, avec le texte autour', () => {
  assert.deepEqual(kinds('Voir REQ-001 et GAO-kanban-agent-001, puis [[Refonte UI]].'), [
    'text:Voir ', 'req:REQ-001', 'text: et ', 'task:GAO-kanban-agent-001', 'text:, puis ', 'chantier:Refonte UI', 'text:.',
  ]);
});

test('splitRefs : un identifiant de tâche garde ses mots et son numéro', () => {
  assert.deepEqual(kinds('GAO-cl-ture-d-l-001'), ['task:GAO-cl-ture-d-l-001']);
  assert.deepEqual(kinds('GAO-760-command-002'), ['task:GAO-760-command-002']);
  assert.deepEqual(kinds('GAO-sans-numero'), ['text:GAO-sans-numero']);
});

test('splitRefs : seules les formes exactes comptent', () => {
  assert.deepEqual(kinds('PREREQ-001 et REQ-12345 et REQ-ABC et REQ-001x'), ['text:PREREQ-001 et REQ-12345 et REQ-ABC et REQ-001x']);
  assert.deepEqual(kinds('[[ ]] et [[]]'), ['text:[[ ]] et [[]]']);
  assert.deepEqual(kinds('[[a\nb]]'), ['text:[[a\nb]]']);
  assert.deepEqual(kinds('(REQ-007)'), ['text:(', 'req:REQ-007', 'text:)']);
});

test('splitRefs : un texte sans référence reste un seul morceau, vide ou non texte compris', () => {
  assert.deepEqual(splitRefs('rien'), [{ type: 'text', text: 'rien' }]);
  assert.deepEqual(splitRefs(''), []);
  assert.deepEqual(splitRefs(null), []);
  assert.equal(hasRefs('rien'), false);
  assert.equal(hasRefs('voir REQ-002'), true);
  assert.equal(hasRefs(undefined), false);
});

test('splitRefs : répétable (l’état de l’expression ne fuit pas d’un appel à l’autre)', () => {
  assert.equal(hasRefs('REQ-001'), true);
  assert.equal(hasRefs('REQ-001'), true);
  assert.deepEqual(kinds('REQ-001'), ['req:REQ-001']);
  assert.deepEqual(kinds('REQ-001'), ['req:REQ-001']);
});

const rows = [
  { slug: 'refonte-ui', titre: 'Refonte de l’interface', etat: 'en-cours' },
  { slug: 'cassé', titre: 'Illisible', error: 'x' },
  { slug: 'site-vitrine', titre: 'Site vitrine' },
];

test('resolveChantier : par slug ou par titre, sans casse ni accents', () => {
  assert.equal(resolveChantier('refonte-ui', rows).slug, 'refonte-ui');
  assert.equal(resolveChantier('REFONTE DE L’INTERFACE', rows).slug, 'refonte-ui');
  assert.equal(resolveChantier('site  vitrine', rows).slug, 'site-vitrine');
  assert.equal(resolveChantier('Refonte de l’interfacé', rows).slug, 'refonte-ui');
});

test('resolveChantier : inconnu, illisible ou sans liste : rien', () => {
  assert.equal(resolveChantier('nope', rows), null);
  assert.equal(resolveChantier('Illisible', rows), null);
  assert.equal(resolveChantier('', rows), null);
  assert.equal(resolveChantier('x', null), null);
});

const doc = {
  phases: [
    { id: 'brief', blocks: [{ id: 'b1', kind: 'paragraphe', text: 'x' }] },
    { id: 'exigences', blocks: [
      { id: 'e1', kind: 'exigence', req: 'REQ-001', titre: 'Exporter', priorite: 'Must', text: '- REQ-001 [Must] : Exporter' },
      { id: 'e2', kind: 'exigence', req: 'REQ-002', titre: '', priorite: '', text: '- REQ-002' },
      { id: 'e3', kind: 'exigence', req: 'REQ-001', titre: 'Doublon', text: '- REQ-001' },
    ] },
  ],
};

test('exigenceBlocks : une entrée par identifiant, la première gagne', () => {
  const map = exigenceBlocks(doc);
  assert.deepEqual([...map.keys()], ['REQ-001', 'REQ-002']);
  assert.equal(map.get('REQ-001').block.id, 'e1');
  assert.equal(map.get('REQ-001').phase, 'exigences');
  assert.equal(exigenceBlocks(null).size, 0);
});

test('exigenceCard : titre, priorité et validation d’après la trace ; inconnue sans trace', () => {
  const trace = { requirements: [{ id: 'REQ-001', validated: true }, { id: 'REQ-002', validated: false }] };
  assert.deepEqual(exigenceCard(doc, 'REQ-001', trace), { req: 'REQ-001', titre: 'Exporter', priorite: 'Must', validee: true, par: '', le: '', moa: null, phase: 'exigences', blockId: 'e1' });
  assert.equal(exigenceCard(doc, 'REQ-002', trace).validee, false);
  assert.equal(exigenceCard(doc, 'REQ-002', trace).titre, 'REQ-002', 'sans titre : l’identifiant');
  assert.equal(exigenceCard(doc, 'REQ-001', null).validee, null);
  assert.equal(exigenceCard(doc, 'REQ-001', {}).validee, null);
  assert.equal(exigenceCard(doc, 'REQ-099', trace), null);
});

test('cardStateText : une mention vraie dans chaque cas, « Validée » seulement si le serveur l’atteste', () => {
  assert.equal(cardStateText({ validee: true, par: 'Ada', le: '2026-11-20T12:00:00Z' }), 'Validée par Ada le 20 novembre');
  assert.equal(cardStateText({ validee: true, par: '', le: '' }), 'Validée par la MOA');
  assert.equal(cardStateText({ validee: true, par: 'Ada', le: 'pas une date' }), 'Validée par Ada');
  assert.equal(cardStateText({ validee: false, moa: true }), 'Non validée');
  assert.equal(cardStateText({ validee: false, moa: null }), 'Non validée');
  assert.equal(cardStateText({ validee: false, moa: false }), 'Validation : aucune MOA déclarée');
  assert.equal(cardStateText({ validee: null, moa: null }), 'État de validation indisponible');
  assert.equal(cardStateText({}), 'État de validation indisponible');
});

test('exigenceCard : la MOA et l’attestation viennent de la trace, jamais d’ailleurs', () => {
  const trace = { moa: { declared: false }, requirements: [{ id: 'REQ-001', validated: true, validated_by: 'Ada', validated_at: '2026-11-20T12:00:00Z' }, { id: 'REQ-002', validated: false, validated_by: 'x' }] };
  const done = exigenceCard(doc, 'REQ-001', trace);
  assert.deepEqual([done.validee, done.par, done.le, done.moa], [true, 'Ada', '2026-11-20T12:00:00Z', false]);
  const open = exigenceCard(doc, 'REQ-002', trace);
  assert.deepEqual([open.validee, open.par, open.le], [false, '', ''], 'non validée : aucune attestation reprise');
  assert.equal(exigenceCard(doc, 'REQ-001', null).moa, null);
});

test('atelierHash : seuls les champs donnés figurent, dans un ordre stable', () => {
  assert.equal(atelierHash({ slug: 'a' }), '#atelier?chantier=a');
  assert.equal(atelierHash({ slug: 'a', phase: 'exigences' }), '#atelier?chantier=a&phase=exigences');
  assert.equal(atelierHash({ slug: 'a', phase: 'exigences', req: 'REQ-001', lecture: true }), '#atelier?chantier=a&phase=exigences&req=REQ-001&lecture=1');
  assert.equal(atelierHash({ slug: 'a b', lecture: false }), '#atelier?chantier=a+b');
});

test('reqFromParams : seule une forme valide est reprise', () => {
  assert.equal(reqFromParams({ req: 'REQ-001' }), 'REQ-001');
  assert.equal(reqFromParams({ req: '"><x' }), '');
  assert.equal(reqFromParams({ req: 'req-001' }), '');
  assert.equal(reqFromParams(null), '');
  assert.equal(reqFromParams({}), '');
});
