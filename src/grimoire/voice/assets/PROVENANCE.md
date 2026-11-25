# Voix de référence livrée (moteur `qwen3-tts-clone`)

- `ref-voix-fr.wav` : 4,6 s, mono, 24 kHz. Voix **générée** par synthèse vocale pour le banc
  d'écoute de l'épique #1343 (manche 2, 2026-11-24) : **aucune personne réelle** n'a enregistré
  ni prêté cette voix. Le générateur précis n'a pas été consigné au moment du banc.
- `ref-voix-fr.txt` : la transcription exacte du WAV, que le clonage exige.
- Modèle qui la lit : `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, licence **Apache-2.0** (le modèle n'est pas
  livré : il vient du cache Hugging Face de l'utilisateur, ou d'un téléchargement qu'il a accepté).

Une autre voix se règle par `voice.tts: {engine: qwen3-tts-clone, voice: /chemin/voix.wav, ref_text: "..."}` :
un fichier local, jamais une URL.
