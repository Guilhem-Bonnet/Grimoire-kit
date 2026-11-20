"""Levier 6 — la mémoire apprend au lieu de s'encombrer.

Constat d'origine : 854 souvenirs sur un projet réel, une quinzaine de vraies
leçons ; le reste recopiait le ledger (fiches de clôture), venait de tests, ou
doublonnait. Ces tests tiennent le ménage (``memory prune``) : il liste sans
écrire par défaut, retire (jamais ne supprime) sur ``--apply``, et ne retire
rien de plus au second passage.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.lessons import (
    DUPLICATE,
    FIXTURE,
    LEDGER_COPY,
    apply_prune,
    is_fixture,
    is_ledger_copy,
    is_lesson,
    plan_prune,
)
from grimoire.memory.manager import MemoryManager
from grimoire.memory.supersession import INCLUDE_ALL, is_retracted

COPIE = "Tâche GAO-webhook-001 clôturée : Configurer le webhook. Critères : le webhook répond 200."
LECON = "Leçon de GAO-webhook-002 (Webhook) : le certificat TLS du staging expire chaque lundi, le renouveler avant."
PIEGE = "Piège : uv run ignore VIRTUAL_ENV quand le chemin du projet diffère, toujours vérifier l'interpréteur."


@pytest.fixture
def memoire(tmp_path: Path) -> MemoryManager:
    return MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "memoire.json"))


def _seme(mgr: MemoryManager) -> None:
    """Un store qui ressemble au constat : leçons, recopies, fixture, doublon exact."""
    mgr.remember("decisions", "claude", LECON, tags=("GAO-webhook-002", "lesson"))
    mgr.remember("decisions", "claude", COPIE, tags=("GAO-webhook-001", "task-recall"))
    mgr.remember("decisions", "claude", "Tâche GAO-autre-001 clôturée : Autre. Critères : x.")
    mgr.remember("failures", "claude", "Entrée écrite par un test.", tags=("fixture",))
    mgr.remember("failures", "codex", PIEGE)
    mgr.remember("failures", "copilot", PIEGE)  # même texte, autre agent : doublon exact


def _entry(text: str, **kw: object) -> MemoryEntry:
    return MemoryEntry(id="x", text=text, **kw)  # type: ignore[arg-type]


# ── jugement ──────────────────────────────────────────────────────────────────


def test_la_fiche_de_cloture_recopiee_n_est_pas_une_lecon() -> None:
    assert is_ledger_copy(_entry(COPIE))
    assert not is_lesson(_entry(COPIE))
    assert is_lesson(_entry(LECON))


@pytest.mark.parametrize(
    "entry",
    [
        _entry("x", tags=("pytest",)),
        _entry("x", user_id="test"),
        _entry("x", metadata={"source_uri": "tests/unit/test_x.py"}),
        _entry("x", metadata={"source_uri": "/var/tmp/pytest-of-ci/pytest-3/test_a0/f.md"}),
        _entry("x", metadata={"fixture": True}),
    ],
)
def test_une_entree_de_test_ou_de_fixture_est_reconnue(entry: MemoryEntry) -> None:
    assert is_fixture(entry)
    assert not is_lesson(entry)


def test_une_entree_du_projet_n_est_pas_prise_pour_une_fixture() -> None:
    assert not is_fixture(_entry(PIEGE, user_id="claude", metadata={"source_uri": "src/grimoire/x.py"}))


# ── ménage ────────────────────────────────────────────────────────────────────


def test_le_plan_compte_et_liste_sans_rien_ecrire(memoire: MemoryManager) -> None:
    _seme(memoire)

    plan = plan_prune(memoire.get_all())

    assert plan.before == 6
    assert plan.counts() == {LEDGER_COPY: 2, FIXTURE: 1, DUPLICATE: 1}
    assert plan.after == 2
    assert not plan.applied
    assert not any(is_retracted(e) for e in memoire.get_all(**INCLUDE_ALL))


def test_apply_retire_sans_supprimer_et_garde_les_lecons(memoire: MemoryManager) -> None:
    _seme(memoire)

    plan = apply_prune(memoire, plan_prune(memoire.get_all()))

    assert plan.applied and not plan.skipped
    assert len(plan.retracted) == 4
    served = memoire.get_all()
    assert sorted(e.text for e in served) == sorted([LECON, PIEGE])
    # retirés, pas supprimés : toujours lisibles avec leur marque
    everything = memoire.get_all(**INCLUDE_ALL)
    assert len(everything) == 6
    assert sum(is_retracted(e) for e in everything) == 4
    marked = memoire.recall(plan.retracted[0])
    assert marked is not None
    assert "memory prune" in marked.metadata["retracted"]["reason"]


def test_le_doublon_garde_un_exemplaire_et_nomme_l_original(memoire: MemoryManager) -> None:
    _seme(memoire)

    dup = next(c for c in plan_prune(memoire.get_all()).candidates if c.kind == DUPLICATE)
    original = memoire.recall(dup.duplicate_of)

    assert original is not None and original.text == PIEGE
    assert dup.id != original.id


def test_le_menage_est_idempotent(memoire: MemoryManager) -> None:
    _seme(memoire)
    apply_prune(memoire, plan_prune(memoire.get_all()))

    second = apply_prune(memoire, plan_prune(memoire.get_all()))

    assert second.candidates == []
    assert second.retracted == []
    assert second.before == second.after == 2


def test_un_refus_du_geste_est_note_sans_casser_le_menage(memoire: MemoryManager) -> None:
    _seme(memoire)
    plan = plan_prune(memoire.get_all())
    refuse = plan.candidates[0].id
    original = memoire.retract

    def retract(entry_id: str, **kw: object) -> object:
        if entry_id == refuse:
            raise RuntimeError("backend en lecture seule")
        return original(entry_id, **kw)  # type: ignore[arg-type]

    with patch.object(memoire, "retract", side_effect=retract):
        apply_prune(memoire, plan)

    assert refuse in plan.skipped
    assert len(plan.retracted) == len(plan.candidates) - 1
    assert plan.after == plan.before - len(plan.retracted)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli(mgr: MemoryManager, *args: str) -> dict[str, object]:
    from grimoire.cli.app import app

    with patch("grimoire.cli.cmd_memory._load_manager", return_value=mgr):
        result = CliRunner().invoke(app, ["-o", "json", "memory", "prune", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_prune_est_un_dry_run_par_defaut_puis_retire_sur_apply(memoire: MemoryManager) -> None:
    _seme(memoire)

    dry = _cli(memoire)
    assert dry["applied"] is False
    assert dry["before"] == 6 and dry["after"] == 2
    assert len(memoire.get_all()) == 6

    done = _cli(memoire, "--apply")
    assert done["applied"] is True
    assert len(done["retracted"]) == 4  # type: ignore[arg-type]
    assert len(memoire.get_all()) == 2

    again = _cli(memoire, "--apply")
    assert again["candidates"] == [] and again["before"] == again["after"] == 2
