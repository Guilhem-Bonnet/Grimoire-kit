import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  afterMessage, approveCommand, changedRequirements, confirmLine, parseRequirement, unknownMessage, validationOf,
} from '../../web/workspace/spaces/atelier-devalidation-logic.js';

const BASE = '- REQ-001 [Must] : maintenir un bouton pour dicter\n  - la transcription arrive en moins de 2 s\n  - [ ] le texte reste local';

test('parseRequirement : un bloc d’exigence, ou rien', () => {
  assert.equal(parseRequirement('Un paragraphe'), null);
  assert.equal(parseRequirement('- Une puce'), null);
  assert.equal(parseRequirement(null), null);
  assert.equal(parseRequirement(BASE).req, 'REQ-001');
  assert.equal(parseRequirement('- **REQ-002** [Should] : gras').req, 'REQ-002');
});

test('changedRequirements : le texte de l’exigence change', () => {
  assert.deepEqual(changedRequirements(BASE, BASE.replace('dicter', 'dicter une idée')), ['REQ-001'], 'formulation');
  assert.deepEqual(changedRequirements(BASE, BASE.replace('[Must]', '[Should]')), ['REQ-001'], 'priorité');
  assert.deepEqual(changedRequirements(BASE, BASE.replace('2 s', '3 s')), ['REQ-001'], 'critère réécrit');
  assert.deepEqual(changedRequirements(BASE, `${BASE}\n  - un critère de plus`), ['REQ-001'], 'critère ajouté');
  assert.deepEqual(changedRequirements(BASE, '- REQ-001 [Must] : maintenir un bouton pour dicter'), ['REQ-001'], 'critères retirés');
  assert.deepEqual(changedRequirements(BASE, 'Plus une exigence'), ['REQ-001'], 'exigence retirée du bloc');
  assert.deepEqual(changedRequirements(BASE, BASE.replace('REQ-001', 'REQ-009')), ['REQ-001'], 'identifiant changé');
});

test('changedRequirements : ce que l’empreinte du serveur ne lit pas ne déclenche rien', () => {
  assert.deepEqual(changedRequirements(BASE, `${BASE}   \n`), [], 'espaces et fin de ligne');
  assert.deepEqual(changedRequirements(BASE, BASE.replace('[ ] le texte', '[x] le texte')), [], 'case cochée d’un critère');
  assert.deepEqual(changedRequirements(BASE, BASE.replace('  - la transcription arrive en moins de 2 s\n  - [ ] le texte reste local', '  - [ ] le texte reste local\n  - la transcription arrive en moins de 2 s')), [], 'ordre des critères');
  assert.deepEqual(changedRequirements(BASE, BASE.replace('- REQ-001', '* REQ-001')), [], 'puce');
  assert.deepEqual(changedRequirements(BASE, BASE), []);
});

test('changedRequirements : un bloc qui n’est pas une exigence ne demande rien', () => {
  assert.deepEqual(changedRequirements('Un paragraphe', 'Un autre'), []);
  assert.deepEqual(changedRequirements('', BASE), [], 'une exigence neuve n’a pas de validation à défaire');
});

test('validationOf : validée, non validée, inconnue', () => {
  const trace = { requirements: [{ id: 'REQ-001', validated: true, validated_by: 'Ada', validated_at: '2026-11-27T10:00:00+00:00' }, { id: 'REQ-002', validated: false }] };
  assert.deepEqual(validationOf(['REQ-001'], trace), { validated: [{ req: 'REQ-001', by: 'Ada', at: '2026-11-27T10:00:00+00:00' }], unknown: [] });
  assert.deepEqual(validationOf(['REQ-002', 'REQ-009'], trace), { validated: [], unknown: [] }, 'non validée ou absente d’une trace lue');
  assert.deepEqual(validationOf(['REQ-001'], null), { validated: [], unknown: ['REQ-001'] }, 'trace indisponible');
  assert.deepEqual(validationOf(['REQ-001'], {}), { validated: [], unknown: ['REQ-001'] });
});

test('les phrases : commande, confirmation, rappel, mention discrète', () => {
  assert.equal(approveCommand('REQ-001', 'principal'), 'grimoire cadrage approve REQ-001');
  assert.equal(approveCommand('REQ-001', 'un-chantier'), 'grimoire cadrage approve REQ-001 --chantier un-chantier');
  assert.match(confirmLine({ req: 'REQ-001', by: 'Ada', at: '2026-11-27T10:00:00+00:00' }), /^REQ-001 est validée par Ada le 27 novembre\. La modifier annule cette validation : il faudra la revalider\.$/);
  assert.match(confirmLine({ req: 'REQ-001', by: '', at: '' }), /^REQ-001 est validée par la MOA\. La modifier/);
  assert.match(afterMessage(['REQ-001'], 'un'), /^REQ-001 n’est plus validée : revalide-la au terminal \(grimoire cadrage approve REQ-001 --chantier un\)\.$/);
  assert.match(afterMessage(['REQ-001', 'REQ-002'], 'principal'), /ne sont plus validées : revalide-les/);
  assert.match(unknownMessage(['REQ-001'], 'un'), /^État de validation inconnu pour REQ-001 : si elle était validée, elle devra être revalidée/);
  assert.match(unknownMessage(['REQ-001', 'REQ-002'], 'un'), /elles étaient validées, elles devront être revalidées/);
});
