"""La suite de tests ne doit jamais écrire dans l'état utilisateur réel.

Régression : `grimoire init` enregistre le projet créé auprès du cockpit, et
les tests qui l'appelaient sans garde-fou ajoutaient une entrée au registre de
la machine. Constaté sur un poste de développement : 10 378 entrées mortes
pointant vers des répertoires pytest disparus, pour un seul projet réel.

Le correctif d'origine détournait une variable, `GRIMOIRE_COCKPIT_HOME`. La
mémoire transverse est arrivée ensuite avec sa propre racine machine,
`~/.grimoire/shared`, protégée test par test — le motif que la fixture existait
justement pour supprimer. L'isolation porte donc maintenant sur `HOME` : toute
racine dérivée de `Path.home()`, présente ou à venir, tombe dans le répertoire
temporaire. Ces tests vérifient les deux niveaux, la variable et la racine.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.memory import bundle, shared


class TestUserHomeIsolation:
    def test_home_is_redirected(self, real_home: Path) -> None:
        """La fixture autouse de tests/conftest.py doit être en vigueur ici."""
        assert Path.home().resolve() != real_home.resolve(), (
            "HOME n'est pas détourné — la suite écrirait dans le vrai répertoire personnel"
        )

    @pytest.mark.parametrize(
        "var", ["GRIMOIRE_COCKPIT_HOME", "GRIMOIRE_SHARED_HOME", "GRIMOIRE_EMBEDDING_CACHE"]
    )
    def test_each_state_variable_is_set(self, var: str) -> None:
        assert os.environ.get(var), f"{var} non posé — le kit écrirait hors du répertoire de test"

    @pytest.mark.parametrize(
        ("label", "resolver"),
        [
            ("registre cockpit", cmd_cockpit._registry_file),
            ("mémoire transverse", shared.shared_home),
            ("bundles d'embedding", bundle.default_install_root),
        ],
    )
    def test_every_state_root_falls_outside_the_user_home(
        self, label: str, resolver: Callable[[], Path], real_home: Path
    ) -> None:
        """Aucune racine d'état ne doit se résoudre sous le vrai répertoire personnel.

        C'est la propriété qui compte : elle tient pour les trois racines
        d'aujourd'hui, et une quatrième ajoutée demain la vérifie déjà tant
        qu'elle passe par ``Path.home()``.
        """
        resolved = resolver().resolve()
        assert real_home.resolve() not in resolved.parents, f"{label} pointe dans {real_home}"


class TestCockpitRegistry:
    def test_registering_never_touches_the_real_registry(
        self, tmp_path: Path, real_home: Path
    ) -> None:
        """Preuve de bout en bout : un enregistrement laisse le vrai fichier intact."""
        real = real_home / ".grimoire" / "cockpit" / "registry.json"
        before = real.read_bytes() if real.is_file() else None

        project = tmp_path / "demo"
        (project / "_grimoire").mkdir(parents=True)
        (project / "project-context.yaml").write_text(
            'project:\n  name: "demo"\nmemory:\n  backend: "local"\n', encoding="utf-8"
        )
        assert cmd_cockpit.register_project(project, "demo") is not None

        after = real.read_bytes() if real.is_file() else None
        assert after == before, "le registre utilisateur réel a été modifié par un test"

        # …et l'entrée a bien atterri dans le registre isolé.
        isolated = json.loads(cmd_cockpit._registry_file().read_text(encoding="utf-8"))
        assert any(entry["path"] == str(project.resolve()) for entry in isolated)


class TestSharedMemory:
    def test_promoting_never_touches_the_real_shared_store(self, real_home: Path) -> None:
        """La mémoire transverse écrit dans le store isolé, jamais dans le vrai.

        Le cas que la fixture d'origine ne couvrait pas : cette racine-là ne
        passait par aucun garde-fou de session.
        """
        real = real_home / ".grimoire" / "shared"
        before = sorted(p.name for p in real.iterdir()) if real.is_dir() else None

        store = shared.shared_home()
        store.mkdir(parents=True, exist_ok=True)
        (store / "témoin.json").write_text("{}", encoding="utf-8")

        after = sorted(p.name for p in real.iterdir()) if real.is_dir() else None
        assert after == before, "le store transverse réel a été modifié par un test"
        assert (store / "témoin.json").is_file()
