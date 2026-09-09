"""Le sommet de la pyramide : ce qui ne doit jamais arriver, quel que soit le chemin.

Trois invariants nommés par l'issue #353 :

1. Un profil de conformité ne rétrograde jamais en silence.
2. Le registre du cockpit d'un utilisateur n'est jamais écrit par la suite de
   tests. **Déjà enforced ailleurs** : `tests/conftest.py` prend une empreinte
   du vrai `~/.grimoire/cockpit/registry.json` à `pytest_sessionstart` et la
   compare à `pytest_sessionfinish`, faisant échouer la session entière si
   elle a bougé (#339). Rien à ajouter ici — le dupliquer affaiblirait la
   preuve en la répartissant sur deux mécanismes au lieu d'un seul qui
   couvre déjà toute la suite, sous-processus `grimoire` compris.
3. Une commande ne dépasse jamais un budget de temps sur sa première réponse.

Les deux invariants ci-dessous se distinguent des contrats de commande de
`tests/test_command_contracts.py` : un contrat vérifie qu'*un* appel se
comporte bien, un invariant vérifie qu'*aucun* chemin ne peut le violer — d'où
des scénarios multiples par test plutôt qu'un seul aller-retour.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

import pytest
from ruamel.yaml import YAML

_yaml = YAML(typ="safe")

#: Budget de première réponse pour `grimoire cockpit refresh` sur un registre
#: pollué. Mesuré empiriquement sur un registre synthétique (1 projet vivant +
#: 100 fantômes) : ~2,1 s avec le correctif de la PR #343, ~13,9 s en le
#: retirant temporairement (`scripts/gen-site-data.py` et
#: `src/grimoire/cli/cmd_cockpit.py` restaurés à leur état d'avant la PR #343
#: pour la mesure, puis remis en place). Une large marge sépare les deux pour
#: que la variance d'une machine CI plus lente ne fasse jamais échouer ce
#: garde à tort, sans jamais s'approcher du plancher du défaut réel.
_COCKPIT_REFRESH_BUDGET_SECONDS = 8.0
_GHOST_COUNT = 100


def _grimoire(args: list[str], cwd: Path, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Un vrai sous-processus `python -m grimoire` — `HOME` déjà isolé par
    l'autouse `_isolate_user_state` de `tests/conftest.py` et hérité via
    `os.environ`."""
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, check=False, timeout=180,
    )


def _standard_profile(root: Path) -> dict:
    marker = root / "_grimoire" / "standard" / "standard-profile.yaml"
    with marker.open(encoding="utf-8") as fh:
        return _yaml.load(fh)


# ── Invariant 1 : le profil de conformité ne rétrograde jamais en silence ──


class TestLeProfilNeRetrogradeJamaisEnSilence:
    """Régression #344. Plusieurs chemins, une seule propriété : le rang du
    profil installé (`starter < controlled < orchestrated < governed <
    production`) ne peut baisser qu'avec un `--needs` explicite et plus étroit
    — jamais par simple absence de flag, quel que soit le nombre de passes.
    """

    _PROFILE_RANK: ClassVar[dict[str, int]] = {
        "starter": 0, "controlled": 1, "orchestrated": 2, "governed": 3, "production": 4,
    }

    @pytest.mark.parametrize(
        "bare_up_calls",
        [1, 3],
        ids=["un-bare-up", "trois-bare-up-consecutifs"],
    )
    def test_repeter_up_sans_needs_ne_fait_jamais_baisser_le_rang(
        self, tmp_path: Path, bare_up_calls: int
    ) -> None:
        target = tmp_path / "proj"
        setup = _grimoire(
            [
                "up", str(target), "--backend", "local", "--name", "invariant",
                "--needs", "multi-agent-orchestration,hooks-skills-governance,observability-cockpit",
            ],
            tmp_path,
        )
        assert setup.returncode == 0, setup.stdout + setup.stderr
        starting_rank = self._PROFILE_RANK[_standard_profile(target)["profile"]]

        for _ in range(bare_up_calls):
            result = _grimoire(["up", str(target)], tmp_path)
            assert result.returncode == 0, result.stdout + result.stderr
            current_rank = self._PROFILE_RANK[_standard_profile(target)["profile"]]
            assert current_rank >= starting_rank, (
                f"le rang du profil a baissé sans --needs explicite ({current_rank} < {starting_rank})"
            )


# ── Invariant 3 : une commande ne dépasse jamais son budget de première réponse ──


class TestUneCommandeRespecteSonBudgetDeTempsALaPremiereReponse:
    """Régression #340 (PR #343) : un registre pollué de chemins disparus
    faisait passer la première réponse de `cockpit serve`/`cockpit refresh`
    d'environ 1 s à ~70 s en usage réel. Aucun test existant ne mesurait un
    temps — `tests/test_cmd_cockpit.py::test_refresh_signale_les_chemins_morts_sans_toucher_au_registre`
    prouve le comportement fonctionnel (l'entrée est signalée, pas retirée)
    mais y arrive en mockant `subprocess.run`, ce qui élimine justement le
    coût que ce correctif règle. Celui-ci mesure le vrai temps d'un vrai
    sous-processus.
    """

    def test_cockpit_refresh_respecte_son_budget_avec_des_chemins_disparus(self, tmp_path: Path) -> None:
        cockpit_home = tmp_path / "cockpit-home"
        cockpit_home.mkdir()
        live_project = tmp_path / "vivant"
        (live_project / ".git").mkdir(parents=True)

        env = {"GRIMOIRE_COCKPIT_HOME": str(cockpit_home)}
        added = _grimoire(["cockpit", "add", str(live_project)], tmp_path, extra_env=env)
        assert added.returncode == 0, added.stdout + added.stderr

        registry_path = cockpit_home / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry.extend(
            {"name": f"fantome-{i}", "path": str(tmp_path / f"disparu-{i}"), "slug": f"fantome-{i}"}
            for i in range(_GHOST_COUNT)
        )
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        started = time.monotonic()
        refreshed = _grimoire(["cockpit", "refresh"], tmp_path, extra_env=env)
        elapsed = time.monotonic() - started

        assert refreshed.returncode == 0, refreshed.stdout + refreshed.stderr
        assert elapsed < _COCKPIT_REFRESH_BUDGET_SECONDS, (
            f"`grimoire cockpit refresh` a mis {elapsed:.1f}s pour {_GHOST_COUNT} chemins disparus "
            f"— budget {_COCKPIT_REFRESH_BUDGET_SECONDS}s (régression #340)"
        )
        # Effet sur le disque, pas seulement le chronomètre : les fantômes sont
        # signalés, jamais retirés d'office (#340, troisième commit de la PR #343).
        remaining = json.loads(registry_path.read_text(encoding="utf-8"))
        assert len(remaining) == _GHOST_COUNT + 1
