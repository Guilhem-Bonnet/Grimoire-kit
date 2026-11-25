---
audience: utilisateur
---

# Synthèse vocale par clonage (Qwen3-TTS)

Le moteur `qwen3-tts-clone` lit les messages avec Qwen3-TTS 0.6B Base, en clonant une voix de référence française livrée dans le paquet. Mesuré sur processeur : environ 6 s de calcul par seconde de parole et environ 6 Go de mémoire, ce qui convient à des messages asynchrones.

## Installation

```bash
pip install 'grimoire-kit[voice-qwen]'
```

L'extra est séparé de `voice` : `qwen-tts` épingle `transformers==4.57.3`. Si votre gestionnaire ne résout pas l'extra, `pip install qwen-tts` dans le même environnement fait la même chose. Installez torch CPU d'abord pour éviter les roues CUDA.

## Choix du moteur

Sans `voice.tts` explicite, le défaut est `qwen3-tts-clone` si le paquet `qwen_tts` est installé, sinon `pocket-tts`, sinon la voix du navigateur. `grimoire doctor` (mode voice) indique le moteur retenu, la mémoire attendue et un modèle absent du cache.

## Réglages

```yaml
voice:
  tts:
    engine: qwen3-tts-clone
    voice: /chemin/ma-voix.wav   # facultatif : WAV local + ma-voix.txt (sa transcription) à côté
    threads: 4                   # facultatif : défaut, la moitié des cœurs
  allow_download: false
```

- Une URL n'est jamais acceptée comme voix.
- Le modèle (`Qwen/Qwen3-TTS-12Hz-0.6B-Base`) est lu dans le cache Hugging Face ; il n'est téléchargé qu'avec `voice.allow_download: true`.
- La synthèse limite les threads de torch le temps du calcul, puis les rétablit.

La voix livrée est entièrement synthétique (voir `src/grimoire/data/voice/README.md`).

Ce que la voix peut et ne peut pas faire (aucune approbation), limites et données gardées : [Mode vocal](voice.md).
