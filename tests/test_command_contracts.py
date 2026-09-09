"""Contrats de commande — `up`, `cockpit`, `doctor` exécutés pour de vrai.

Étage manquant identifié par l'issue #353 : la suite historique vérifie ces
commandes via `typer.testing.CliRunner` — en mémoire, dans le process de test,
sans jamais passer par un vrai sous-processus ni relire l'état qu'elles
laissent sur le disque autrement qu'à travers la sortie que `CliRunner` a
capturée. `tests/test_cmd_up.py`, `tests/test_kit_integrity.py` et
`tests/integration/test_cli_workflows.py` (malgré son nom) le font tous les
trois. C'est un contrat plus faible que celui qu'un utilisateur observe : deux
défauts réels — `up` qui rétrograde un profil de conformité en rapportant
`done` (#344, corrigé par la PR #348) et un agent d'override invisible au
diagnostic (#345, corrigé par la PR #349) — ont survécu à cette suite verte
pendant tout un cycle.

Chaque test ici lance `python -m grimoire <commande>` en sous-processus réel
(exactement comme l'utilisateur le tape), vérifie son code de retour, sa
sortie, ET relit le résultat sur le disque de façon indépendante — jamais la
mémoire du process qui vient de tourner.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

_yaml = YAML(typ="safe")


def _grimoire(args: list[str], cwd: Path, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Un vrai sous-processus `python -m grimoire` — pas un `CliRunner` en mémoire.

    `HOME` est déjà détourné par `_isolate_user_state` (autouse, `tests/conftest.py`)
    et hérité via `os.environ` : aucun de ces appels ne touche l'état réel de
    la machine qui lance la suite.
    """
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )


def _standard_profile(root: Path) -> dict:
    """Le profil du standard, relu directement sur le disque après coup."""
    marker = root / "_grimoire" / "standard" / "standard-profile.yaml"
    with marker.open(encoding="utf-8") as fh:
        return _yaml.load(fh)


class TestUpContract:
    """`grimoire up`, en sous-processus réel — régression #344 (PR #348)."""

    def test_up_sans_needs_ne_retrograde_pas_le_profil_installe(self, tmp_path: Path) -> None:
        """Avant la PR #348 : un `up` sans `--needs` réécrivait le profil avec le
        défaut `starter`, désenregistrant toutes les policies d'un profil
        `governed` déjà en place, et rapportait `done`. Reproduit ici par deux
        vrais appels en sous-processus séparés, avec relecture indépendante du
        fichier sur le disque — pas de la mémoire du premier process.
        """
        target = tmp_path / "proj"
        up1 = _grimoire(
            [
                "up", str(target), "--backend", "local", "--name", "contrat",
                "--needs", "multi-agent-orchestration,hooks-skills-governance,observability-cockpit",
            ],
            tmp_path,
        )
        assert up1.returncode == 0, up1.stdout + up1.stderr
        profile_after_needs = _standard_profile(target)
        assert profile_after_needs["profile"] == "governed"
        artifacts_after_needs = len(profile_after_needs["artifacts"])
        assert artifacts_after_needs >= 20, "un profil `governed` réel compte plusieurs dizaines d'artefacts"

        up2 = _grimoire(["up", str(target)], tmp_path)
        assert up2.returncode == 0, up2.stdout + up2.stderr

        profile_after_bare_up = _standard_profile(target)
        assert profile_after_bare_up["profile"] == "governed", (
            "le profil a été rétrogradé par un `up` sans --needs — sortie :\n" + up2.stdout + up2.stderr
        )
        assert len(profile_after_bare_up["artifacts"]) == artifacts_after_needs, (
            "le nombre d'artefacts déclarés a changé sans --needs explicite"
        )

    def test_up_avec_needs_explicite_qui_reduit_le_dit_et_ne_rapporte_pas_done(self, tmp_path: Path) -> None:
        """Contre-épreuve du même correctif : une réduction *explicite* du
        périmètre (`--needs` plus restreint que celui déjà installé) reste
        honorée, mais rapportée comme un changement — jamais un `done`
        silencieux. C'est ce mot qui a laissé filer la régression #344 : un
        garde qui se contenterait de vérifier "le profil ne descend jamais"
        interdirait aussi ce cas légitime.
        """
        target = tmp_path / "proj"
        up1 = _grimoire(
            [
                "up", str(target), "--backend", "local", "--name", "contrat",
                "--needs", "multi-agent-orchestration,hooks-skills-governance,observability-cockpit",
            ],
            tmp_path,
        )
        assert up1.returncode == 0, up1.stdout + up1.stderr
        assert _standard_profile(target)["profile"] == "governed"

        reduced = _grimoire(["up", str(target), "--needs", "provider-neutral"], tmp_path)
        assert reduced.returncode == 0, reduced.stdout + reduced.stderr
        combined = reduced.stdout + reduced.stderr
        assert "changed" in combined, "une réduction explicite doit être rapportée comme un changement"
        assert "'governed' -> 'controlled'" in combined
        assert _standard_profile(target)["profile"] == "controlled"


class TestDoctorContract:
    """`grimoire doctor`, en sous-processus réel — régression #345 (PR #349)."""

    def test_doctor_voit_un_agent_depose_en_override(self, tmp_path: Path) -> None:
        """Avant la PR #349 : un agent déposé sous `_grimoire/overrides/agents/`
        restait invisible aux deux mécanismes qui décident qui existe — le
        diagnostic le déclarait « routé mais non installé », et la carte de
        routage régénérée par `up` ne le mentionnait jamais. Reproduit avec un
        vrai projet scaffoldé par un vrai sous-processus `up` (pas par
        `ProjectScaffolder` appelé directement, comme le fait
        `tests/test_kit_integrity.py`), puis un vrai sous-processus `doctor`.
        """
        target = tmp_path / "proj"
        up = _grimoire(["up", str(target), "--backend", "local", "--name", "contrat", "--no-standard"], tmp_path)
        assert up.returncode == 0, up.stdout + up.stderr

        override = target / "_grimoire" / "overrides" / "agents" / "site-agent.md"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(
            '---\nname: "site-agent"\ndescription: "Agent propre au projet"\n---\n'
            '<agent name="Cassandre">Persona propre au projet, jamais livrée par le kit.</agent>\n',
            encoding="utf-8",
        )

        # La carte de routage de la persona d'entrée n'est régénérée qu'au
        # `plan()` ou au `up` (limite connue, documentée par la PR #349) :
        # il faut donc relancer `up` pour que l'override y apparaisse.
        rerun = _grimoire(["up", str(target)], tmp_path)
        assert rerun.returncode == 0, rerun.stdout + rerun.stderr

        concierge = target / "_grimoire" / "kit" / "agents" / "concierge.md"
        assert "site-agent" in concierge.read_text(encoding="utf-8"), (
            "l'override n'a pas été repris par la carte de routage régénérée"
        )

        doctor = _grimoire(["doctor", str(target)], tmp_path)
        combined = doctor.stdout + doctor.stderr
        assert doctor.returncode == 0, combined
        assert "routé(s) mais non installé(s)" not in combined, (
            "l'agent d'override est signalé comme fantôme par `doctor` — sortie :\n" + combined
        )
        assert "carte de routage cohérente" in combined
