/* bp2-manual.js — F1 ouvre le manuel sur ce qui est sélectionné
   Le lexique explique un terme sans quitter la toile ; le manuel, lui, explique
   pourquoi. Plutôt que de demander à l'utilisateur d'aller chercher la page,
   la toile sait où elle est : un node de pattern porte sa référence, et le
   catalogue porte l'emplacement de sa fiche dans le manuel (`docPath`).

   Rien n'est codé en dur ici : si une famille est renommée, `docPath` change
   avec elle et ce module suit sans modification.
   ========================================================================== */
(function () {
  'use strict';

  var NODAL = 'nodal/';
  var REFERENCE = 'nodal/reference/';

  /* Chaque terme du lexique qui a une page dans le manuel. Un terme absent
     d'ici n'aura simplement pas de lien — mieux vaut pas de lien qu'un lien
     mort. Ces chemins sont vérifiés contre le manuel rendu par
     tests/unit/test_doc_reference.py. */
  var PAGES = {
    'agent': 'nodal/reference/unit/',
    'orchestrateur': 'nodal/concepts/primitives/',
    'déclencheur ▶': 'nodal/reference/reference/',
    'branchement MCP': 'nodal/concepts/portes/',
    'pattern': 'nodal/reference/patterns/',
    'preuve': 'nodal/reference/contrats/evidence-pack/',
    'porte': 'nodal/concepts/portes/',
    'mission brief': 'nodal/reference/contrats/task-envelope/',
    'blueprint': 'nodal/'
  };

  function base() {
    /* Résolue par grimoire-mode.js : `docs/` sur le site publié, l'URL
       publique quand c'est l'atelier local qui sert (il n'embarque pas le
       manuel). */
    return window.GrimoireDocsBase || 'docs/';
  }

  function selectedNode() {
    return document.querySelector('.bp-node.sel');
  }

  /* La référence du standard portée par un node de pattern (ORC-01, QUA-04…).
     On la lit dans le DOM plutôt que dans l'état de l'éditeur : ce module n'a
     alors aucune prise sur le cœur, et ne peut rien y casser. */
  function selectedRef() {
    var node = selectedNode();
    var tag = node && node.querySelector('.ref-tag');
    return tag ? tag.textContent.trim() : null;
  }

  function targetForSelection() {
    var ref = selectedRef();
    var known = ref && window.Atelier && Atelier.byRef && Atelier.byRef[ref];
    if (known && known.docPath) return known.docPath;
    if (selectedNode()) return REFERENCE;
    return NODAL;
  }

  function open(path) {
    window.open(base() + path, '_blank', 'noopener');
  }

  function isTyping(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    var tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'F1') return;
    if (isTyping(e.target)) return;
    e.preventDefault();
    open(targetForSelection());
  });

  window.BP2Manual = {
    open: open, target: targetForSelection, base: base, pages: PAGES
  };
})();
