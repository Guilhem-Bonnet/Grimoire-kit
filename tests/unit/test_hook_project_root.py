"""Issue #717 — la racine du projet d'un hook n'est pas le cwd du shell.

Rouge avant : ``resolve_project_root`` préférait le ``cwd`` du payload (qui suit
chaque ``cd`` de l'agent) à ``CLAUDE_PROJECT_DIR``, et les hooks créaient
``_grimoire-output/`` dans n'importe quel dossier.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.runtime import resolve_project_root, run_hook

SESSION = "sess-717"
ENV_VARS = ("CLAUDE_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "COPILOT_WORKSPACE_FOLDER")


@pytest.fixture(autouse=True)
def _env_propre(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _projet(root: Path) -> Path:
    (root / "_grimoire").mkdir(parents=True)
    return root.resolve()


def test_claude_project_dir_prime_sur_un_cwd_dans_un_sous_dossier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    racine = _projet(tmp_path / "forge")
    sous = racine / "sous" / "dossier"
    sous.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(racine))
    assert resolve_project_root({"cwd": str(sous)}) == racine


def test_sans_variable_le_cwd_remonte_a_la_premiere_racine_marquee(tmp_path: Path) -> None:
    racine = _projet(tmp_path / "forge")
    sous = racine / "_scratch" / "backup"
    sous.mkdir(parents=True)
    assert resolve_project_root({"cwd": str(sous)}) == racine


def test_un_projet_imbrique_qui_porte_son_propre_marqueur_reste_resolu_comme_tel(tmp_path: Path) -> None:
    externe = _projet(tmp_path / "forge")
    interne = _projet(externe / "grimoire-kit")
    (interne / "src").mkdir()
    assert resolve_project_root({"cwd": str(interne / "src")}) == interne


def test_un_projet_imbrique_lance_dedans_garde_sa_racine_via_la_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    externe = _projet(tmp_path / "forge")
    interne = _projet(externe / "grimoire-kit")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(interne))
    assert resolve_project_root({"cwd": str(externe)}) == interne


def test_l_option_explicite_prime_sur_tout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    racine = _projet(tmp_path / "forge")
    autre = _projet(tmp_path / "autre")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(racine))
    assert resolve_project_root({"cwd": str(racine)}, autre) == autre


@pytest.mark.parametrize(
    ("event", "extra"),
    [
        ("SessionStart", {}),
        ("UserPromptSubmit", {"prompt": "bonjour"}),
        ("PostToolUse", {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x"}, "tool_response": {}}),
        ("PreCompact", {}),
        ("Stop", {}),
        ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "echo hi > x"}}),
        ("PreToolUse", {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x"}}),
        ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}),
    ],
)
def test_aucun_hook_ne_cree_de_sortie_hors_d_une_racine_de_projet(
    tmp_path: Path, event: str, extra: dict[str, object]
) -> None:
    hors_projet = tmp_path / "hors" / "projet"
    hors_projet.mkdir(parents=True)
    payload = {"session_id": SESSION, "hook_event_name": event, "cwd": str(hors_projet), **extra}
    run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    assert sorted(p.name for p in tmp_path.rglob("_grimoire-output")) == []


def test_un_cd_dans_un_sous_dossier_ne_deplace_pas_les_ecritures(tmp_path: Path) -> None:
    racine = _projet(tmp_path / "forge")
    sous = racine / "_scratch" / "qdrant-backup"
    sous.mkdir(parents=True)
    payload = {"session_id": SESSION, "hook_event_name": "UserPromptSubmit", "cwd": str(sous), "prompt": "bonjour"}
    run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    assert not (sous / "_grimoire-output").exists()
    assert (racine / "_grimoire-output").is_dir()


def _arbre(racine: Path) -> list[str]:
    return sorted(str(p.relative_to(racine)) for p in racine.rglob("*"))


@pytest.mark.parametrize(
    "commande",
    ["echo hi > x.txt", "git push --force origin main", "git reset --hard HEAD~3", "ls -la"],
)
def test_pretooluse_hors_racine_decide_sans_ecrire_un_seul_fichier(tmp_path: Path, commande: str) -> None:
    """K4-S1-01 : la décision est gardée, ses effets de bord (cache d'état, traces, holds) non."""
    hors_projet = tmp_path / "hors" / "projet"
    hors_projet.mkdir(parents=True)
    avant = _arbre(tmp_path)
    payload = {
        "session_id": SESSION,
        "hook_event_name": "PreToolUse",
        "cwd": str(hors_projet),
        "tool_name": "Bash",
        "tool_input": {"command": commande},
    }
    result, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    assert result, "le hook doit toujours rendre un verdict"
    assert decision.outcome is not None
    assert _arbre(tmp_path) == avant


def test_pretooluse_hors_racine_garde_le_verdict_du_dedans(tmp_path: Path) -> None:
    """Couper les effets ne désarme pas la politique : même verdict dedans et dehors."""
    hors = tmp_path / "hors"
    hors.mkdir()
    dedans = _projet(tmp_path / "forge")
    commande = {"command": "git push --force origin main"}
    base = {"session_id": SESSION, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": commande}
    _, hors_decision, _ = run_hook({**base, "cwd": str(hors)}, host_id=HostId.CLAUDE_CODE_CLI)
    _, dedans_decision, _ = run_hook({**base, "cwd": str(dedans)}, host_id=HostId.CLAUDE_CODE_CLI)
    assert hors_decision.outcome == dedans_decision.outcome


def test_pretooluse_dans_un_projet_ecrit_toujours_ses_traces(tmp_path: Path) -> None:
    """Le correctif ne coupe rien dans une racine reconnue."""
    racine = _projet(tmp_path / "forge")
    payload = {
        "session_id": SESSION,
        "hook_event_name": "PreToolUse",
        "cwd": str(racine),
        "tool_name": "Bash",
        "tool_input": {"command": "git push --force origin main"},
    }
    run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    assert (racine / "_grimoire-output").is_dir()


def test_pretooluse_hors_racine_ne_pose_aucun_hold_et_aucun_cache(tmp_path: Path) -> None:
    """Un hold ne s'écrit que sur un verdict non-allow : on force ce chemin avec un faux verdict."""
    from unittest import mock

    from grimoire.hosts.decisions import Decision, Outcome

    hors_projet = tmp_path / "hors" / "projet"
    hors_projet.mkdir(parents=True)
    avant = _arbre(tmp_path)
    payload = {
        "session_id": SESSION,
        "hook_event_name": "PreToolUse",
        "cwd": str(hors_projet),
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf build"},
    }
    with mock.patch(
        "grimoire.hosts.decisions.tool_policy._evaluate_tool_policy",
        return_value=Decision(outcome=Outcome.ASK, reason="test"),
    ):
        result, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    assert decision.outcome is Outcome.ASK
    assert result
    assert _arbre(tmp_path) == avant
