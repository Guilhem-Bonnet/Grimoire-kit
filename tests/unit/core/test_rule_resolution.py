"""Résolution des règles par couche et par chemin (W6-05).

AI-DLC compile la chaîne org -> équipe -> projet -> phase en concaténant les
couches : rien ne dit quelle règle vaut pour un fichier, ni laquelle cède.
Ici ``resolve_rules`` est une fonction pure qui rend la liste ordonnée, la
couche d'origine de chaque règle et ce qui la fait céder.

Chaque test échoue si le module est retiré (il n'existait ni couche ``team``,
ni correspondance portée/chemin, ni rang).
"""

from __future__ import annotations

import itertools

import pytest

from grimoire.core.layered_rules import LAYERS, Rule, parse_rule
from grimoire.core.rule_resolution import (
    EMITTED_LAYERS,
    STATUS_EFFECTIVE,
    STATUS_PARTIAL,
    STATUS_SHADOWED,
    STATUS_UNRESOLVED,
    normalize_path,
    resolve_rules,
    scope_covers,
    scope_matches,
)


def _rule(text: str, layer: str = "project", line: int = 1) -> Rule:
    return parse_rule(text, layer=layer, line=line)


def test_la_couche_team_existe_entre_organisation_et_projet() -> None:
    assert LAYERS == ("organization", "team", "project", "agent")
    assert EMITTED_LAYERS == ("team", "project")  # décision 10 : ni organisation ni agent


# ── Chemins et portées ────────────────────────────────────────────────────────

MATCHES = (
    ("**", "a.py", True),
    ("**", "src/a/b.py", True),
    ("src/**", "src/a.py", True),
    ("src/**", "src/a/b/c.py", True),
    ("src/**", "src", True),  # ** absorbe zéro segment
    ("src/**", "lib/a.py", False),
    ("src/*.py", "src/a.py", True),
    ("src/*.py", "src/a/b.py", False),  # * ne franchit pas /
    ("**/*.py", "a.py", True),
    ("**/*.py", "src/a/b.py", True),
    ("**/*.py", "src/a.txt", False),
    ("docs/?.md", "docs/a.md", True),
    ("docs/?.md", "docs/ab.md", False),
    (".github/workflows/*.yml", ".github/workflows/ci.yml", True),
    ("CHANGELOG.md", "CHANGELOG.md", True),
    ("CHANGELOG.md", "docs/CHANGELOG.md", False),
)


@pytest.mark.parametrize(("scope", "path", "expected"), MATCHES)
def test_scope_matches(scope: str, path: str, expected: bool) -> None:
    assert scope_matches(scope, path) is expected


@pytest.mark.parametrize(("raw", "expected"), [("a/b.py", "a/b.py"), ("./a//b.py", "a/b.py"), ("a\\b.py", "a/b.py")])
def test_normalize_path_accepte(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw", ["", ".", "/etc/passwd", "../x", "a/../b", "C:/x"])
def test_normalize_path_refuse(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_path(raw)


SCOPES = ("**", "src/**", "src/a/**", "src/*", "src/*.py", "src/a.py", "*", "*.py", "**/*.py", "docs/**", "src/*/**", "?")
SEGMENTS = ("src", "docs", "a", "b", "a.py", "b.py", "c.txt")


def test_scope_covers_est_prudent_sur_un_univers_de_chemins() -> None:
    """``scope_covers(outer, inner)`` vrai => tout chemin de ``inner`` est un chemin de ``outer``."""
    universe = [
        "/".join(parts)
        for depth in range(1, 4)
        for parts in itertools.product(SEGMENTS, repeat=depth)
    ]
    proved = 0
    for outer, inner in itertools.product(SCOPES, repeat=2):
        if scope_covers(outer, inner):
            proved += 1
            leaked = [p for p in universe if scope_matches(inner, p) and not scope_matches(outer, p)]
            assert not leaked, f"{outer!r} ne couvre pas {inner!r} : {leaked[:3]}"
    assert proved >= 20  # le test ne passe pas à vide


@pytest.mark.parametrize(
    ("outer", "inner"),
    [("**", "src/a.py"), ("src/**", "src/a/**"), ("src/**", "src/*.py"), ("src/*", "src/a.py"), ("src/**", "src")],
)
def test_scope_covers_prouve_les_cas_evidents(outer: str, inner: str) -> None:
    assert scope_covers(outer, inner)


@pytest.mark.parametrize(
    ("outer", "inner"),
    [("src/a/**", "src/**"), ("src/*.py", "src/**"), ("lib/**", "src/**"), ("src/*", "src/**"), ("src/**", "**")],
)
def test_scope_covers_refuse_ce_qui_deborde(outer: str, inner: str) -> None:
    assert not scope_covers(outer, inner)


# ── Résolution ────────────────────────────────────────────────────────────────


def test_le_rang_suit_la_precedence_des_couches_puis_le_fichier() -> None:
    rules = [
        _rule("ALWAYS run linter", "agent", 1),
        _rule("ALWAYS run tests", "project", 2),
        _rule("ALWAYS run formatter", "project", 1),
        _rule("ALWAYS run audit", "organization", 1),
        _rule("ALWAYS run types", "team", 1),
    ]
    resolved = resolve_rules(rules)
    assert [(r.rank, r.layer, r.rule.object) for r in resolved] == [
        (1, "organization", "audit"),
        (2, "team", "types"),
        (3, "project", "formatter"),
        (4, "project", "tests"),
        (5, "agent", "linter"),
    ]
    assert all(r.status == STATUS_EFFECTIVE for r in resolved)


def test_un_chemin_ne_garde_que_les_regles_applicables_avec_leur_origine() -> None:
    rules = [
        _rule("ALWAYS run tests", "project"),
        _rule("NEVER edit generated-files @docs/api/**", "team"),
        _rule("ALWAYS use python3.12 @src/**", "project", 2),
    ]
    docs = resolve_rules(rules, "docs/api/openapi.yaml")
    assert [(r.rank, r.layer, r.rule.render()) for r in docs] == [
        (1, "team", "NEVER edit generated-files @docs/api/**"),
        (2, "project", "ALWAYS run tests"),
    ]
    src = resolve_rules(rules, "src/a.py")
    assert [r.rule.render() for r in src] == ["ALWAYS run tests", "ALWAYS use python3.12 @src/**"]
    assert [r.to_dict()["layer"] for r in src] == ["project", "project"]


def test_une_regle_de_projet_plus_etroite_ne_masque_pas_en_silence_une_regle_exterieure() -> None:
    org = _rule("NEVER edit generated-files @docs/**", "organization")
    narrow = _rule("ALWAYS edit generated-files @docs/api/**", "project")
    resolved = {r.rule: r for r in resolve_rules([org, narrow])}
    assert resolved[org].status == STATUS_EFFECTIVE
    loser = resolved[narrow]
    assert loser.status == STATUS_SHADOWED and loser.dead
    assert loser.defeated_by == (org,)  # le conflit est nommé, pas avalé
    assert loser.to_dict()["defeated_by"] == [{"layer": "organization", "line": 1, "rule": org.render()}]


def test_sur_un_chemin_commun_la_couche_exterieure_l_emporte() -> None:
    org = _rule("NEVER edit generated-files @docs/**", "organization")
    narrow = _rule("ALWAYS edit generated-files @docs/api/**", "project")
    on = {r.rule: r.status for r in resolve_rules([org, narrow], "docs/api/x.md")}
    assert on == {org: STATUS_EFFECTIVE, narrow: STATUS_SHADOWED}
    off = resolve_rules([org, narrow], "docs/guide.md")
    assert [r.rule for r in off] == [org]  # la règle étroite ne s'applique pas ici


def test_une_regle_qui_ne_cede_que_sur_le_recouvrement_reste_vivante_ailleurs() -> None:
    team = _rule("NEVER touch lockfile @src/**", "team")
    wide = _rule("ALWAYS touch lockfile @**/*.py", "project")
    resolved = {r.rule: r for r in resolve_rules([team, wide])}
    assert resolved[wide].status == STATUS_PARTIAL and not resolved[wide].dead
    assert resolved[wide].defeated_by == (team,)
    # sur un chemin où les deux s'appliquent : elle cède ; ailleurs, elle vaut seule
    assert {r.rule: r.status for r in resolve_rules([team, wide], "src/a.py")}[wide] == STATUS_SHADOWED
    assert [(r.rule, r.status) for r in resolve_rules([team, wide], "lib/a.py")] == [(wide, STATUS_EFFECTIVE)]


def test_deux_regles_opposees_de_la_meme_couche_restent_non_decidees() -> None:
    a, b = _rule("ALWAYS run tests", "project", 1), _rule("NEVER run tests", "project", 2)
    resolved = resolve_rules([a, b])
    assert [r.status for r in resolved] == [STATUS_UNRESOLVED, STATUS_UNRESOLVED]
    assert resolved[0].contested_by == (b,) and resolved[1].contested_by == (a,)


def test_une_regle_ne_cede_pas_devant_une_regle_qui_cede_elle_meme() -> None:
    org = _rule("ALWAYS run tests", "organization")
    team = _rule("NEVER run tests", "team")
    project = _rule("ALWAYS run tests", "project")
    resolved = {r.rule: r.status for r in resolve_rules([org, team, project])}
    assert resolved[team] == STATUS_SHADOWED
    assert resolved[project] == STATUS_EFFECTIVE  # l'organisation dit la même chose que lui


def _cas_b() -> tuple[Rule, Rule, Rule]:
    org = _rule("NEVER run tests @src/**", "organization")
    team = _rule("ALWAYS run tests", "team")
    project = _rule("NEVER run tests @src/a/**", "project")
    return org, team, project


def test_une_regle_partielle_ne_fait_pas_ceder_une_regle_interieure_en_entier() -> None:
    # W6-05-S1-01 : l'équipe cède devant l'organisation sur src/**, donc elle ne peut pas
    # condamner le projet sur src/a/**, où l'organisation dit la même chose que lui.
    org, team, project = _cas_b()
    resolved = {r.rule: r for r in resolve_rules([org, team, project])}
    assert resolved[team].status == STATUS_PARTIAL
    assert resolved[project].status != STATUS_SHADOWED and not resolved[project].dead
    assert resolved[project].status == STATUS_PARTIAL  # prudent : l'équipe vaut hors de src/**
    assert resolved[project].defeated_by == (team,)


def test_sur_le_chemin_du_cas_b_le_projet_reste_effectif() -> None:
    org, team, project = _cas_b()
    on = {r.rule: r.status for r in resolve_rules([org, team, project], "src/a/x.py")}
    assert on == {org: STATUS_EFFECTIVE, team: STATUS_SHADOWED, project: STATUS_EFFECTIVE}


def test_la_regle_partielle_est_emise_et_la_regle_a_l_ombre_non() -> None:
    from grimoire.core.rule_resolution import emitted_rules  # import local : le reste du module se juge sans lui

    org, team, project = _cas_b()
    assert [r.rule for r in emitted_rules(resolve_rules([org, team, project]))] == [team, project]
    inside = _rule("ALWAYS edit generated-files @docs/api/**", "project")
    outside = _rule("NEVER edit generated-files @docs/**", "organization")
    assert emitted_rules(resolve_rules([outside, inside])) == ()  # à l'ombre d'une couche extérieure
    a, b = _rule("ALWAYS run tests @src/**", "team", 1), _rule("NEVER run tests @src/gen/**", "team", 2)
    assert emitted_rules(resolve_rules([a, b])) == ()  # conflit non décidé : rien ne part


def test_la_resolution_est_pure_et_deterministe() -> None:
    rules = [_rule("ALWAYS run tests", "project"), _rule("NEVER run tests @src/**", "team")]
    snapshot = list(rules)
    first = resolve_rules(rules, "src/a.py")
    assert resolve_rules(reversed(rules), "src/a.py") == first
    assert rules == snapshot
