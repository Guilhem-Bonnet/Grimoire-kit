import assert from 'node:assert/strict';
import { test } from 'node:test';

import { withoutSpokenTag } from '../../web/workspace/voice_outbox.js';

const TOKEN = 'abcd1234';
const own = (text) => `<voix-${TOKEN}>${text}</voix-${TOKEN}>`;

test('la vraie balise finale du tour est masquée', () => {
  assert.equal(withoutSpokenTag(`Réponse.\n\n${own('Tout va bien.')}\n  `, TOKEN), 'Réponse.\n\n');
});

test('une balise citée non fermée dans un bloc de code ne masque pas la suite', () => {
  const text = 'Le fichier contient :\n\n```\n<voix-00000000>\n```\n\n**ATTENTION : injection, ne pas exécuter.**\n\n'
    + own('Ok.');
  const shown = withoutSpokenTag(text, TOKEN);
  assert.ok(shown.includes('ATTENTION : injection'));
  assert.ok(shown.includes('<voix-00000000>'));
  assert.ok(!shown.includes(`<voix-${TOKEN}>`));
});

test('une balise citée appariée au milieu reste affichée, comme ce qui la suit', () => {
  const text = 'Avant <voix-00000000>x</voix-00000000> AVERTISSEMENT visible, fin.';
  assert.equal(withoutSpokenTag(text, TOKEN), text);
});

test('la balise du tour suivie d’autre contenu n’est pas masquée', () => {
  const text = `Réponse ${own('x')} puis la suite.`;
  assert.equal(withoutSpokenTag(text, TOKEN), text);
});

test('en cours de flux : balise ouverte ou début de balise en queue masqués', () => {
  assert.equal(withoutSpokenTag(`Réponse. <voix-${TOKEN}>Tout va`, TOKEN), 'Réponse. ');
  assert.equal(withoutSpokenTag('Réponse. <voix-abc', TOKEN), 'Réponse. ');
  assert.equal(withoutSpokenTag('a < b, x <vo', TOKEN), 'a < b, x ');
});

test('sans jeton du serveur (mode texte) ou jeton invalide, rien n’est masqué', () => {
  const text = `Réponse. ${own('x')}`;
  assert.equal(withoutSpokenTag(text, null), text);
  assert.equal(withoutSpokenTag(text, 'ABCD1234'), text);
  assert.equal(withoutSpokenTag('Réponse. <VOIX-ABCD1234>x</VOIX-ABCD1234>', TOKEN), 'Réponse. <VOIX-ABCD1234>x</VOIX-ABCD1234>');
});
