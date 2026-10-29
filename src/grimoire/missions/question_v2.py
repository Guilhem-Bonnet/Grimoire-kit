"""Lire une question v2 telle qu'un agent la saisit : drapeaux de ``task ask`` ou fichier ``--spec`` (P92, §9.4).

Rien ne s'écrit ici : ce sont des fonctions pures qui rendent les arguments de ``TaskService.ask``.
La validation de fond (doublons, recommandation hors des choix, type de pièce, URL) reste à
``waiting.question_problem``, appelée à l'écriture.
"""

from __future__ import annotations

from typing import Any

from grimoire.missions.waiting import Attachment, Choice, Recommendation

__all__ = ["ask_kwargs", "parse_attach", "parse_choice"]


def parse_choice(flag: str) -> Choice:
    """``VALUE=LABEL[|CONSEQUENCE]`` ; sans ``=``, la valeur sert de libellé. ``!VALUE…`` marque un choix dangereux."""
    value, _, rest = flag.partition("=")
    danger = value.startswith("!")
    value = value.lstrip("!").strip()
    label, _, consequence = (rest or value).partition("|")
    return Choice(value, label.strip() or value, consequence.strip(), danger)


def parse_attach(flag: str) -> Attachment:
    """``TYPE:REF[:LABEL]`` — le libellé suit le dernier ``:`` sauf si ce n'est qu'un port ou un chemin d'URL."""
    kind, sep, rest = flag.partition(":")
    if not sep or not rest.strip():
        raise ValueError(f"pièce illisible : {flag!r} — attendu TYPE:REF[:LABEL]")
    ref, label = rest.strip(), ""
    idx = rest.rfind(":")
    tail = rest[idx + 1 :]
    if (
        idx > 0
        and not rest[:idx].lower().endswith(("http", "https"))
        and tail
        and not tail.isdigit()
        and "/" not in tail
    ):
        ref, label = rest[:idx].strip(), tail.strip()
    kind = kind.strip()
    if kind == "command":
        return Attachment(kind, label or ref, ref=ref, excerpt=ref, mutates=False)
    return Attachment(kind, label or ref, ref=ref)


def _recommendation(raw: Any) -> Recommendation | None:
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError("`recommendation` : un objet {value, reason, consequence}")
    return Recommendation.from_dict(raw)


def ask_kwargs(
    *,
    title: str = "",
    context: str = "",
    choices: list[str] | None = None,
    attach: list[str] | None = None,
    recommend: str = "",
    because: str = "",
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Les arguments v2 de ``TaskService.ask`` : le fichier ``--spec`` d'abord, les drapeaux par-dessus."""
    raw = spec or {}
    found: list[Choice] = [
        Choice.from_dict(c) if isinstance(c, dict) else Choice(str(c), str(c)) for c in raw.get("choices") or ()
    ]
    found += [parse_choice(c) for c in choices or ()]
    pieces = [Attachment.from_dict(a) for a in raw.get("attachments") or () if isinstance(a, dict)]
    pieces += [parse_attach(a) for a in attach or ()]
    recommendation = _recommendation(raw.get("recommendation"))
    if recommend:
        recommendation = Recommendation(recommend.strip(), because.strip())
    elif because and recommendation is not None:
        recommendation = Recommendation(recommendation.value, because.strip(), recommendation.consequence)
    elif because:
        raise ValueError("`--because` accompagne `--recommend`")
    return {
        "title": title or str(raw.get("title", "") or ""),
        "context": context or str(raw.get("context", "") or ""),
        "choices": tuple(found),
        "attachments": tuple(pieces),
        "answer_mode": str(raw.get("answer_mode", "") or ""),
        "recommendation": recommendation,
    }
