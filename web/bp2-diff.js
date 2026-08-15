/* bp2-diff.js — Diff visuel git du graphe (P3.4)
   « Le diff git est la revue » n'a de valeur que si le diff se lit. Comparer
   deux .blueprint.json ligne à ligne montre des accolades ; cet onglet montre
   ce qui a changé de sens : nodes et liens ajoutés, retirés, modifiés — et le
   teinte directement sur la toile.
   Le moteur vit côté serveur (grimoire.tools.blueprint_diff) : le Studio ne
   recalcule rien, il affiche. Hors atelier (pas d'API), l'onglet le dit.
   ========================================================================== */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  let last = null;          // dernier diff obtenu
  let painting = false;     // teinte active sur la toile

  function whenReady(cb) {
    if (window.BPEditor && window.Atelier) return cb();
    setTimeout(() => whenReady(cb), 60);
  }

  whenReady(() => {
    const E = window.BPEditor;
    const esc = Atelier.esc;

    /* ── Teinte de la toile ──
       Un node ajouté ou modifié se voit sur le graphe, pas seulement dans une
       liste : c'est là que le relecteur regarde. Les retirés n'existent plus
       sur la toile — ils restent listés dans le panneau. */
    function paint() {
      $$('.bp-node').forEach(el => el.classList.remove('df-add', 'df-mod'));
      if (!painting || !last) return;
      const mark = (list, cls) => (list || []).forEach(n => {
        const el = document.querySelector(`.bp-node[data-id="${n.id}"]`);
        if (el) el.classList.add(cls);
      });
      mark(last.nodes_added, 'df-add');
      mark(last.nodes_changed, 'df-mod');
    }

    function row(icon, cls, label, detail) {
      return `<div class="df-row ${cls}"><span class="df-ico">${icon}</span>
        <span><b>${esc(label)}</b>${detail ? `<span class="df-det">${esc(detail)}</span>` : ''}</span></div>`;
    }

    function render() {
      const panel = $('#panel-diff');
      const badge = $('#diff-count');
      if (!panel) return;
      if (!Atelier.online) {
        badge.style.display = 'none';
        panel.innerHTML = `<p class="empty">Le diff compare le blueprint à sa version commitée.<br><br>
          Il demande l'atelier local (<code>grimoire serve</code>) — hors ligne, il n'y a pas de dépôt à interroger.</p>`;
        return;
      }
      if (!last) {
        badge.style.display = 'none';
        panel.innerHTML = `<p class="empty">Comparer ce blueprint à sa dernière version commitée.</p>
          <button class="at-btn sm acc" id="df-run">COMPARER À HEAD</button>`;
        $('#df-run').addEventListener('click', run);
        return;
      }
      if (!last.tracked) {
        badge.style.display = 'none';
        panel.innerHTML = `<div class="bp-prop"><div class="k">Jamais commité</div>
          <div class="v soft">Ce blueprint n'existe pas encore dans l'historique : il n'y a rien à comparer. Compilez et commitez-le, le diff aura alors un sens.</div></div>
          <button class="at-btn sm ghost" id="df-run">RÉESSAYER</button>`;
        $('#df-run').addEventListener('click', run);
        return;
      }
      const n = (last.nodes_added.length + last.nodes_removed.length + last.nodes_changed.length
        + last.edges_added.length + last.edges_removed.length);
      badge.style.display = n ? '' : 'none';
      badge.textContent = n;

      const lien = e => `${e.contract}${e.channel && e.channel !== 'happy' ? ' · ' + e.channel : ''}`;
      let html = `<div class="bp-prop"><div class="k">Depuis la dernière version commitée</div>
        <div class="v">${esc(last.summary)}</div></div>`;
      if (!last.changed) {
        html += `<p class="empty">Les positions ont pu bouger : ranger le graphe ne change rien à ce qu'il décrit.</p>`;
      } else {
        html += '<div class="bp-diff">'
          + last.nodes_added.map(x => row('+', 'add', x.label, x.path.length ? 'dans un sous-flow' : '')).join('')
          + last.nodes_removed.map(x => row('−', 'del', x.label, x.path.length ? 'dans un sous-flow' : '')).join('')
          + last.nodes_changed.map(x => row('~', 'mod', x.label, x.fields.join(', '))).join('')
          + last.edges_added.map(x => row('+', 'add', 'lien ' + lien(x), '')).join('')
          + last.edges_removed.map(x => row('−', 'del', 'lien ' + lien(x), '')).join('')
          + '</div>';
      }
      html += `<button class="at-btn sm${painting ? ' acc' : ' ghost'}" id="df-paint" style="margin-top:10px">
          ${painting ? 'MASQUER SUR LA TOILE' : 'MONTRER SUR LA TOILE'}</button>
        <button class="at-btn sm ghost" id="df-run">RECALCULER</button>`;
      panel.innerHTML = html;
      $('#df-run').addEventListener('click', run);
      $('#df-paint').addEventListener('click', () => { painting = !painting; paint(); render(); });
    }

    async function run() {
      const panel = $('#panel-diff');
      panel.innerHTML = '<p class="empty">comparaison en cours…</p>';
      try {
        last = await Atelier.api('/api/blueprints/' + encodeURIComponent(E.bpId()) + '/diff');
      } catch (e) {
        last = null;
        panel.innerHTML = `<p class="empty">Comparaison impossible — ${esc(String(e.message || e))}</p>
          <button class="at-btn sm ghost" id="df-run">RÉESSAYER</button>`;
        $('#df-run').addEventListener('click', run);
        return;
      }
      render();
      paint();
    }

    /* Le diff se périme dès qu'on touche au graphe : mieux vaut ne rien
       afficher qu'un état faux. */
    ['mutated', 'bp-loaded'].forEach(ev => E.on(ev, () => {
      if (!last) return;
      last = null; painting = false;
      $$('.bp-node').forEach(el => el.classList.remove('df-add', 'df-mod'));
      render();
    }));
    E.on('rendered', paint);

    render();
    window.BP2Diff = { run, state: () => last };
  });
})();
