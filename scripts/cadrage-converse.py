#!/usr/bin/env python3
"""Essai du moteur de conversation de cadrage, dans le terminal, SANS modèle (épique #1477, lot A).

Joue une conversation avec le moteur sans modèle sur un chantier existant : une question à la fois, un tour par ligne
saisie. C'est le même programme que les routes de l'Atelier (``grimoire.core.cadrage_conversation_turn``) : rien n'est
écrit dans le document sans ta confirmation, et la conversation est enregistrée dans ``conversation.json`` du chantier.

Usage::

    python scripts/cadrage-converse.py --chantier mon-chantier [--root .]

Saisie : un texte (ta réponse) ; ``/pause`` ; ``/etat`` (résumé en deux lignes) ; ``/quitter``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # lancé depuis un clone : le paquet est sous src/
    sys.path.insert(0, str(ROOT / "src"))


def main(argv: Sequence[str] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    from grimoire.core import cadrage_conversation_hygiene as hygiene
    from grimoire.core import cadrage_conversation_turn as turn
    from grimoire.core.cadrage import PHASES
    from grimoire.core.cadrage_conversation import ConversationError
    from grimoire.core.cadrage_conversation_engine import Garde, MoteurSansModele
    from grimoire.tools import workspace_atelier as wa

    parser = argparse.ArgumentParser(description="Conversation de cadrage dans le terminal, sans modèle.")
    parser.add_argument("--chantier", required=True, help="le slug du chantier (``principal`` pour le dossier historique)")
    parser.add_argument("--root", default=".", help="la racine du projet (défaut : le dossier courant)")
    args = parser.parse_args(argv)
    src, out = stdin or sys.stdin, stdout or sys.stdout
    root = Path(args.root).resolve()

    def say(text: str) -> None:
        print(text, file=out)

    try:
        directory = wa._phase_path(root, wa._slug(args.chantier), PHASES[0].filename).parent
    except FileNotFoundError:
        say(f"Chantier inconnu : {args.chantier!r}")
        return 2
    ctx = turn.Contexte(root, args.chantier, directory, Garde(MoteurSansModele()), hygiene.maintenant(), hygiene.Reglages())
    try:
        started = turn.demarrer(ctx)
        say(started["message"] or started["resume"])
        rev = int(started["rev"])
        for line in src:
            text = line.strip()
            if text == "/quitter":
                break
            if not text:
                continue
            if text == "/etat":
                say(turn.vue(ctx)["resume"])
                continue
            reaction = turn.pause(ctx, rev) if text == "/pause" else turn.recevoir(ctx, rev, text)
            rev = int(reaction["rev"])
            say(f"{reaction['accuse']} {reaction['message']}".strip())
            if reaction["etape"]["kind"] in ("termine", "pause"):
                break
    except ConversationError as exc:
        say(f"Refusé : {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
