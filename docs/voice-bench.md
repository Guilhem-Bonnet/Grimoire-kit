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
