/* grimoire-mode.js — Monde courant : vitrine (public) ou atelier (local).
   Chargé dans <head>, avant le premier rendu.
   - Les pages d'un seul monde FORCENT leur mode (et le mémorisent).
   - Les pages partagées (patterns, extensions, documentation) héritent
     du dernier monde visité : même page, deux habillages.
   Pose mode-vitrine | mode-atelier sur <html> ; expose window.GrimoireMode.
   ============================================================== */
(function () {
  'use strict';
  var FORCE = {
    'index.html': 'vitrine', 'demo.html': 'vitrine', 'portfolio.html': 'vitrine',
    'anatomy.html': 'vitrine', 'game-ui.html': 'vitrine',
    'atelier.html': 'atelier', 'blueprints.html': 'atelier',
    'observability.html': 'atelier', 'memory.html': 'atelier', 'kanban.html': 'atelier'
  };
  var page = location.pathname.replace(/\/$/, '').split('/').pop() || 'index.html';
  var mode = FORCE[page] || null;
  try {
    if (mode) localStorage.setItem('grimoire.mode', mode);
    else mode = localStorage.getItem('grimoire.mode') || 'vitrine';
  } catch (e) { mode = mode || 'vitrine'; }
  document.documentElement.classList.add(mode === 'atelier' ? 'mode-atelier' : 'mode-vitrine');
  window.GrimoireMode = mode;

  /* Base du manuel. Le site publié sert mkdocs sous /docs/, à côté de ces
     pages ; l'atelier local (`grimoire serve`) ne sert que l'interface, sans
     le manuel — on renvoie alors vers la version en ligne plutôt que vers un
     404. Le test porte sur qui sert la page, pas sur le mode : `mode-atelier`
     est un habillage, il ne dit rien de l'origine.

     Le protocole vient en premier : ouvert depuis le disque (`file://`),
     `location.hostname` vaut la chaîne vide et aucun test d'hôte ne le
     rattrape — c'est pourtant le cas où un chemin relatif est le plus sûrement
     cassé. */
  var elsewhere = location.protocol === 'file:'
    || /^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);
  window.GrimoireDocsBase = elsewhere
    ? 'https://guilhem-bonnet.github.io/Grimoire-kit/docs/'
    : 'docs/';
})();
