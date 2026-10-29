// Pilote automatique (P83) — l'interrupteur à glissière de la barre du haut.
//
// Un `role="switch"` (bouton natif : Espace et Entrée le règlent) dont `aria-checked`
// est l'état relu sur `GET autonomy/autopilot`, jamais celui du dernier clic. Glisser
// à droite l'active : geste humain, clé du cockpit demandée (le jeton de session ne
// suffit pas, voir `mandate.ensure_control_key`) et gardée en mémoire de page seulement.
// Glisser à gauche le coupe. Sans mandat actif, ou hors d'un projet, l'interrupteur est
// désactivé et dit pourquoi en une ligne. La barre d'état n'écrit « activé depuis … » que
// pilote activé : coupé, elle ne laisse rien.

import { api, host } from './api.js';


const $ = (id) => document.getElementById(id);

/** « 14 h 02 », heure locale — l'heure d'un geste, pas un âge. */
export function clockText(iso) {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return '';
  return `${moment.getHours()} h ${String(moment.getMinutes()).padStart(2, '0')}`;
}

/** Ce que l'interrupteur dit quand il est désactivé (`''` : réglable). */
export function whyDisabled({ scopeAll, readOnly, mandate, autopilot }) {
  if (scopeAll) return 'Choisissez un projet : le pilote se règle projet par projet.';
  if (readOnly) return 'Cockpit en lecture seule : le pilote se règle depuis le projet.';
  if (!mandate || mandate.error) return 'Pilote indisponible : état du mandat illisible.';
  if (!mandate.active) {
    // Déjà allumé quand le mandat tombe : on garde la main pour le couper, rien à désactiver.
    if (autopilot && autopilot.active) return '';
    return 'Aucun mandat actif : accordez-en un pour activer le pilote.';
  }
  return '';
}

export function createAutopilot({ scopeAll, onChange }) {
  const button = $('autopilot-switch');
  const why = $('autopilot-why');
  const status = $('st-autopilot');
  const dialog = $('autopilot-key');
  if (!button) return { refresh: async () => {}, get state() { return null; } };

  let state = { autopilot: { active: false }, mandate: null, error: '' };
  let controlKey = '';
  let busy = false;
  let notice = '';

  function paint() {
    const { autopilot, mandate } = state;
    const active = !!(autopilot && autopilot.active);
    const reason = state.error || whyDisabled({ scopeAll, readOnly: host.readOnly, mandate, autopilot });
    button.setAttribute('aria-checked', String(active));
    button.dataset.state = active ? 'on' : 'off';
    button.disabled = !!reason || busy;
    why.textContent = reason || notice;
    why.hidden = !(reason || notice);
    why.dataset.tone = reason ? '' : (notice ? 'bad' : '');
    button.title = reason || (active
      ? 'Le pilote tranche les cas bloqués ; seul le réservé à l’humain reste ici. Glisser à gauche pour le couper.'
      : 'Glisser à droite : l’orchestrateur tranche les cas bloqués, sauf la liste réservée à l’humain.');
    if (autopilot && autopilot.active) {
      const since = clockText(autopilot.since);
      status.textContent = `Pilote automatique : activé${since ? ` depuis ${since}` : ''}`;
      status.hidden = false;
    } else {
      status.textContent = '';
      status.hidden = true;
    }
  }

  async function refresh() {
    if (scopeAll) { state = { autopilot: null, mandate: null, error: '' }; paint(); return; }
    try {
      const [autopilot, mandate] = await Promise.all([
        api.autopilotState(),
        api.mandateState().catch(() => ({ error: true })),
      ]);
      state = { autopilot, mandate, error: '' };
    } catch {
      state = { autopilot: null, mandate: null, error: 'Pilote indisponible : le serveur ne répond pas.' };
    }
    paint();
  }

  function askKey(reason) {
    return new Promise((resolve) => {
      const input = $('autopilot-key-input');
      const note = $('autopilot-key-note');
      const path = state.mandate && state.mandate.control_key_path;
      note.textContent = reason || '';
      $('autopilot-key-path').textContent = path ? `cat ${path}` : 'cat ~/.grimoire/control/cockpit-key';
      input.value = '';
      let value = null;
      const done = () => { dialog.removeEventListener('close', done); resolve(value); };
      dialog.addEventListener('close', done);
      $('autopilot-key-form').onsubmit = (event) => {
        event.preventDefault();
        value = input.value.trim();
        dialog.close();
      };
      $('autopilot-key-cancel').onclick = () => dialog.close();
      dialog.showModal();
      input.focus();
    });
  }

  async function set(on) {
    busy = true;
    notice = '';
    paint();
    let note = '';
    try {
      for (;;) {
        if (!controlKey) {
          const given = await askKey(note);
          if (!given) return;
          controlKey = given;
        }
        try {
          await api.autopilotSet(on, controlKey);
          break;
        } catch (error) {
          if (error && error.code === 403) { controlKey = ''; note = 'Clé refusée : relisez-la dans votre terminal.'; continue; }
          throw error;
        }
      }
      await refresh();
      if (onChange) onChange(on);
    } catch (error) {
      notice = `Pilote : ${error && error.message ? error.message : 'geste refusé'}`;
    } finally {
      busy = false;
      paint();
    }
  }

  button.addEventListener('click', () => {
    if (button.disabled) return;
    set(button.getAttribute('aria-checked') !== 'true');
  });
  paint();
  return { refresh, get state() { return state; } };
}


