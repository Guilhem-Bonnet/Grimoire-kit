// Le message vocal sortant du chat (lot V3) : le résumé parlé de l'orchestrateur, avec son texte, sa durée
// et un bouton lecture. Tant qu'il n'a pas été écouté, une nouvelle réponse met le texte à jour sur place ;
// au premier « Lire » il est figé (le serveur ouvre un autre message pour la suite). La synthèse n'a lieu
// qu'à la lecture. La lecture est interruptible : le même bouton devient « Arrêter ».
//
// Lot V4 : une question de l'orchestrateur est un message distinct, marqué « Question », en tête de la boîte
// tant qu'elle n'a pas de réponse ; ses options sont des boutons (un clic répond), « Répondre » cible la
// saisie (dictée ou texte, corrigeable avant l'envoi). L'état des angles d'un entretien de cadrage est
// affiché à côté. Tout texte passe par textContent.

const ANGLE_STATE = { ouvert: 'ouvert', couvert: 'couvert', ecarte: 'écarté' };

/** Rang d'affichage : les questions sans réponse d'abord, puis l'ordre d'arrivée. */
export function displayOrder(messages) {
  const waiting = (m) => m.kind === 'question' && !m.answered;
  return [...messages].sort((a, b) => (waiting(b) - waiting(a)) || (a.order - b.order));
}

/** « 12 s » ou « 1 min 05 s ». */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, '0')} s`;
}

/**
 * Le fil du chat sans la balise parlée du tour : `<voix-JETON>…</voix-JETON>` (le message parlé a sa place
 * dans la boîte vocale). `token` vient du serveur (`turn_started.spoken_token`) : un contenu cité ne peut
 * pas le connaître, donc rien d'autre n'est masqué. Seule la QUEUE du texte est retirée — la balise finale,
 * ou en cours de flux une balise ouverte (ou son début) qui termine le texte ; une balise suivie d'autre
 * contenu reste affichée telle quelle.
 */
export function withoutSpokenTag(text, token) {
  if (typeof token !== 'string' || !/^[0-9a-f]{8}$/.test(token)) return text;
  const open = `<voix-${token}>`;
  const close = `</voix-${token}>`;
  const at = text.lastIndexOf(open);
  if (at >= 0) {
    const rest = text.slice(at + open.length);
    const end = rest.indexOf(close);
    if (end < 0) return text.slice(0, at); // en cours de flux : la balise ouverte est la queue
    return rest.slice(end + close.length).trim() === '' ? text.slice(0, at) : text;
  }
  for (let n = Math.min(open.length - 1, text.length); n > 0; n -= 1) {
    if (text.endsWith(open.slice(0, n))) return text.slice(0, text.length - n); // début de balise en queue
  }
  return text;
}

/** Un `Audio` jouant un WAV en base64. */
function playWav(base64) {
  const audio = new window.Audio(`data:audio/wav;base64,${base64}`);
  return { audio, done: new Promise((resolve) => { audio.onended = resolve; audio.onerror = resolve; audio.onpause = resolve; audio.play().catch(resolve); }), stop: () => audio.pause() };
}

/** Le navigateur lit le texte avec sa propre voix (moteur « navigateur »). */
function speakText(text, lang) {
  const synth = window.speechSynthesis;
  const utterance = new window.SpeechSynthesisUtterance(text);
  if (lang) utterance.lang = lang;
  return { done: new Promise((resolve) => { utterance.onend = resolve; utterance.onerror = resolve; synth.speak(utterance); }), stop: () => synth.cancel() };
}

export function createVoiceOutbox({ api, note, setStatus, onReply = () => {}, onOption = async () => {} }) {
  const element = document.createElement('section');
  element.className = 'chat-voice-out';
  element.setAttribute('aria-label', 'Messages parlés de l’orchestrateur');
  element.hidden = true;
  const interview = document.createElement('aside');
  interview.className = 'chat-voice-interview';
  interview.setAttribute('aria-label', 'Entretien de cadrage');
  interview.hidden = true;
  const rows = new Map();
  const messages = new Map();
  let counter = 0;
  let playing = null; // { id, stop }

  function arrange() {
    const ordered = displayOrder([...rows.entries()].map(([id, row]) => ({ id, kind: messages.get(id).kind, answered: messages.get(id).answered, order: row.order })));
    for (const item of ordered) element.append(rows.get(item.id).node);
  }

  function stopPlayback() {
    if (!playing) return;
    const current = playing;
    playing = null;
    current.stop();
    const row = rows.get(current.id);
    if (row) { row.button.textContent = 'Lire'; row.button.setAttribute('aria-pressed', 'false'); }
  }

  function paint(message) {
    let row = rows.get(message.id);
    if (!row) {
      const text = document.createElement('p');
      text.className = 'chat-voice-text';
      const meta = document.createElement('span');
      meta.className = 'chat-voice-meta';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn chat-voice-play';
      button.textContent = 'Lire';
      button.setAttribute('aria-pressed', 'false');
      const node = document.createElement('div');
      node.className = 'chat-voice-msg';
      const badge = document.createElement('strong');
      badge.className = 'chat-voice-badge';
      badge.textContent = 'Question';
      badge.hidden = true;
      const choices = document.createElement('div');
      choices.className = 'chat-voice-options';
      choices.hidden = true;
      const reply = document.createElement('button');
      reply.type = 'button';
      reply.className = 'btn pri chat-voice-reply';
      reply.textContent = 'Répondre';
      reply.hidden = true;
      node.append(badge, text, meta, button, reply, choices);
      element.append(node);
      counter += 1;
      row = { node, text, meta, button, badge, choices, reply, order: counter };
      rows.set(message.id, row);
      button.addEventListener('click', () => toggle(message.id));
      reply.addEventListener('click', () => onReply(messages.get(message.id)));
    }
    messages.set(message.id, message);
    const isQuestion = message.kind === 'question';
    const waiting = isQuestion && !message.answered;
    row.text.textContent = message.text;
    const state = isQuestion
      ? (message.answered ? ' · répondue' : ' · question en attente de votre réponse')
      : (message.played ? ' · écouté' : ' · brouillon, mis à jour tant qu’il n’est pas lu');
    row.meta.textContent = `${formatDuration(message.duration_s)}${isQuestion && message.played && waiting ? ' · écoutée' : ''}${state}`;
    row.node.dataset.played = String(Boolean(message.played));
    row.node.dataset.kind = isQuestion ? 'question' : 'report';
    row.node.dataset.answered = String(Boolean(message.answered));
    row.badge.hidden = !isQuestion;
    row.reply.hidden = !waiting;
    row.choices.replaceChildren();
    row.choices.hidden = !(waiting && message.ref && (message.options || []).length);
    if (!row.choices.hidden) {
      for (const option of message.options) {
        const choice = document.createElement('button');
        choice.type = 'button';
        choice.className = 'btn chat-voice-option';
        choice.textContent = option;
        choice.addEventListener('click', async () => {
          for (const b of row.choices.querySelectorAll('button')) b.disabled = true;
          try { await onOption(messages.get(message.id), option); } catch (error) {
            for (const b of row.choices.querySelectorAll('button')) b.disabled = false;
            note(`Réponse non transmise : ${error.message}`, 'error');
          }
        });
        row.choices.append(choice);
      }
    }
    arrange();
    element.hidden = false;
  }

  function paintInterview(state) {
    interview.replaceChildren();
    const open = (state.interviews || []).filter((entry) => !entry.error);
    interview.hidden = open.length === 0;
    for (const entry of open) {
      const title = document.createElement('div');
      title.className = 'chat-voice-interview-title';
      title.textContent = `Entretien de cadrage${entry.chantier && entry.chantier !== 'principal' ? ` — ${entry.chantier}` : ''}${entry.complete ? ' : terminé' : ''}`;
      const list = document.createElement('ul');
      list.className = 'chat-voice-angles';
      for (const angle of entry.angles || []) {
        const item = document.createElement('li');
        item.dataset.state = angle.state;
        item.textContent = `${angle.title} : ${ANGLE_STATE[angle.state] || angle.state}${angle.state === 'ecarte' && angle.reason ? ` (${angle.reason})` : ''}`;
        list.append(item);
      }
      interview.append(title, list);
    }
  }

  async function toggle(id) {
    if (playing && playing.id === id) { stopPlayback(); return; }
    stopPlayback();
    const row = rows.get(id);
    row.button.disabled = true;
    setStatus('Synthèse vocale…');
    try {
      const result = await api.voiceOutboxPlay(id);
      paint(result.message);
      const player = result.audio ? playWav(result.audio) : speakText(result.text);
      playing = { id, stop: player.stop };
      row.button.textContent = 'Arrêter';
      row.button.setAttribute('aria-pressed', 'true');
      setStatus('');
      player.done.then(() => { if (playing && playing.id === id) stopPlayback(); });
    } catch (error) {
      setStatus('');
      note(`Lecture impossible : ${error.message}`, 'error');
    } finally {
      row.button.disabled = false;
    }
  }

  return {
    element,
    /** L'état des angles de l'entretien de cadrage, à côté de la boîte (rien d'affiché sans entretien). */
    interview,
    async refreshInterview() {
      try { paintInterview(await api.voiceInterview()); } catch { interview.hidden = true; }
    },
    /** Un événement `voice_message` du flux : texte à jour, ou nouveau message. */
    receive(message) { paint(message); },
    /** À l'ouverture : l'état du serveur (la page a pu manquer des messages). */
    async refresh() {
      try {
        const state = await api.voiceOutbox();
        for (const message of state.messages || []) paint(message);
      } catch { /* sans vocal, rien à montrer */ }
      await this.refreshInterview();
    },
    abort: stopPlayback,
  };
}
