// Prévenir d'une décision attendue, onglet masqué ou fermé (I1).
//
// Trois canaux, du plus discret au plus fort, tous nourris par le compteur « À traiter »
// de la coque (`shell.js`, `renderAttentionCount`) — ce module ne lit rien lui-même :
//   - le titre de l'onglet : « (N) Grimoire — vue de travail » ;
//   - une pastille sur le favicon ;
//   - une notification du navigateur par NOUVELLE question, si l'interrupteur est actif.
// À zéro, le titre et le favicon redeviennent ce qu'ils étaient au chargement.
//
// L'interrupteur est le seul à rompre le gel de l'onglet masqué (live.js) : sans lui, rien
// ne change. La déduplication est par identifiant de question, gardée en localStorage
// (toujours sous try/catch : un navigateur privé ou sans stockage ne casse rien).

const STORE_ON = 'grimoire.notify.on';
const STORE_SEEN = 'grimoire.notify.seen';
const SEEN_MAX = 300;

const read = (key) => { try { return window.localStorage.getItem(key); } catch { return null; } };
const write = (key, value) => { try { window.localStorage.setItem(key, value); } catch { /* sans stockage : pas de mémoire */ } };

const supported = () => typeof window.Notification === 'function';

function loadSeen() {
  try {
    const list = JSON.parse(read(STORE_SEEN) || '[]');
    return new Set(Array.isArray(list) ? list.map(String) : []);
  } catch { return new Set(); }
}

function saveSeen(seen) {
  write(STORE_SEEN, JSON.stringify([...seen].slice(-SEEN_MAX)));
}

/** Le favicon avec sa pastille, ou `null` si le navigateur ne dessine pas. */
function badgedIcon(base, size = 64) {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext && canvas.getContext('2d');
  if (!ctx) return null;
  const style = getComputedStyle(document.documentElement);
  const token = (name) => style.getPropertyValue(name).trim() || 'currentColor';
  const accent = token('--acc');
  ctx.fillStyle = accent;
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(4, 4, size - 8, size - 8, 14) : ctx.rect(4, 4, size - 8, size - 8);
  ctx.fill();
  if (base) ctx.drawImage(base, 4, 4, size - 8, size - 8);
  ctx.fillStyle = token('--bad');
  ctx.beginPath();
  ctx.arc(size - 16, 16, 14, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = token('--onfill');
  ctx.lineWidth = 4;
  ctx.stroke();
  try { return canvas.toDataURL('image/png'); } catch { return null; }
}

export function createNotify({ live, openDecisions, refresh, button, announce }) {
  const baseTitle = document.title;
  const existingIcon = document.querySelector('link[rel~="icon"]');
  const baseHref = existingIcon ? existingIcon.getAttribute('href') : null;
  let madeIcon = null;
  let count = 0;
  let on = false;
  let seen = loadSeen();
  let baselined = false;

  const say = (text) => { if (announce) announce(text); };

  /** Titre « (N) … » et pastille ; zéro les rend tels qu'ils étaient. */
  function paintTab() {
    document.title = count > 0 ? `(${count}) ${baseTitle}` : baseTitle;
    if (count > 0) {
      const apply = (base) => {
        const href = badgedIcon(base);
        if (!href || count === 0) return;
        if (!madeIcon) {
          madeIcon = document.createElement('link');
          madeIcon.rel = 'icon';
          madeIcon.dataset.role = 'notify-icon';
          if (existingIcon) existingIcon.disabled = true;
          document.head.append(madeIcon);
        }
        madeIcon.type = 'image/png';
        madeIcon.href = href;
      };
      if (baseHref) {
        const image = new Image();
        image.onload = () => apply(image);
        image.onerror = () => apply(null);
        image.src = baseHref;
      } else {
        apply(null);
      }
    } else if (madeIcon) {
      madeIcon.remove();
      madeIcon = null;
      if (existingIcon) existingIcon.disabled = false;
    }
  }

  function paintButton() {
    if (!button) return;
    button.setAttribute('aria-pressed', String(on));
    button.dataset.state = !supported() ? 'unsupported' : (Notification.permission === 'denied' ? 'denied' : (on ? 'on' : 'off'));
  }

  function fire(item) {
    try {
      const popup = new Notification(`Décision attendue — ${item.title || item.task_id}`, {
        body: item.reason || '',
        tag: `grimoire-${item.question_id}`,
      });
      popup.onclick = () => {
        try { window.focus(); } catch { /* sans focus : l'onglet reste où il est */ }
        popup.close();
        openDecisions();
      };
    } catch { /* un navigateur qui refuse de construire la notification : pas d'exception pour la coque */ }
  }

  /** Les questions présentes dans la lecture d'attention. */
  const questionsOf = (data) => (data.sections || [])
    .flatMap((section) => section.items || [])
    .filter((item) => item.kind === 'question' && item.ref && item.ref.question_id)
    .map((item) => ({ ...item.ref, title: item.title, reason: item.reason, question_id: String(item.ref.question_id) }));

  function reconcile(data) {
    const questions = questionsOf(data);
    if (!on) return;
    if (!baselined) {
      // À l'activation : ce qui attend déjà n'est pas « nouveau ». Au rechargement avec
      // l'interrupteur déjà actif, la mémoire (`seen`) dit ce qui a déjà été signalé.
      baselined = true;
      for (const q of questions) seen.add(q.question_id);
      saveSeen(seen);
      return;
    }
    let changed = false;
    for (const q of questions) {
      if (seen.has(q.question_id)) continue;
      seen.add(q.question_id);
      changed = true;
      if (Notification.permission === 'granted') fire(q);
    }
    if (changed) saveSeen(seen);
  }

  async function turnOn() {
    if (!supported()) { say('Ce navigateur ne sait pas afficher de notifications.'); return false; }
    let permission = Notification.permission;
    if (permission === 'default') {
      try { permission = await Notification.requestPermission(); } catch { permission = 'denied'; }
    }
    if (permission !== 'granted') {
      on = false;
      paintButton();
      say('Notifications refusées par le navigateur : autorisez-les pour ce site.');
      return false;
    }
    on = true;
    baselined = false;
    write(STORE_ON, 'on');
    live.keepWhileHidden(true);
    paintButton();
    say('Notifications activées : vous serez prévenu à chaque nouvelle décision attendue.');
    // Une lecture neuve fixe la référence : ce qui attend déjà n'est pas une nouvelle décision.
    if (refresh) refresh();
    return true;
  }

  function turnOff() {
    on = false;
    write(STORE_ON, 'off');
    live.keepWhileHidden(false);
    paintButton();
    say('Notifications coupées.');
  }

  if (button) {
    button.addEventListener('click', () => { if (on) turnOff(); else turnOn(); });
  }
  if (!supported()) {
    if (button) { button.disabled = true; button.title = 'Ce navigateur ne sait pas afficher de notifications.'; }
  } else if (read(STORE_ON) === 'on' && Notification.permission === 'granted') {
    // Rechargement : l'interrupteur se souvient, mais seulement si la permission tient encore.
    on = true;
    baselined = true;
    live.keepWhileHidden(true);
  }
  paintButton();

  return {
    /** Appelé par la coque à chaque lecture du compteur, avec la réponse d'attention entière. */
    update(total, data) {
      count = Math.max(0, Number(total) || 0);
      paintTab();
      if (data) reconcile(data);
    },
    get active() { return on; },
  };
}
