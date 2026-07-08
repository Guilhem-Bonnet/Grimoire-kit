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
})();
