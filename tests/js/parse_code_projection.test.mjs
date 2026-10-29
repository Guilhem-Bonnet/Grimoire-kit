import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseCodeProjection } from '../../web/workspace/spaces/code-projection.js';

// Texte tel que `_code_node_text` (src/grimoire/memory/projections.py) l'écrit.
const TEXT = [
  'Code symbol: load_config',
  'File: src/grimoire/core/config.py',
  'Module: grimoire.core.config',
  'Kind: function',
  'Lines: 10-14',
  'Test: no',
  'Public: yes',
  'Docstring:',
  'Charge la configuration.',
  'Sur deux lignes.',
  'Source excerpt:',
  'def load_config(path: str) -> dict:',
  '    with open(path) as fh:',
  '        return json.load(fh)',
].join('\n');

test('texte de projections.py -> champs', () => {
  const p = parseCodeProjection(TEXT);
  assert.equal(p.name, 'load_config');
  assert.equal(p.file, 'src/grimoire/core/config.py');
  assert.equal(p.module, 'grimoire.core.config');
  assert.equal(p.kind, 'function');
  assert.equal(p.lines, '10-14');
  assert.equal(p.test, false);
  assert.equal(p.signature, 'def load_config(path: str) -> dict:');
  assert.equal(p.docstring, 'Charge la configuration.\nSur deux lignes.');
  assert.equal(p.source, 'def load_config(path: str) -> dict:\n    with open(path) as fh:\n        return json.load(fh)');
});

test('sans docstring ni source, test oui', () => {
  const p = parseCodeProjection('Code symbol: t\nFile: a.py\nModule: a\nKind: function\nLines: 1-2\nTest: yes\nPublic: no');
  assert.equal(p.test, true);
  assert.equal(p.source, '');
  assert.equal(p.signature, '');
});

test('texte non reconnu -> null, sans exception', () => {
  for (const bad of ['', 'Décision : pinner Qdrant', 'Test chunk: a.py\nModule: a', null, undefined, 42, {}]) {
    assert.equal(parseCodeProjection(bad), null);
  }
});
