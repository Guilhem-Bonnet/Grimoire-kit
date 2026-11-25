import assert from 'node:assert/strict';
import { test } from 'node:test';

import { renderMarkdown, safeLinkUrl, safeWebUrl } from '../../web/workspace/markdown.js';

const link = (url, text = 'x') => renderMarkdown(`[${text}](${url})`);
const hasHref = (html) => /<a\b/.test(html);

const ACTIVE = [
  'javascript:alert(1)',
  'JaVaScRiPt:alert(1)',
  ' javascript:alert(1)',
  '\tjavascript:alert(1)',
  'java\tscript:alert(1)',
  'java\u0000script:alert(1)',
  'jav\u0001ascript:alert(1)',
  ' javascript:alert(1)',
  'javascript%3Aalert(1)',
  '%6Aavascript:alert(1)',
  '%256Aavascript:alert(1)',
  'data:text/html,hello',
  'DATA:text/html;base64,AAAA',
  'vbscript:msgbox(1)',
  'file:///etc/passwd',
  'blob:https://e.example/1',
  'unknown-scheme:foo',
  '//evil.example/x',
  '\\\\evil.example/x',
];

test('les schémas actifs ou inconnus ne produisent aucun lien', () => {
  for (const url of ACTIVE) {
    const html = link(url);
    assert.ok(!hasHref(html), `lien rendu pour ${JSON.stringify(url)} : ${html}`);
    assert.equal(safeLinkUrl(url), '', JSON.stringify(url));
  }
});

test('le texte du lien refusé reste, échappé', () => {
  const html = link('javascript:alert(1)', '<b>t</b>');
  assert.ok(!hasHref(html));
  assert.ok(html.includes('&lt;b&gt;t&lt;/b&gt;'), html);
  assert.ok(!html.includes('<b>'), html);
});

test('les schémas autorisés sont conservés', () => {
  const cases = ['https://example.com/a?b=1&c=2', 'http://example.com', 'HTTPS://example.com', 'mailto:a@example.com', '#ancre', 'docs/page.md', '/chemin/absolu', '?projet=x#vue', './x:y'];
  for (const url of cases) {
    const html = link(url);
    assert.ok(hasHref(html), `lien perdu pour ${url} : ${html}`);
    assert.notEqual(safeLinkUrl(url), '', url);
  }
});

test('les liens http(s) portent target et rel', () => {
  const html = link('https://example.com');
  assert.ok(html.includes('target="_blank"'), html);
  assert.ok(html.includes('rel="noopener noreferrer"'), html);
});

test("l'URL conservée est échappée dans l'attribut", () => {
  const html = link('https://e.example/a?b=1&c=2');
  assert.ok(html.includes('href="https://e.example/a?b=1&amp;c=2"'), html);
  const injected = renderMarkdown('[a](https://e.example" onmouseover="x)');
  assert.match(injected, /^<p><a href="[^"]*" target="_blank" rel="noopener noreferrer">a<\/a><\/p>$/, injected);
});

test('safeWebUrl exige une URL http(s) absolue', () => {
  assert.equal(safeWebUrl('https://github.com/o/r/pull/1'), 'https://github.com/o/r/pull/1');
  for (const url of ['javascript:alert(1)', '#x', '/relatif', 'mailto:a@b.c', '', null, undefined]) {
    assert.equal(safeWebUrl(url), '', String(url));
  }
});
