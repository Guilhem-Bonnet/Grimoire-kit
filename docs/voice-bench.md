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
8. Clonage de voix (Qwen3-TTS 0.6B Base) : `python scripts/voice_bench.py tts --out sortie/ --engine qwen3-tts-0.6b-clone-prompt --ref-audio ma-voix.wav --ref-text "transcription exacte" --download`. Aucun audio de référence n'est dans le dépôt. Les variantes se choisissent par le nom du moteur : `qwen3-tts-0.6b-clone` (base), `-threads4|8|16`, `-inference`, `-prompt` (invite de clonage calculée une fois), `-xvector` (empreinte de locuteur seule), `-int8`, `-prompt-int8`, `-bf16`, `-prompt-bf16`, `-gpu` (CUDA ou ROCm, `cuda:0`). `--intelligibilite [taille]` retranscrit chaque WAV avec faster-whisper (défaut `small`) et ajoute la colonne « erreurs de mots » (mots faux / mots attendus) ; « charge s » est la durée de chargement, « charge machine » la charge moyenne à la fin (une machine occupée fausse tout). Sans `--download`, le clonage échoue hors ligne même modèle en cache (le processeur de qwen-tts interroge l'API Hugging Face) : passez `--download`.

## Mesures du clonage sur un poste type (épique #1343)

Poste : Intel Core i9-9900K (8 coeurs, 16 fils, AVX2 sans AVX-512 ni bf16), 62 Go de RAM, Radeon RX 9070 (ROCm 7.1 via la roue torch, venv à part), Fedora, torch 2.15 CPU, qwen-tts 0.1.1. Voix de référence française de 8 s, 8 phrases du banc (114 mots, environ 45 s de parole). Un moteur par processus, `nice -n 10`.

Limite essentielle : la machine n'était pas au repos (charge moyenne de 15 à 30 pendant tous les essais, d'autres traitements en parallèle). Les temps sont donc gonflés et bruités (le même réglage varie de 33 à 52 s d'un passage à l'autre) : seuls les écarts supérieurs à environ 30 % comptent. « Erreurs de mots » = faux mots de faster-whisper small sur les 114 attendus : un indicateur d'intelligibilité, qui bouge de 11 à 21 d'un tirage à l'autre pour un même réglage (échantillonnage aléatoire) ; il ne mesure ni la ressemblance de la voix ni la qualité, qui se jugent à l'écoute.

### Qwen3-TTS 0.6B Base avec torch (`pip install qwen-tts`)

`pip install qwen-tts` s'installe sur Linux, macOS et Windows (paquet Python pur, torch en dépendance ; la doc amont cible Python 3.12). Sans CUDA, qwen-tts avertit que flash-attn manque : sans effet sur le processeur.

| Variante | 1er son (s) | Temps / parole (RTF) | RAM pic | Erreurs de mots | Verdict |
|---|---|---|---|---|---|
| base (réglages par défaut) | 41 à 52 | 8 à 10 | 6,1 Go | 18 à 19 / 114 | référence |
| `-threads4` | 35 | 6,7 | 6,1 Go | 17 | plus rapide que tous les fils sous charge |
| `-threads8` | 63 | 11,2 | 6,1 Go | 16 | bruit de charge, pas de gain |
| `-threads16` | non terminé | non terminé | - | - | abandonné : 5 phrases en 75 min (charge 40) |
| `-inference` | 34 | 6,2 | 6,1 Go | 14 | dans le bruit |
| `-prompt` (invite réutilisée) | 37 | 6,9 | 6,0 Go | 13 | gain d'environ 10 %, sans perte |
| `-xvector` (empreinte seule) | 33 | 5,8 | 6,1 Go | 11 | un peu plus rapide ; voix moins fidèle à vérifier à l'écoute |
| `-int8` / `-prompt-int8` | aucune phrase en 25 min | - | - | - | inutilisable (quantification dynamique torch, AVX2) |
| `-bf16` | aucune phrase en 18 min | - | - | - | inutilisable : ce processeur n'a pas de bf16 matériel |
| `-gpu` (RX 9070, ROCm 7.1, bf16) | 12,2 à 12,4 | 2,2 à 2,3 | 3,8 Go (hors VRAM) | 16 à 21 | 3 à 4 fois plus rapide que le processeur, toujours plus lent que la parole |

Notes : `non_streaming_mode=False` ne diffuse rien (il simule seulement l'entrée texte en flux) : avec qwen-tts 0.1.1 le premier son est la fin du calcul. Sur le GPU, `MIOPEN_FIND_MODE=FAST` est indispensable (mode par défaut : 71 s, RTF 13, MIOpen cherche un noyau par forme de convolution). La carte ne s'installe qu'avec une roue torch ROCm de 3 à 4 Go dans un venv séparé, sans toucher au système ; le périphérique doit être lisible (`/dev/kfd`).

### Ports communautaires

Aucun ne s'installe par `pip` : tous demandent une compilation ou un binaire. Un seul a été essayé : le port en C pur `gabriele-mastrapasqua/qwen3-tts` (licence MIT, lit les poids safetensors d'origine, `make blas` sur Linux, macOS et WSL2 ; il manquait seulement `cblas.h`, fourni par un en-tête minimal local avec la bibliothèque OpenBLAS du système). Poids : 0,6B Base ; clonage avec `--ref-text` (sans lui il n'utilise que l'empreinte de locuteur) ; le profil de voix se sauvegarde une fois (`--save-voice`, 16 Mo) et se recharge (`--load-voice`).

| Variante du port C | 1er son (s) | RTF | RAM pic | Erreurs de mots |
|---|---|---|---|---|
| bf16, invite recalculée | 13,2 | 4,9 | 3,2 Go | 13 / 114 |
| `--int8`, invite recalculée | 13,1 | 4,0 | 3,8 Go | 15 |
| `--int4`, invite recalculée | 14,2 | 4,5 | 3,5 Go | 17 |
| `--int8 --xvector-only` | 4,4 | 4,0 | 3,6 Go | 15 |
| `--int8 --load-voice` (profil sauvegardé), 8 fils | 4,5 | 4,3 | 3,6 Go | 15 |
| `--int8 --load-voice`, 4 fils | 3,8 | 3,5 | 3,6 Go | 15 |

Le premier son est mesuré en flux (`--stdout`, première réponse), le reste sur le fichier complet. Sous une charge de 15 à 25, avec un passage isolé à RTF 2,6 quand la machine était plus calme. Le port C diffuse vraiment : 4 s avant le premier son contre 33 à 52 s en torch, pour une mémoire divisée par 1,7.

Non essayés : `predict-woo/qwen3-tts.cpp` (C++/GGML, MIT, compilation cmake absente du poste, Metal/CUDA surtout), `darkautism/qwen3-tts` (Rust, MIT, benchs sur ARM RK3588 uniquement), poids GGUF/ONNX de `wkwong/LunaVox` (Apache-2.0, clonage non documenté) et `wavekat/Qwen3-TTS-0.6B-Base-ONNX` (INT4 annoncé). Vulkan : aucun des ports essayés ne le propose ; non essayé.

## Défaut recommandé par palier

- Processeur modeste (AVX2, sans bf16, 8 coeurs ou moins) : ne pas mettre Qwen3-TTS en torch par défaut (6 Go, RTF de 6 à 10, 35 s avant le premier son). Défaut : Pocket TTS français sans clonage (1,2 Go, RTF 0,38, premier son environ 2,6 s sur ce poste, voix du catalogue, note d'écoute 1,8 ; le clonage Pocket exige des poids sous conditions d'accès, non essayé). Le clonage reste une option avancée via le port C `--int8` avec profil sauvegardé (4 s avant le premier son, RTF 3 à 4 : pas du temps réel, donc messages différés).
- Processeur récent ou Apple Silicon (AVX-512/VNNI/BF16, AMX, ou M-series) : non mesuré ici. D'après la doc amont du port C, un M1 atteint RTF 0,5 à 0,7 en int4/int8 avec 3 Go : le défaut proposé est le port C int8 avec profil de voix sauvegardé ; en torch, bf16 n'a de sens que sur ces matériels. À vérifier par le banc sur la machine cible.
- GPU : torch + `-gpu` (ROCm ou CUDA) donne un RTF d'environ 2 sur une RX 9070, mieux qu'aucune variante processeur ici mais pas du temps réel ; non mesuré sur CUDA NVIDIA (le port C et torch le supportent d'après leur doc).

Recommandation globale : `-prompt` (invite calculée une fois) sur torch est gratuit et sans perte audible mesurable ; le gain décisif vient du port C (premier son, mémoire), à confirmer par l'écoute des WAV conservés avant de le retenir comme défaut.

## Ce qui n'a pas pu être mesuré

- Machine au repos : tous les temps sont pollués par une charge de 15 à 30.
- Ressemblance et qualité de la voix : l'intelligibilité par transcription ne les mesure pas, l'écoute à l'aveugle est nécessaire.
- Palier récent / Apple Silicon / CUDA : aucun matériel disponible.
- Ports C++ GGML, Rust, ONNX : non essayés. Vulkan : non essayé.
- Mode flux de qwen-tts : n'existe pas en 0.1.1.
