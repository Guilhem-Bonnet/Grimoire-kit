import assert from 'node:assert/strict';
import { test } from 'node:test';
import { routingLine } from '../../web/workspace/activity_routing.js';

test('une entrée sans routage ne donne aucune ligne', () => {
  assert.equal(routingLine({ label: 'session x lancée' }), '');
  assert.equal(routingLine(null), '');
  assert.equal(routingLine({ routing: 42 }), '');
  assert.equal(routingLine({ routing: '   ' }), '');
});

test('la ligne de routage est rendue telle quelle, texte seul', () => {
  const line = 'haiku (palier cheap, classe V0) — défaut du kit pour cette classe';
  assert.equal(routingLine({ routing: line }), line);
  assert.equal(routingLine({ routing: '<img src=x onerror=alert(1)>' }), '<img src=x onerror=alert(1)>');
});

test('les caractères de contrôle sont retirés et la longueur bornée', () => {
  assert.equal(routingLine({ routing: 'a\u001b[2Jb\nc' }), 'a [2Jb c');
  const long = routingLine({ routing: 'x'.repeat(5000) });
  assert.equal(long.length, 400);
  assert.ok(long.endsWith('…'));
});
