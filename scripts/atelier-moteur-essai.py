#!/usr/bin/env python3
"""Essai MANUEL des gestes du moteur de conversation contre l'Ollama LOCAL (épique #1477, lot B2).

Jamais lancé par les tests ni par la CI. Il parle uniquement à une adresse de la machine (refus sinon), n'écrit rien,
ne lit ni clé ni projet, et montre pour chaque geste ce que le modèle a rendu après validation du schéma.

    python scripts/atelier-moteur-essai.py --modele qwen2.5:7b [--url http://127.0.0.1:11434] [--delai 20]
"""

from __future__ import annotations

import argparse
import sys
import time

from grimoire.core.cadrage_conversation_engine import (
    RUBRIQUE_KEYS,
    EntreeClassement,
    EntreeExtraction,
    EntreePistes,
    EntreeQuestion,
    EntreeReformulation,
)
from grimoire.tools.atelier_engines.moteur import MoteurModeles
from grimoire.tools.atelier_engines.ollama import OllamaBackend
from grimoire.tools.atelier_engines.transport import EngineError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--modele", required=True, help="nom du modèle Ollama (ex. qwen2.5:7b)")
    parser.add_argument("--url", default="http://127.0.0.1:11434", help="adresse d'Ollama (boucle locale seulement)")
    parser.add_argument("--delai", type=float, default=20.0, help="délai par geste, en secondes")
    args = parser.parse_args()
    try:
        backend = OllamaBackend(args.url, args.modele, timeout_s=args.delai)
    except EngineError as exc:
        print(f"refusé : {exc}", file=sys.stderr)
        return 2
    moteur = MoteurModeles("essai", args.modele, {"conversation": backend, "brainstorm": backend})
    gestes = {
        "formuler_question": lambda: moteur.formuler_question(
            EntreeQuestion("amorce", "utilisateurs", 0, "qui va s'en servir", "pour savoir à qui on parle")),
        "classer_reponse": lambda: moteur.classer_reponse(
            EntreeClassement("Je ne sais pas trop, plutôt des mainteneurs je crois", "amorce", "cadrage", "Qui va s'en servir ?")),
        "extraire_elements": lambda: moteur.extraire_elements(
            EntreeExtraction("Les mainteneurs, surtout le week-end. Pas les bots.", RUBRIQUE_KEYS[0])),
        "reformuler": lambda: moteur.reformuler(EntreeReformulation(("Les mainteneurs, le week-end",))),
        "proposer_pistes": lambda: moteur.proposer_pistes(EntreePistes("un outil de relecture", ("export CSV",))),
        "repondre_question": lambda: moteur.repondre_question("À quoi sert ce cadrage ?", ""),
    }
    echecs = 0
    for nom, geste in gestes.items():
        debut = time.monotonic()
        try:
            rendu = geste()
            print(f"[ok  {time.monotonic() - debut:5.1f}s] {nom} -> {rendu!r}")
        except EngineError as exc:
            echecs += 1
            print(f"[ECHEC {time.monotonic() - debut:5.1f}s] {nom} -> {type(exc).__name__} : {exc}")
    return 1 if echecs else 0


if __name__ == "__main__":
    raise SystemExit(main())
