"""Un outil nommé dans une compilation doit consommer le format qu'il exécute.

Le mission pack disait « Gate CI : exécuter via ``agent-test`` » pour les évals
comportementales. ``agent-test`` teste des agents par catégorie — persona,
outils, périmètre — et ne contient pas une seule référence aux suites attachées
à un blueprint. La commande promise n'aurait rien fait, et rien ne le signalait :
la section compilait, le lint passait, les tests étaient verts.

C'est une classe de dérive, pas un incident. Elle apparaît chaque fois qu'une
capacité est ajoutée d'un côté sans que l'autre soit repris, et elle est
invisible tant que personne n'essaie vraiment de suivre l'instruction.

Ce test la rend mécanique. Toute compilation qui nomme un exécutant doit le
déclarer ici, et l'exécutant doit prouver qu'il lit le format concerné. Ajouter
une promesse sans l'honorer fait échouer la suite — la promesse devient donc
un engagement vérifié plutôt qu'une phrase.

Le modèle est celui du ratchet de taille de code : ce qui tient n'est pas la
vigilance, c'est ce qui est automatique.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from grimoire.tools.blueprint_checkpoint import compile_checkpoint_section
from grimoire.tools.blueprint_evals import compile_evals_section
from grimoire.tools.blueprint_gate import compile_gate_section
from grimoire.tools.blueprint_scatter import compile_scatter_section

ROOT = Path(__file__).resolve().parents[3]

#: Exécutants connus : ce qu'il faut trouver dans leur source pour croire
#: qu'ils consomment vraiment le format qu'on leur confie. Un exécutant absent
#: de cette table ne peut pas être nommé dans une compilation.
EXECUTORS: dict[str, tuple[Path, tuple[str, ...]]] = {
    # Rejeu des évals déclarées. Les marqueurs sont les genres d'assertion du
    # format : un exécutant qui ne les nomme pas ne peut pas les vérifier.
    "grimoire blueprint evals": (
        Path("src/grimoire/tools/blueprint_eval_runner.py"),
        ("no-refusal", "path-taken", "recordVersion"),
    ),
}

#: Mots entre accents graves qui ne désignent pas un exécutant : contrats,
#: canaux, champs de configuration. Les lister évite de confondre le
#: vocabulaire du format avec une promesse d'exécution.
NOT_EXECUTORS = frozenset({
    "error-envelope", "escalation", "failure", "happy", "evidence-pack",
    "handoff-packet", "task-envelope", "context-pack", "verification-verdict",
    "state", "state+artifacts", "full", "config.evals", "evals",
    "agent.md", "on_failure", "block", "human", "budget", "guardrail",
    "mcp-trust", "output-contract", "timeout", "refusal", "schema-violation",
    "rate-limit", "unknown",
})

#: Un mot pouvant faire partie d'un nom de commande : minuscules ASCII,
#: chiffres, tirets. Volontairement strict — la prose française porte des
#: accents et tombe donc d'elle-même.
_WORD = re.compile(r"^[a-z][a-z0-9-]*$")

#: Ce qui, dans un token entre accents graves, est un argument plutôt qu'un
#: nom : un placeholder `<flow>`, un drapeau `--record`, un chemin.
_ARGLIKE = re.compile(r"^(?:<[^>]*>|--?[a-z0-9-]+|[./~$][^\s]*)$")

#: Profondeur maximale d'un nom de commande. `grimoire blueprint evals` en
#: fait trois ; au-delà on lit de la prose, pas une invocation.
_MAX_DEPTH = 3


def _sections() -> list[tuple[str, list[str]]]:
    """Toutes les sections compilables, sur des entrées qui les déclenchent."""
    node_evals: dict[str, Any] = {
        "id": "n", "config": {"evals": {"version": "1.0", "cases": [
            {"id": "c", "input": {}, "assert": [{"kind": "contract", "contract": "evidence-pack"}]}
        ]}},
    }
    node_gate: dict[str, Any] = {
        "id": "g", "role": "Gate",
        "config": {"gate": {"mode": "human", "params": {"action": "approve"}}},
    }
    node_scatter: dict[str, Any] = {
        "id": "s", "role": "Scatter",
        "config": {"scatter": {"over": "fichiers", "maxParallel": 4}},
    }
    blueprint: dict[str, Any] = {
        "nodes": [node_gate],
        "edges": [],
        "boundaries": [{"id": "ck", "mode": "checkpoint", "members": ["g"]}],
    }
    return [
        ("évals", compile_evals_section(node_evals)),
        ("porte", compile_gate_section(node_gate)),
        ("éclatement", compile_scatter_section(node_scatter)),
        ("reprise", compile_checkpoint_section(blueprint)),
    ]


#: Ce qui fait d'une ligne une instruction d'exécution. On ne cherche pas les
#: mots entre accents graves partout — un identifiant de cas en porte aussi —
#: mais ceux qui apparaissent dans une phrase demandant de lancer quelque chose.
_ACTION = re.compile(r"\b(exécuter|exécut|lancer|via|commande|gate ci|run)\b", re.IGNORECASE)


def _command_name(token: str) -> str | None:
    """Nom de commande porté par un token, arguments retirés.

    « grimoire blueprint evals <flow> --record <trace> » donne « grimoire
    blueprint evals ». Un token qui n'est pas une invocation donne ``None`` —
    sans quoi chaque bout de prose entre accents graves serait pris pour une
    promesse d'exécution.
    """
    words = token.strip().split()
    if not words:
        return None
    head: list[str] = []
    for word in words:
        if _WORD.match(word):
            head.append(word)
            continue
        if _ARGLIKE.match(word):
            break          # les arguments commencent : le nom est complet
        return None        # ni mot de commande ni argument → ce n'est pas ça
    if not head:
        return None
    # Un exécutant déclaré est reconnu même si le token continue au-delà de la
    # profondeur maximale — c'est la table qui fait autorité, pas l'heuristique.
    for depth in range(min(len(head), _MAX_DEPTH), 0, -1):
        candidate = " ".join(head[:depth])
        if candidate in EXECUTORS:
            return candidate
    return " ".join(head[:_MAX_DEPTH]) if len(head) <= _MAX_DEPTH else None


def _claimed_executors(lines: list[str]) -> set[str]:
    """Exécutants nommés dans une ligne qui demande de lancer quelque chose."""
    found: set[str] = set()
    for line in lines:
        if not _ACTION.search(line):
            continue
        for token in re.findall(r"`([^`]+)`", line):
            if token.strip() in NOT_EXECUTORS:
                continue
            name = _command_name(token)
            if name and name not in NOT_EXECUTORS:
                found.add(name)
    return found


@pytest.mark.parametrize("name,lines", _sections(), ids=lambda v: v if isinstance(v, str) else "")
def test_aucune_promesse_d_execution_non_honoree(name: str, lines: list[str]) -> None:
    """Chaque exécutant nommé doit être déclaré et prouver qu'il lit le format."""
    for claim in sorted(_claimed_executors(lines)):
        assert claim in EXECUTORS, (
            f"la section « {name} » nomme `{claim}` comme exécutant, mais il "
            f"n'est pas déclaré dans EXECUTORS. Soit il consomme réellement le "
            f"format et il faut l'y inscrire avec ses marqueurs, soit la "
            f"compilation promet une commande sans effet — c'est ce qui est "
            f"arrivé à `agent-test` pour les évals."
        )
        path, markers = EXECUTORS[claim]
        target = ROOT / path
        assert target.is_file(), f"`{claim}` déclaré mais introuvable : {path}"
        source = target.read_text(encoding="utf-8", errors="replace")
        assert any(m in source for m in markers), (
            f"`{claim}` est nommé pour la section « {name} » mais sa source ne "
            f"contient aucun de {markers} — rien n'indique qu'il lise ce format."
        )


def test_les_executants_declares_existent_et_consomment() -> None:
    """La table elle-même reste vraie, même si aucune section ne les cite."""
    for claim, (path, markers) in EXECUTORS.items():
        target = ROOT / path
        assert target.is_file(), f"`{claim}` : {path} introuvable"
        source = target.read_text(encoding="utf-8", errors="replace")
        assert any(m in source for m in markers), f"`{claim}` ne consomme plus {markers}"


def test_le_detecteur_repere_une_promesse() -> None:
    """Sans ce contrôle, une table vide ferait passer le test pour une garantie."""
    assert _claimed_executors(["- Gate CI : exécuter via `agent-test`"]) == {"agent-test"}
    assert _claimed_executors(["- via `standard gate` après la porte"]) == {"standard gate"}


def test_le_detecteur_ignore_le_vocabulaire_du_format() -> None:
    assert _claimed_executors(["- contrat `evidence-pack`, canal `failure`"]) == set()


def test_le_detecteur_voit_une_commande_avec_ses_arguments() -> None:
    """Le piège du premier jet : la ligne réellement compilée porte des
    arguments, et un détecteur limité à deux mots nus l'aurait ignorée — donc
    validée en silence, ce que ce fichier existe précisément pour empêcher."""
    line = "- Gate CI : `grimoire blueprint evals <flow> --record <trace>`"
    assert _claimed_executors([line]) == {"grimoire blueprint evals"}


def test_le_detecteur_ne_prend_pas_la_prose_pour_une_commande() -> None:
    assert _claimed_executors(["- exécuter `avec les cas déclarés plus haut`"]) == set()
    assert _claimed_executors(["- lancer `Analyse Complète`"]) == set()
