---
audience: contributeur
---

# Banc de mesure vocal

`scripts/voice_bench.py` mesure reconnaissance (stt) et synthèse (tts) sur votre machine, pour choisir les moteurs par défaut avec des chiffres réels (épique #1343). Hors du paquet : aucune dépendance n'est ajoutée.

1. Installez à part les moteurs à comparer (`faster-whisper`, `kokoro`, `pocket-tts`, `qwen-tts`) ; un moteur absent est listé « non installé ».
2. Reconnaissance : mettez des enregistrements français et leur transcription dans un dossier (`a.wav` + `a.txt`), puis `python scripts/voice_bench.py stt <dossier> --json stt.json`. Rend le taux d'erreur de mots et le facteur temps réel (RTF, < 1 = plus vite que la parole) de base, small et large-v3-turbo.
3. Synthèse : `python scripts/voice_bench.py tts --out sortie/ --json tts.json`. Huit phrases (nombres, liaisons, noms propres, technique) par moteur ; mesure délai avant le premier son, RTF et pic de mémoire.
4. Écoute à l'aveugle : les WAV de `sortie/` ont un nom anonyme et un ordre mélangé ; n'ouvrez `_correspondance.json` (fichier → moteur) qu'après avoir noté.
5. Rien n'est téléchargé sans `--download` : un modèle absent du cache est signalé, pas récupéré.
6. Le pic de mémoire est le jalon haut du processus : lancez un moteur à la fois (`--engine kokoro`) pour un chiffre propre.
7. Les adaptateurs Pocket TTS et Qwen3-TTS suivent leur API publique sans avoir été exécutés ici : corrigez-les au premier essai réel.

## Qwen3-TTS 0.6B en clonage et ses versions allégées

Moteurs : `qwen3-tts-0.6b-clone` (poids d'origine), `-bf16` (chargement torch en bfloat16), `-int8` (quantification dynamique torch des couches linéaires), `-gguf` et `-onnx` (ports communautaires). Même voix de référence pour tous :

```
python scripts/voice_bench.py tts --out sortie/ --json tts.json --ref-audio voix.wav --ref-text "Transcription de voix.wav." \
  --engine qwen3-tts-0.6b-clone --engine qwen3-tts-0.6b-clone-bf16 --engine qwen3-tts-0.6b-clone-int8
```

Un moteur à la fois pour un pic de mémoire propre. Sans `--ref-audio`, le moteur est listé avec le statut « voix de référence absente », sans erreur. Les ports GGUF et ONNX n'ont pas d'adaptateur : aucun paquet pip installable sous Linux, macOS et Windows n'a pu être vérifié (`qwen3-tts-gguf`, `-cpp`, `-onnx` n'existent pas sur PyPI) ; ils restent « non installé ».

### Mesures

Aucun modèle n'est téléchargé en CI ni dans l'environnement de cette PR : seule la ligne non quantifiée provient de la mesure du 2026-11-24 (processeur, décision #1343). Les autres cases sont à remplir par `--json` sur la machine de chaque palier, puis par l'écoute à l'aveugle (une version allégée ne doit pas perdre en qualité).

| Moteur | 1er son | RTF (processeur) | Pic de mémoire | Qualité à l'écoute |
|---|---|---|---|---|
| `qwen3-tts-0.6b-clone` | 33 s | 6 | 5,9 Go | référence |
| `-bf16` | à mesurer | à mesurer | à mesurer | à écouter |
| `-int8` | à mesurer | à mesurer | à mesurer | à écouter |
| `-gguf`, `-onnx` | non installé | non installé | non installé | non installé |

### Défaut proposé par palier (provisoire, à confirmer par les mesures ci-dessus)

- **Processeur modeste** : pas de clonage Qwen3 (RTF 6 sur processeur). Garder un moteur léger (`pocket-tts-fr`, `kokoro`).
- **Processeur récent ou Apple Silicon** : `-bf16` d'abord (Apple Silicon l'exécute nativement), `-int8` sur x86 ; ne devient défaut que si RTF < 1 et écoute équivalente, sinon moteur léger.
- **GPU** : `qwen3-tts-0.6b-clone` en bf16 ; la mémoire et le RTF CPU ne s'y appliquent pas.
