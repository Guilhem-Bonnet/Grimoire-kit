"""``grimoire digest`` — résumer un gros texte par le fournisseur le moins cher.

L'agent interactif dépense son quota à lire des journaux de CI, des sorties de
tick ou des transcripts. Un modèle local bon marché peut les résumer d'abord :
cette commande prend un texte (fichier ou ``-``), le découpe en morceaux d'environ
:data:`CHUNK_CHARS` caractères, demande un résumé par morceau puis la synthèse
de ces résumés.

Deux garanties de fond, indépendantes du modèle :

- les lignes qui ressemblent à une erreur (``FAILED``, ``Error``, ``Traceback``,
  ``AssertionError``, ``exit code``) sont extraites **déterministement** du texte
  source et rendues telles quelles, avec leur numéro de ligne, sous le résumé :
  le modèle ne peut ni les inventer ni les perdre ;
- sans fournisseur admis, ou si l'appel échoue, la commande rend un repli
  déterministe (début, fin, lignes d'erreur) et le dit — elle sort en 0.

La sélection suit le registre (``routing.choice``) : capacité ``chat``, palier
``cheap``, ``currency: local`` préféré, et la politique de données de #1133 —
sans classe de données déclarée, aucun fournisseur externe. Un fournisseur
``provider_type: local`` reste admissible, rien n'y sortant de la machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.missions.dispatch import DEFAULT_CALL_TIMEOUT_S, _run_provider_call
from grimoire.providers.data_policy import split_by_data_policy
from grimoire.providers.routing import candidates as provider_candidates
from grimoire.providers.state import available_models, load_state

if TYPE_CHECKING:
    from grimoire.providers.registry import ProviderSpec

__all__ = [
    "CHUNK_CHARS",
    "DEFAULT_MAX_WORDS",
    "Digest",
    "DigestProviderError",
    "ErrorLine",
    "digest_text",
    "extract_error_lines",
    "split_chunks",
]

#: Taille d'un morceau soumis au modèle : assez pour garder le contexte d'une
#: pile d'appels, assez petit pour un modèle local lent.
CHUNK_CHARS = 6000

#: Borne par défaut d'un résumé, en mots.
DEFAULT_MAX_WORDS = 200

#: Lignes de début et de fin gardées dans le repli déterministe.
_HEAD_LINES = 20
_TAIL_LINES = 20

#: Ce qui fait qu'une ligne « ressemble à une erreur ». Volontairement large et
#: insensible à la casse : mieux vaut citer une ligne de trop que perdre la
#: seule ligne utile du journal.
_ERROR_LINE = re.compile(r"FAILED|Error|Traceback|AssertionError|exit code", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ErrorLine:
    """Une ligne d'erreur du texte source, à sa position d'origine."""

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class Digest:
    """Le résultat rendu à l'utilisateur : résumé borné, puis erreurs exactes."""

    summary: str
    error_lines: tuple[ErrorLine, ...] = ()
    provider: str | None = None
    model: str | None = None
    fallback_reason: str | None = None

    @property
    def used_fallback(self) -> bool:
        return self.fallback_reason is not None

    def render(self) -> str:
        """Le texte final : le résumé, puis les lignes d'erreur avec leur numéro."""
        parts = [self.summary.rstrip("\n")]
        if self.error_lines:
            parts.extend(("", "Lignes d'erreur :"))
            parts.extend(f"  {line.number}: {line.text}" for line in self.error_lines)
        return "\n".join(parts) + "\n"


class DigestProviderError(Exception):
    """L'appel au fournisseur n'a pas rendu de résumé exploitable."""


@dataclass(frozen=True, slots=True)
class _Choice:
    provider: ProviderSpec
    model: str


def extract_error_lines(text: str) -> tuple[ErrorLine, ...]:
    """Toutes les lignes du texte qui ressemblent à une erreur, dans l'ordre.

    Aucune déduplication, aucun filtre par le modèle : deux occurrences de la
    même erreur restent deux lignes, à leurs deux numéros.
    """
    return tuple(
        ErrorLine(number, line.rstrip("\r\n"))
        for number, line in enumerate(text.splitlines(), 1)
        if _ERROR_LINE.search(line)
    )


def split_chunks(text: str, size: int = CHUNK_CHARS) -> tuple[str, ...]:
    """Découpe *text* en morceaux d'environ *size* caractères, aux fins de ligne.

    Une fin de ligne trouvée après la moitié du morceau est préférée à une
    coupure au milieu d'une ligne : un message d'erreur cité par le modèle
    reste lisible. Un texte déjà court rend un seul morceau, sans copie.
    """
    if len(text) <= size:
        return (text,)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start + size // 2:
                end = newline + 1
        chunks.append(text[start:end])
        start = end
    return tuple(chunks)


def _bound_words(text: str, max_words: int) -> str:
    """Borne *text* à *max_words* mots, sans jamais couper un mot."""
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]) + " …"


def _goal(goal: str) -> str:
    return goal.strip() or "savoir ce qui s'est passé"


def _chunk_prompt(chunk: str, goal: str, max_words: int) -> str:
    return (
        "Tu résumes un extrait de texte technique pour un agent qui ne veut "
        "lire que l'essentiel.\n"
        f"Objectif de lecture : {_goal(goal)}\n"
        f"Résume en {max_words} mots au plus, en français, factuel. "
        "N'invente rien, ne recopie pas ce prompt.\n"
        "---\n"
        f"{chunk}\n"
        "---\n"
        "Résumé :"
    )


def _synthesis_prompt(summaries: list[str], goal: str, max_words: int) -> str:
    joined = "\n\n".join(f"[{i}]\n{summary}" for i, summary in enumerate(summaries, 1))
    return (
        "Voici les résumés successifs d'un même texte technique, dans l'ordre.\n"
        f"Objectif de lecture : {_goal(goal)}\n"
        f"Fusionne-les en un résumé de {max_words} mots au plus, en français, "
        "factuel. N'invente rien.\n"
        "---\n"
        f"{joined}\n"
        "---\n"
        "Résumé :"
    )


def _choose(root: Path, provider_id: str | None, now: datetime) -> tuple[_Choice | None, str]:
    """Le fournisseur ``chat``/``cheap`` à appeler, ou ``(None, motif du repli)``."""
    state = load_state(root)
    eligible = tuple(
        provider
        for provider in provider_candidates(root, "cheap", now=now)
        if "chat" in provider.allowed_capabilities
        and provider.invocation
        and available_models(provider, "cheap", state, now=now)
    )
    if provider_id:
        eligible = tuple(provider for provider in eligible if provider.id == provider_id)
        if not eligible:
            return None, f"fournisseur {provider_id!r} indisponible pour la capacité chat au palier cheap"
    admitted, refused = split_by_data_policy(root, eligible, "")
    if not admitted:
        if refused:
            return None, "politique de données : " + "; ".join(refused.values())
        return None, "aucun fournisseur chat/cheap disponible"
    # `currency: local` d'abord ; l'ordre du registre départage les ex æquo.
    provider = sorted(admitted, key=lambda item: item.currency != "local")[0]
    return _Choice(provider, available_models(provider, "cheap", state, now=now)[0].id), ""


def _call(root: Path, choice: _Choice, prompt: str) -> str:
    """Un appel fournisseur ; lève :class:`DigestProviderError` s'il n'y a pas de réponse."""
    code, stdout, stderr, _duration, failure_kind, _signal = _run_provider_call(
        root, choice.provider, choice.model, prompt, call_timeout=DEFAULT_CALL_TIMEOUT_S
    )
    if failure_kind is not None or code not in (None, 0):
        detail = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
        motif = failure_kind or f"code {code}"
        reason = f"appel {choice.provider.id}/{choice.model} échoué ({motif})"
        raise DigestProviderError(f"{reason} : {detail[:200]}" if detail else reason)
    answer = stdout.strip()
    if not answer:
        raise DigestProviderError(f"appel {choice.provider.id}/{choice.model} : sortie vide")
    return answer


def _summarize(root: Path, choice: _Choice, text: str, goal: str, max_words: int) -> str:
    """Un résumé par morceau, puis la synthèse de ces résumés s'il y en a plusieurs."""
    chunks = split_chunks(text)
    summaries = [_call(root, choice, _chunk_prompt(chunk, goal, max_words)) for chunk in chunks]
    if len(summaries) > 1:
        return _call(root, choice, _synthesis_prompt(summaries, goal, max_words))
    return summaries[0]


def _fallback_summary(reason: str, text: str, max_words: int) -> str:
    """Le repli déterministe : le motif, puis le début et la fin du texte."""
    lines = text.splitlines()
    parts = [f"résumé non généré : {reason}", ""]
    if len(lines) <= _HEAD_LINES + _TAIL_LINES:
        parts.extend(("Texte :", _bound_words(text, max_words) or "(vide)"))
    else:
        head = "\n".join(lines[:_HEAD_LINES])
        tail = "\n".join(lines[-_TAIL_LINES:])
        parts.extend(
            (
                "Début :",
                _bound_words(head, max_words) or "(vide)",
                "",
                "Fin :",
                _bound_words(tail, max_words) or "(vide)",
            )
        )
    return "\n".join(parts)


def _fallback(
    reason: str,
    text: str,
    errors: tuple[ErrorLine, ...],
    max_words: int,
    choice: _Choice | None = None,
) -> Digest:
    return Digest(
        summary=_fallback_summary(reason, text, max_words),
        error_lines=errors,
        provider=choice.provider.id if choice is not None else None,
        model=choice.model if choice is not None else None,
        fallback_reason=reason,
    )


def digest_text(
    root: Path,
    text: str,
    *,
    goal: str = "",
    max_words: int = DEFAULT_MAX_WORDS,
    provider_id: str | None = None,
    now: datetime | None = None,
) -> Digest:
    """Résume *text* via le fournisseur le moins cher, erreurs citées mot pour mot.

    Le repli est toujours rendu (jamais une exception) quand aucun fournisseur
    n'est admis ou que l'appel échoue : un texte d'entrée trop gros pour être lu
    vaut mieux qu'un échec qui laisse l'appelant sans rien.
    """
    if max_words < 1:
        raise ValueError("max_words doit être au moins 1")
    errors = extract_error_lines(text)
    moment = now or datetime.now(UTC)
    if not text.strip():
        return _fallback("entrée vide", text, errors, max_words)
    choice, reason = _choose(root, provider_id, moment)
    if choice is None:
        return _fallback(reason, text, errors, max_words)
    try:
        summary = _summarize(root, choice, text, goal, max_words)
    except DigestProviderError as exc:
        return _fallback(str(exc), text, errors, max_words, choice)
    return Digest(
        summary=_bound_words(summary, max_words),
        error_lines=errors,
        provider=choice.provider.id,
        model=choice.model,
    )
