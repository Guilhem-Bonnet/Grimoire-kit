"""Résolution des règles par couche : qui s'applique, dans quel ordre, et qui cède.

Suite de :mod:`grimoire.core.layered_rules` (W6-03) : ce module répond à la
question « quelles règles valent pour ce fichier, et d'où viennent-elles ? »
par une fonction pure, au lieu de concaténer les couches et de laisser le
modèle trancher.

- :func:`scope_matches` : une portée (glob exact de la grammaire) couvre-t-elle
  un chemin ?
- :func:`scope_covers` : une portée en couvre-t-elle une autre ? Approximation
  **prudente** : ``True`` est une preuve, ``False`` veut dire « non prouvé ».
- :func:`resolve_rules` : la liste ordonnée des règles, avec le rang, la couche
  d'origine et, pour chacune, ce qui la fait céder.

Sans chemin, la résolution sert l'émission vers les hôtes (une règle est émise
pour toutes les portées qu'elle couvre) ; avec un chemin, elle sert
``grimoire memory rules explain <chemin>`` et son verdict est exact : deux
règles qui s'appliquent au même chemin se recouvrent forcément.

Une règle qui cède n'est comptée comme telle que devant une règle qui, elle, ne
cède pas : ``ALWAYS x`` (organisation), ``NEVER x`` (équipe) et ``ALWAYS x``
(projet) laissent l'équipe à l'ombre de l'organisation, et le projet reste
effectif puisque l'organisation dit la même chose que lui.
"""

from __future__ import annotations

import fnmatch
import functools
from collections.abc import Iterable
from dataclasses import dataclass

from grimoire.core.layered_rules import LAYERS, Rule, check_rule_conflicts

__all__ = [
    "EMITTED_LAYERS",
    "STATUS_EFFECTIVE",
    "STATUS_PARTIAL",
    "STATUS_SHADOWED",
    "STATUS_UNRESOLVED",
    "ResolvedRule",
    "normalize_path",
    "resolve_rules",
    "scope_covers",
    "scope_matches",
]

#: Couches que ``host sync`` émet vers les hôtes (décision 10 de la campagne :
#: ``team`` et ``project`` en vague 6 ; ``organization`` et ``agent`` existent
#: dans la grammaire mais ne sont pas livrés aux hôtes).
EMITTED_LAYERS: tuple[str, ...] = ("team", "project")

STATUS_EFFECTIVE = "effective"
#: Une règle d'une couche extérieure, effective, la contredit sur tout ce qu'elle couvre.
STATUS_SHADOWED = "shadowed"
#: Idem, mais seulement là où les portées se recouvrent : la règle vit encore ailleurs.
STATUS_PARTIAL = "partial"
#: Une règle de la même couche la contredit : rien ne tranche.
STATUS_UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ResolvedRule:
    """Une règle, son rang dans la résolution et ce qui la contredit."""

    rule: Rule
    #: 1 = la plus prioritaire (couche la plus extérieure, puis ordre du fichier).
    rank: int
    status: str = STATUS_EFFECTIVE
    #: Règles effectives d'une couche extérieure qui la contredisent (``shadowed``, ``partial``).
    defeated_by: tuple[Rule, ...] = ()
    #: Règles de la même couche qui la contredisent (``unresolved``).
    contested_by: tuple[Rule, ...] = ()

    @property
    def layer(self) -> str:
        return self.rule.layer

    @property
    def dead(self) -> bool:
        """Vraie quand la règle ne vaut nulle part où elle est écrite."""
        return self.status == STATUS_SHADOWED

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "layer": self.rule.layer,
            "line": self.rule.line,
            "rule": self.rule.render(),
            "scope": self.rule.scope,
            "status": self.status,
            "defeated_by": [_origin(r) for r in self.defeated_by],
            "contested_by": [_origin(r) for r in self.contested_by],
        }


def _origin(rule: Rule) -> dict[str, object]:
    return {"layer": rule.layer, "line": rule.line, "rule": rule.render()}


# ── Chemins et portées ────────────────────────────────────────────────────────


def normalize_path(raw: str) -> str:
    """Chemin relatif à la racine du projet, en séparateurs ``/``, ou ``ValueError``.

    Refusé : vide, absolu, ``..``. ``./a`` devient ``a``.
    """
    text = raw.replace("\\", "/")
    if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise ValueError(f"chemin {raw!r} : relatif à la racine du projet attendu.")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"chemin {raw!r} : vide ou remontant (..).")
    return "/".join(parts)


def scope_matches(scope: str, path: str) -> bool:
    """Le glob ``scope`` (``**`` = zéro ou plusieurs segments, ``*``/``?`` dans un segment) couvre-t-il ``path`` ?"""
    pattern, parts = scope.split("/"), path.split("/")
    n, m = len(pattern), len(parts)

    @functools.cache
    def reach(i: int, j: int) -> bool:
        if i == n:
            return j == m
        if pattern[i] == "**":
            return reach(i + 1, j) or (j < m and reach(i, j + 1))
        return j < m and fnmatch.fnmatchcase(parts[j], pattern[i]) and reach(i + 1, j + 1)

    return reach(0, 0)


def _segment_covers(outer: str, inner: str) -> bool:
    if outer == inner:
        return True
    if inner == "**":
        return False  # zéro segment ou plusieurs : un segment d'un motif n'en couvre pas autant
    if outer == "*":
        return True
    wild = "*?"
    return not any(c in inner for c in wild) and fnmatch.fnmatchcase(inner, outer)


def scope_covers(outer: str, inner: str) -> bool:
    """Tout chemin couvert par ``inner`` l'est-il par ``outer`` ? Prudent : ``False`` = non prouvé.

    Prouve deux formes : ``outer`` identique à ``inner`` (ou ``**``), et
    ``outer = <préfixe>/**`` dont chaque segment du préfixe couvre le segment
    correspondant de ``inner``. Tout le reste (``**/*.py`` couvrant ``src/a.py``,
    qui est vrai) renvoie ``False``.
    """
    if outer == "**" or outer == inner:
        return True
    outer_parts, inner_parts = outer.split("/"), inner.split("/")
    if outer_parts[-1] == "**":
        prefix = outer_parts[:-1]
        if "**" in prefix or len(inner_parts) < len(prefix):
            return False
        return all(_segment_covers(o, i) for o, i in zip(prefix, inner_parts, strict=False))
    if "**" in outer_parts or "**" in inner_parts or len(outer_parts) != len(inner_parts):
        return False
    return all(_segment_covers(o, i) for o, i in zip(outer_parts, inner_parts, strict=True))


# ── Résolution ────────────────────────────────────────────────────────────────


def _layer_index(rule: Rule) -> int:
    return LAYERS.index(rule.layer) if rule.layer in LAYERS else len(LAYERS)


def resolve_rules(rules: Iterable[Rule], path: str | None = None) -> tuple[ResolvedRule, ...]:
    """Ordonner les règles par précédence et dire laquelle cède devant quelle autre. Pure.

    Avec ``path`` (déjà normalisé, voir :func:`normalize_path`), seules les
    règles dont la portée couvre le chemin sont retournées. L'ordre est la
    précédence : couche extérieure d'abord, puis ordre du fichier.
    """
    pool = [r for r in rules if path is None or scope_matches(r.scope, path)]
    ordered = sorted(enumerate(pool), key=lambda pair: (_layer_index(pair[1]), pair[1].line, pair[0]))
    ranked = [rule for _, rule in ordered]
    opposed: dict[Rule, list[Rule]] = {}
    for conflict in check_rule_conflicts(ranked):
        for mine, other in ((conflict.first, conflict.second), (conflict.second, conflict.first)):
            opposed.setdefault(mine, []).append(other)

    resolved: list[ResolvedRule] = []
    effective: set[tuple[str, int, str]] = set()  # (couche, ligne, texte) des règles qui ne cèdent pas
    for rank, rule in enumerate(ranked, start=1):
        outer = [o for o in opposed.get(rule, ()) if _layer_index(o) < _layer_index(rule) and _ident(o) in effective]
        same = tuple(o for o in opposed.get(rule, ()) if o.layer == rule.layer)
        if outer:
            full = any(scope_covers(o.scope, rule.scope) for o in outer) if path is None else True
            status = STATUS_SHADOWED if full else STATUS_PARTIAL
            resolved.append(ResolvedRule(rule, rank, status, defeated_by=tuple(outer), contested_by=same))
            if status == STATUS_PARTIAL:
                effective.add(_ident(rule))
            continue
        if same:
            resolved.append(ResolvedRule(rule, rank, STATUS_UNRESOLVED, contested_by=same))
            continue
        resolved.append(ResolvedRule(rule, rank))
        effective.add(_ident(rule))
    return tuple(resolved)


def _ident(rule: Rule) -> tuple[str, int, str]:
    return (rule.layer, rule.line, rule.render())
