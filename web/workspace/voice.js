// Le message vocal entrant du chat (lot V2) : un bouton « Dicter » qui enregistre, envoie l'audio au
// serveur local, et dépose le texte reconnu dans la saisie — corrigeable avant l'envoi.
//
// Garde-fous (rien ici ne passe par un LLM, rien n'approuve une action) :
// - le micro ne s'ouvre que pendant l'enregistrement : on le coupe (pistes arrêtées, contexte fermé)
//   à l'arrêt, au plafond de durée et au moindre échec ;
// - echoCancellation et noiseSuppression sont demandés, en mono, à 16 kHz ;
// - la durée est plafonnée par le réglage du projet (`voice.max_record_seconds`) ;
// - sans moteur local (extra `voice` absent, mode texte), le bouton n'existe pas.

export const SAMPLE_RATE = 16000;

/** Ramène des échantillons flottants de `from` Hz à `to` Hz (interpolation linéaire). */
export function resample(samples, from, to = SAMPLE_RATE) {
  if (from === to || samples.length === 0) return samples;
  const length = Math.max(1, Math.floor((samples.length * to) / from));
  const out = new Float32Array(length);
  const step = from / to;
  for (let i = 0; i < length; i++) {
    const at = i * step;
    const low = Math.floor(at);
    const high = Math.min(low + 1, samples.length - 1);
    out[i] = samples[low] + (samples[high] - samples[low]) * (at - low);
  }
  return out;
}

/** Un WAV PCM 16 bits mono à partir d'échantillons flottants [-1, 1]. */
export function encodeWav(samples, rate = SAMPLE_RATE) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return new Uint8Array(view.buffer);
}

/** Base64 d'octets, par tranches (un seul `String.fromCharCode` sur des Mo dépasse la pile). */
export function toBase64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Les mots d'un texte, en minuscules, lettres et chiffres seulement. */
function words(text) {
  return String(text || '').toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
}

/**
 * Le texte qui part contient-il encore la dictée ? Faux quand il ne reste aucun mot dicté : le contenu
 * a été entièrement réécrit au clavier (ou effacé). Une correction partielle garde la mention.
 */
export function stillDictated(text, dictatedParts) {
  const kept = new Set(words(text));
  if (kept.size === 0) return false;
  return dictatedParts.some((part) => words(part).some((word) => kept.has(word)));
}

export const MIC_CONSTRAINTS = {
  audio: { channelCount: 1, sampleRate: SAMPLE_RATE, echoCancellation: true, noiseSuppression: true },
};

/**
 * Monte la saisie vocale. `input` est la zone de texte du chat ; `slot` reçoit le bouton (s'il y a lieu).
 * Retourne `dictationPending(text)` : vrai si le texte qui part contient encore une dictée (à signaler
 * à l'orchestrateur), et `commitDictation()`, à appeler après un envoi RÉUSSI seulement : un envoi
 * refusé rend le texte à la saisie avec sa mention. La dictée tombe si le texte est entièrement réécrit.
 * Rien de ceci n'approuve quoi que ce soit : une permission se confirme par un clic (lot V5).
 */
export function createVoiceInput({ api, input, slot, note, setStatus, readOnly = () => false }) {
  let config = null;
  let recording = null;
  let dictated = [];
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn chat-mic';
  button.textContent = 'Dicter';
  button.setAttribute('aria-pressed', 'false');
  button.hidden = true;
  slot.append(button);

  function release(session) {
    // Le micro se referme ici, quoi qu'il arrive.
    try { session.node.disconnect(); } catch { /* déjà détaché */ }
    try { session.source.disconnect(); } catch { /* déjà détaché */ }
    for (const track of session.stream.getTracks()) track.stop();
    session.context.close().catch(() => {});
    clearTimeout(session.timer);
    clearInterval(session.ticker);
  }

  async function start() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
      note('Ce navigateur ne donne pas accès au micro.', 'error');
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
    } catch (error) {
      note(`Micro refusé ou absent : ${error.message}`, 'error');
      return;
    }
    const Context = window.AudioContext || window.webkitAudioContext;
    const context = new Context({ sampleRate: SAMPLE_RATE });
    const source = context.createMediaStreamSource(stream);
    const node = context.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    node.onaudioprocess = (event) => chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    source.connect(node);
    node.connect(context.destination);
    const startedAt = Date.now();
    const session = { stream, context, source, node, chunks, rate: context.sampleRate };
    session.timer = setTimeout(() => stop(), config.max_seconds * 1000);
    session.ticker = setInterval(() => {
      const left = Math.max(0, Math.ceil(config.max_seconds - (Date.now() - startedAt) / 1000));
      button.textContent = `Arrêter (${left} s)`;
    }, 250);
    recording = session;
    button.textContent = `Arrêter (${Math.ceil(config.max_seconds)} s)`;
    button.setAttribute('aria-pressed', 'true');
    setStatus('Enregistrement en cours — le micro est ouvert.');
  }

  async function stop() {
    const session = recording;
    if (!session) return;
    recording = null;
    release(session);
    button.setAttribute('aria-pressed', 'false');
    button.textContent = 'Dicter';
    const total = session.chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    if (total === 0) { setStatus('Micro fermé : rien enregistré.'); return; }
    const merged = new Float32Array(total);
    let offset = 0;
    for (const chunk of session.chunks) { merged.set(chunk, offset); offset += chunk.length; }
    // Jamais plus que le plafond, même si le minuteur a pris du retard.
    const limit = Math.floor(config.max_seconds * session.rate);
    const samples = resample(merged.subarray(0, limit), session.rate);
    button.disabled = true;
    setStatus('Transcription locale…');
    try {
      const result = await api.voiceTranscribe(toBase64(encodeWav(samples)));
      const text = (result.text || '').trim();
      if (!text) { setStatus('Rien de reconnu : réessayez, ou tapez le message.'); return; }
      input.value = input.value.trim() ? `${input.value.trim()} ${text}` : text;
      dictated.push(text);
      setStatus('Dictée transcrite : relisez et corrigez avant d’envoyer.');
      input.focus({ preventScroll: true });
    } catch (error) {
      setStatus('');
      note(`Transcription impossible : ${error.message}`, 'error');
    } finally {
      button.disabled = readOnly();
    }
  }

  button.addEventListener('click', () => { (recording ? stop() : start()); });
  input.addEventListener('input', () => {
    if (dictated.length && !stillDictated(input.value, dictated)) dictated = [];
  });

  return {
    /** À l'ouverture de l'onglet : demande au serveur si le vocal est disponible, sinon le bouton reste absent. */
    async refresh() {
      try {
        config = await api.voiceConfig();
      } catch {
        config = null;
      }
      const on = Boolean(config && config.available) && !readOnly();
      button.hidden = !on;
      if (!on) button.remove(); else if (!button.isConnected) slot.append(button);
    },
    dictationPending(text = input.value) {
      return dictated.length > 0 && stillDictated(text, dictated);
    },
    commitDictation() {
      dictated = [];
    },
    /** Coupe le micro si l'onglet se ferme en plein enregistrement. */
    abort() {
      if (recording) { const session = recording; recording = null; release(session); button.textContent = 'Dicter'; }
    },
  };
}
