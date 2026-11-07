"""``grimoire doctor`` signale un hook de l'hôte absent du PATH de l'unité du tick (issue #1022).

Le tick lance ses sessions avec le ``PATH`` réduit de
:func:`grimoire.missions.timer._unit_path`. Un hook utilisateur ou projet
appelé par chemin absolu voit son dossier entrer dans ce ``PATH`` (#1111) —
sauf s'il est temporaire, absent, relatif ou coupé par une fin de ligne. Un
hook qui réécrit alors ``git status`` en ``rtk git status`` fait échouer la
session en 127 : le worker s'arrête au premier geste, sans rapport avec la
tâche. Le contrôle ``hook_path`` doit nommer ce hook, et se taire quand il se
résout.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from pathlib import Path

import pytest

from grimoire.cli.doctor_hooks import _unresolvable_hooks, check_hook_path

#: Sous Windows, ``shutil.which`` ne retient qu'un fichier dont l'extension est
#: dans ``PATHEXT``.
_EXE = ".exe" if os.name == "nt" else ""


@pytest.fixture(autouse=True)
def _user_settings_isolees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Les réglages utilisateur lus par ``doctor`` sont ceux de ce test, jamais ceux du poste."""
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config / "settings.json"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    root.mkdir()
    return root


def _programme_unique() -> str:
    return f"hook-{uuid.uuid4().hex[:12]}{_EXE}"


def _executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    program = directory / name
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    if os.name != "nt":
        program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return program


def _ecrire_reglages(path: Path, commandes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": commande} for commande in commandes],
                }],
            },
        }),
        encoding="utf-8",
    )


def _sans_dossier_temporaire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retire ``tmp_path`` de la liste des dossiers temporaires, pour exercer la branche « gardé ».

    ``_unit_path`` écarte tout dossier sous ``tempfile.gettempdir()`` ; sans ce
    détour, un binaire créé par un test serait toujours exclu, et seul le cas
    « signalé » serait atteignable.
    """
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "faux-temp"))


# ── Résolution du programme ───────────────────────────────────────────────────


def test_un_hook_absolu_resolu_dans_le_path_de_l_unite_se_tait(
    tmp_path: Path, project: Path, _user_settings_isolees: Path
) -> None:
    bin_dir = tmp_path / "bin"
    programme = _executable(bin_dir, _programme_unique())
    _ecrire_reglages(_user_settings_isolees, [f"{programme} hook claude"])

    assert _unresolvable_hooks(project, str(bin_dir)) == []


def test_un_hook_absolu_hors_du_path_de_l_unite_est_signale(
    tmp_path: Path, project: Path, _user_settings_isolees: Path
) -> None:
    programme = _executable(tmp_path / "bin", _programme_unique())
    _ecrire_reglages(_user_settings_isolees, [f"{programme} hook claude"])

    problems = _unresolvable_hooks(project, str(tmp_path / "ailleurs"))

    assert len(problems) == 1
    assert programme.name in problems[0]


def test_un_hook_au_nom_nu_resolu_dans_le_path_de_l_unite_se_tait(
    tmp_path: Path, project: Path, _user_settings_isolees: Path
) -> None:
    bin_dir = tmp_path / "bin"
    programme = _executable(bin_dir, _programme_unique())
    _ecrire_reglages(_user_settings_isolees, [f"{programme.name} hook claude"])

    assert _unresolvable_hooks(project, str(bin_dir)) == []


def test_un_hook_au_nom_nu_absent_du_path_de_l_unite_est_signale(
    tmp_path: Path, project: Path, _user_settings_isolees: Path
) -> None:
    nom = _programme_unique()
    _ecrire_reglages(_user_settings_isolees, [f"{nom} hook claude"])

    problems = _unresolvable_hooks(project, str(tmp_path / "bin"))

    assert len(problems) == 1
    assert nom in problems[0]


def test_un_programme_absent_du_dossier_du_hook_est_signale(
    tmp_path: Path, project: Path, _user_settings_isolees: Path
) -> None:
    """Le dossier est dans le PATH, mais l'exécutable nommé n'y est plus."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nom = _programme_unique()
    _ecrire_reglages(_user_settings_isolees, [f"{bin_dir / nom} hook claude"])

    problems = _unresolvable_hooks(project, str(bin_dir))

    assert len(problems) == 1
    assert nom in problems[0]


def test_les_reglages_utilisateur_et_projet_sont_balayes(
    tmp_path: Path, project: Path, _user_settings_isolees: Path
) -> None:
    utilisateur = _programme_unique()
    projet = _programme_unique()
    _ecrire_reglages(_user_settings_isolees, [f"{tmp_path / 'a' / utilisateur} hook"])
    _ecrire_reglages(project / ".claude" / "settings.json", [f"{tmp_path / 'b' / projet} hook"])

    problems = _unresolvable_hooks(project, "")

    assert len(problems) == 2
    assert utilisateur in problems[0]
    assert projet in problems[1]


@pytest.mark.parametrize(
    "contenu",
    [
        "{pas du json",
        "[]",
        '{"hooks": "x"}',
        '{"hooks": {"PreToolUse": "x"}}',
        '{"hooks": {"PreToolUse": ["x", {"hooks": "x"}]}}',
        '{"hooks": {"PreToolUse": [{"hooks": [{"command": ""}]}]}}',
        '{"hooks": {"PreToolUse": [{"hooks": [{"command": "\'non-ferme"}]}]}}',
        '{"hooks": {"PreToolUse": [{"hooks": [{"command": "relatif/rtk hook"}]}]}}',
    ],
)
def test_des_reglages_illisibles_ne_signalent_rien(
    project: Path, _user_settings_isolees: Path, contenu: str
) -> None:
    _user_settings_isolees.parent.mkdir(parents=True, exist_ok=True)
    _user_settings_isolees.write_text(contenu, encoding="utf-8")

    assert _unresolvable_hooks(project, "") == []


# ── Intégration au PATH réel de l'unité ───────────────────────────────────────


def test_un_hook_dans_un_dossier_temporaire_est_signale(
    tmp_path: Path, project: Path, _user_settings_isolees: Path
) -> None:
    """``_unit_path`` écarte les dossiers temporaires : le hook n'y est pas résoluble."""
    programme = _executable(tmp_path / "bin", _programme_unique())
    _ecrire_reglages(_user_settings_isolees, [f"{programme} hook claude"])

    (check,) = check_hook_path(project)

    assert check.name == "hook_path"
    assert check.passed is True, "une unité peut ne pas être installée : doctor n'échoue pas"
    assert check.level == "warn"
    assert programme.name in check.detail
    assert check.remedy


def test_un_hook_que_l_unite_garde_se_tait(
    tmp_path: Path, project: Path, _user_settings_isolees: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sans_dossier_temporaire(tmp_path, monkeypatch)
    programme = _executable(tmp_path / "bin", _programme_unique())
    _ecrire_reglages(_user_settings_isolees, [f"{programme} hook claude"])

    assert check_hook_path(project) == []


def test_un_hook_dont_le_dossier_manque_est_signale(
    tmp_path: Path, project: Path, _user_settings_isolees: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un dossier absent ne peut pas entrer dans le PATH, même hors des dossiers temporaires."""
    _sans_dossier_temporaire(tmp_path, monkeypatch)
    nom = _programme_unique()
    _ecrire_reglages(_user_settings_isolees, [f"{tmp_path / 'disparu' / nom} hook claude"])

    (check,) = check_hook_path(project)

    assert nom in check.detail


@pytest.mark.skipif(os.name == "nt", reason="un nom de dossier ne peut pas contenir de fin de ligne")
def test_un_hook_dont_le_dossier_porte_une_fin_de_ligne_est_signale(
    tmp_path: Path, project: Path, _user_settings_isolees: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le filtre des fins de ligne de ``_unit_path`` tient : doctor le voit, et le signale."""
    _sans_dossier_temporaire(tmp_path, monkeypatch)
    bin_dir = tmp_path / "bin\nExecStartPre=evil"
    programme = _executable(bin_dir, _programme_unique())
    _ecrire_reglages(_user_settings_isolees, [f"{programme} hook claude"])

    (check,) = check_hook_path(project)

    assert programme.name in check.detail


# ── Branchement dans les sondes de doctor ────────────────────────────────────


def test_run_env_checks_inclut_le_controle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli import cmd_up
    from grimoire.cli.env_checks import EnvCheck

    sentinelle = EnvCheck("hook_path", passed=True, level="warn", detail="sentinelle")
    monkeypatch.setattr(cmd_up, "check_hook_path", lambda _target: [sentinelle])

    assert sentinelle in cmd_up.run_env_checks(tmp_path)
