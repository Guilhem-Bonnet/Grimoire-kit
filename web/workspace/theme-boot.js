// Thème et contraste avant le premier paint. Fichier séparé, chargé de façon
// bloquante (script classique, sans defer) en tête de index.html : la politique
// de sécurité du cockpit interdit tout script en ligne (voir CSP, #1366).
// La clé par projet du choix n'est lisible qu'après `boot()`, shell.js en
// recopie donc une version non scopée.
(function () {
  try {
    var d = document.documentElement;
    var get = function (k) { try { return localStorage.getItem('grimoire.workspace.' + k); } catch (e) { return null; } };
    var t = get('theme');
    d.dataset.theme = t === 'light' || t === 'dark' ? t
      : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var c = get('contrast');
    d.dataset.contrast = c === 'high' || c === 'standard' ? c
      : (matchMedia('(prefers-contrast: more)').matches ? 'high' : 'standard');
  } catch (e) { /* sans script : le CSS suit prefers-color-scheme */ }
})();
