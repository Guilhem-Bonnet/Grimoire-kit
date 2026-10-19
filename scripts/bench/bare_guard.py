"""Refus fail-closed de ``claude -p --bare`` pour un bras dont la mesure dépend des hooks du projet (B-06).

``--bare`` a été introduit au lot J (#582, ``--auth api-key``) pour ne copier aucun jeton OAuth. Mais
``claude --help`` (2.1.302) le dit sans ambiguïté : il saute les hooks, les skills et le ``CLAUDE.md`` du
projet. Un bras gouverné lancé ainsi n'est pas gouverné : le résultat serait faux, et sans aucun signal.
Seul ``nu`` (aucun hook, aucune skill, aucun fichier d'instructions) peut garder ``--bare``.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Les bras qui n'ont de sens qu'avec les hooks, skills ou instructions du projet : tous sauf ``nu``.
BARE_ALLOWED_ARMS: frozenset[str] = frozenset({"nu"})


def refuse_bare_for_governed_arms(arms: Iterable[str], auth_mode: str | None, *, bare: bool | None = None) -> None:
    """Lève ``SystemExit`` si ``--bare`` s'appliquerait à un bras gouverné.

    *bare* : ``None`` déduit de *auth_mode* (``api-key`` ajoute ``--bare``, voir ``run_claude_headless``) ;
    ``True``/``False`` forcent. Appelée avant tout appel payant (``main``) et avant chaque lancement.
    """
    if not ((auth_mode == "api-key") if bare is None else bare):
        return
    governed = sorted(set(arms) - BARE_ALLOWED_ARMS)
    if governed:
        raise SystemExit(
            f"refus : --auth api-key ajoute `claude -p --bare`, qui ignore les hooks, les skills et le CLAUDE.md du projet ; "
            f"le(s) bras {', '.join(governed)} ne serait pas gouverné et son résultat serait faux. "
            "Utilise --auth oauth-copy pour ce(s) bras, ou limite la campagne au bras nu (--arms nu)."
        )
