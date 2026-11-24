// Le message vocal sortant du chat (lot V3) : le résumé parlé de l'orchestrateur, avec son texte, sa durée
// et un bouton lecture. Tant qu'il n'a pas été écouté, une nouvelle réponse met le texte à jour sur place ;
// au premier « Lire » il est figé (le serveur ouvre un autre message pour la suite). La synthèse n'a lieu
// qu'à la lecture. La lecture est interruptible : le même bouton devient « Arrêter ».

/** « 12 s » ou « 1 min 05 s ». */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, '0')} s`;
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

export function createVoiceOutbox({ api, note, setStatus }) {
  const element = document.createElement('section');
  element.className = 'chat-voice-out';
  element.setAttribute('aria-label', 'Messages parlés de l’orchestrateur');
  element.hidden = true;
  const rows = new Map();
  let playing = null; // { id, stop }

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
      node.append(text, meta, button);
      element.append(node);
      row = { node, text, meta, button };
      rows.set(message.id, row);
      button.addEventListener('click', () => toggle(message.id));
    }
    row.text.textContent = message.text;
    row.meta.textContent = `${formatDuration(message.duration_s)}${message.played ? ' · écouté' : ' · brouillon, mis à jour tant qu’il n’est pas lu'}`;
    row.node.dataset.played = String(Boolean(message.played));
    element.hidden = false;
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
    /** Un événement `voice_message` du flux : texte à jour, ou nouveau message. */
    receive(message) { paint(message); },
    /** À l'ouverture : l'état du serveur (la page a pu manquer des messages). */
    async refresh() {
      try {
        const state = await api.voiceOutbox();
        for (const message of state.messages || []) paint(message);
      } catch { /* sans vocal, rien à montrer */ }
    },
    abort: stopPlayback,
  };
}
