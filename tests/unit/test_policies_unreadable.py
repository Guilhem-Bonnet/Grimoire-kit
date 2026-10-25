"""Un ``policies.yaml`` illisible est une erreur nommée, jamais « aucune règle » (R37).

Avant, ``rules: [`` était avalé et rendu comme une liste vide : le projet
croyait ses budgets et approbations actifs, le moteur n'en appliquait aucun.
Une garde qui échoue ouvert. Maintenant ``GR-POL-004`` est levé, et
``run_decision`` rend ``ASK`` sur PreToolUse avec la cause.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.hosts.decisions import run_decision
from grimoire.hosts.decisions._shared import HookInput, Outcome
from grimoire.hosts.decisions.tool_policy import decide_tool_policy
from grimoire.hosts.events import HookEvent
from grimoire.policies.rules_config import load_custom_rules


def _write(root: Path, text: str) -> None:
    path = root / "_grimoire" / "standard" / "policies.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _hook(root: Path) -> HookInput:
    return HookInput(
        event=HookEvent.PRE_TOOL_USE,
        project_root=root,
        tool_name="Write",
        tool_input={"file_path": str(root / "f.txt")},
        session_id="sess-r37",
    )


@pytest.mark.parametrize(
    "text",
    [
        "rules: [",
        "- juste\n- une liste\n",
        "rules: {pas: une liste}\n",
        "rules:\n  - ceci n'est pas une règle\n",
    ],
)
def test_politiques_illisibles_levent_une_erreur_nommee(project: Path, text: str) -> None:
    _write(project, text)
    with pytest.raises(GrimoirePolicyError) as err:
        decide_tool_policy(_hook(project))
    assert err.value.error_code == "GR-POL-004"
    assert "policies.yaml" in str(err.value)


def test_run_decision_rend_ask_avec_la_cause(project: Path) -> None:
    _write(project, "rules: [")
    decision = run_decision("grimoire.tool-policy", _hook(project))
    assert decision.outcome is Outcome.ASK
    assert "GR-POL-004" in decision.reason or "GrimoirePolicyError" in decision.reason
    assert "policies.yaml" in decision.reason


def test_fichier_absent_ou_vide_ou_sans_rules_reste_sans_regle(project: Path) -> None:
    assert load_custom_rules(project) == ()
    _write(project, "")
    assert load_custom_rules(project) == ()
    _write(project, "# rien du tout\n")
    assert load_custom_rules(project) == ()
    _write(project, "rules: []\n")
    assert load_custom_rules(project) == ()


def test_docstring_des_decisions_dit_ce_que_le_code_rend() -> None:
    """R44 : le docstring ne promet plus ALLOW pour un crash en PreToolUse (le code rend ASK)."""
    import grimoire.hosts.decisions as decisions

    doc = decisions.__doc__ or ""
    assert "Outcome.ASK" in doc
    assert "degrade to :data:`Outcome.ALLOW`" not in doc


@pytest.mark.parametrize(
    "text",
    [
        "rule:\n  - id: x\n",  # singulier
        "Rules:\n  - id: x\n",  # casse
        "rules:\n",  # null dans un fichier non vide
        "autre: 1\n",  # clés, mais pas de rules
    ],
)
def test_faute_de_frappe_sur_la_cle_racine_n_est_pas_aucune_regle(project: Path, text: str) -> None:
    _write(project, text)
    with pytest.raises(GrimoirePolicyError) as err:
        load_custom_rules(project)
    assert err.value.error_code == "GR-POL-004"


def test_rules_vide_explicite_et_entete_restent_valides(project: Path) -> None:
    _write(project, '$schema: "x"\nmetadata: {a: 1}\nrules: []\n')
    assert load_custom_rules(project) == ()
    _write(project, "# juste un commentaire\n")
    assert load_custom_rules(project) == ()


def test_dossier_ou_lien_casse_n_est_pas_absent(project: Path) -> None:
    path = project / "_grimoire" / "standard" / "policies.yaml"
    path.mkdir(parents=True)
    with pytest.raises(GrimoirePolicyError):
        load_custom_rules(project)
    path.rmdir()
    try:
        path.symlink_to(project / "nulle-part.yaml")
    except OSError:  # pragma: no cover - symlinks indisponibles (Windows sans droits)
        pytest.skip("liens symboliques indisponibles")
    with pytest.raises(GrimoirePolicyError):
        load_custom_rules(project)


def test_outil_en_lecture_seule_passe_avec_avertissement_le_reste_demande(project: Path) -> None:
    _write(project, "rules: [")
    read = HookInput(
        event=HookEvent.PRE_TOOL_USE,
        project_root=project,
        tool_name="Read",
        tool_input={"file_path": str(project / "_grimoire/standard/policies.yaml")},
        session_id="sess-r37-read",
    )
    decision = run_decision("grimoire.tool-policy", read)
    assert decision.outcome is Outcome.ALLOW
    assert "GR-POL-004" in decision.context or "illisible" in decision.context
    assert run_decision("grimoire.tool-policy", _hook(project)).outcome is Outcome.ASK
