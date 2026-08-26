"""La suite de tests ne doit jamais écrire dans l'état utilisateur réel.

Régression : `grimoire init` enregistre le projet créé auprès du cockpit, et
les tests qui l'appelaient sans garde-fou ajoutaient une entrée au registre de
la machine. Constaté sur un poste de développement : 10 378 entrées mortes
pointant vers des répertoires pytest disparus, pour un seul projet réel.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from grimoire.cli import cmd_cockpit


class TestCockpitHomeIsolation:
    def test_env_override_is_active(self) -> None:
        """La fixture autouse de tests/conftest.py doit être en vigueur ici."""
        assert os.environ.get("GRIMOIRE_COCKPIT_HOME"), (
            "GRIMOIRE_COCKPIT_HOME non posé — la suite écrirait dans le vrai $HOME"
        )

    def test_registry_resolves_outside_the_user_home(self) -> None:
        registry = cmd_cockpit._registry_file().resolve()
        real = (Path.home() / ".grimoire" / "cockpit").resolve()
        assert real not in registry.parents, f"le registre pointe dans {real}"

    def test_registering_never_touches_the_real_registry(self, tmp_path: Path) -> None:
        """Preuve de bout en bout : un enregistrement laisse le vrai fichier intact."""
        real = Path.home() / ".grimoire" / "cockpit" / "registry.json"
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
