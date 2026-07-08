"""Parité entre les deux implémentations de stigmergy.

Il en existe deux volontairement :

- ``grimoire.tools.stigmergy`` — bibliothèque du paquet (SDK + classe).
- ``framework/tools/stigmergy.py`` — script **autonome** (stdlib seule)
  embarqué dans les projets, exécutable sans le paquet installé.

Elles partagent le même tableau (``_grimoire-output/pheromone-board.json``).
Ce test garantit qu'elles ne dérivent pas silencieusement : mêmes constantes
de comportement, et format de board compatible dans les deux sens.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from grimoire.tools import stigmergy as sdk


def _load_framework_module() -> ModuleType:
    """Charge le script autonome framework/tools/stigmergy.py par chemin."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "framework" / "tools" / "stigmergy.py"
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("stigmergy_standalone", candidate)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            # Enregistré avant exec : les @dataclass du module résolvent
            # cls.__module__ via sys.modules pendant le chargement.
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    pytest.skip("framework/tools/stigmergy.py introuvable (paquet sans framework)")


@pytest.fixture(scope="module")
def fw() -> ModuleType:
    return _load_framework_module()


class TestStigmergyParity:
    def test_constants_match(self, fw: ModuleType) -> None:
        assert set(sdk.VALID_TYPES) == set(fw.VALID_TYPES)
        assert sdk.DEFAULT_HALF_LIFE_HOURS == fw.DEFAULT_HALF_LIFE_HOURS
        assert sdk.DETECTION_THRESHOLD == fw.DETECTION_THRESHOLD
        assert sdk.MAX_INTENSITY == fw.MAX_INTENSITY
        assert sdk.DEFAULT_INTENSITY == fw.DEFAULT_INTENSITY
        assert sdk.PHEROMONE_FILE == fw.PHEROMONE_FILE

    def test_board_path_match(self, fw: ModuleType, tmp_path: Path) -> None:
        assert sdk._board_path(tmp_path) == fw._board_path(tmp_path)

    def test_framework_board_read_by_sdk(self, fw: ModuleType, tmp_path: Path) -> None:
        """Un board écrit par le script autonome est lisible par le SDK,
        avec le même ensemble de signaux actifs."""
        board_fw = fw.load_board(tmp_path)
        fw.emit_pheromone(board_fw, ptype="ALERT", location="src/db",
                          text="breaking change", emitter="architect")
        fw.emit_pheromone(board_fw, ptype="NEED", location="src/auth",
                          text="review", emitter="dev")
        fw.save_board(tmp_path, board_fw)

        board_sdk = sdk.load_board(tmp_path)
        active_sdk = {p.pheromone_id for p, _ in sdk.sense_pheromones(board_sdk)}
        active_fw = {p.pheromone_id for p, _ in fw.sense_pheromones(board_fw)}
        assert active_sdk == active_fw
        assert len(active_sdk) == 2

    def test_sdk_board_read_by_framework(self, fw: ModuleType, tmp_path: Path) -> None:
        """Réciproque : un board écrit par le SDK est lisible par le script."""
        board_sdk = sdk.load_board(tmp_path)
        sdk.emit_pheromone(board_sdk, ptype="PROGRESS", location="src/api",
                           text="wip", emitter="dev")
        sdk.save_board(tmp_path, board_sdk)

        board_fw = fw.load_board(tmp_path)
        assert len(board_fw.pheromones) == 1
        assert board_fw.pheromones[0].pheromone_type == "PROGRESS"

    def test_decay_agrees(self, fw: ModuleType) -> None:
        """La décroissance (demi-vie) donne la même intensité pour un même âge."""
        from datetime import UTC, datetime, timedelta

        now = datetime(2026, 1, 2, tzinfo=UTC)
        emitted = (now - timedelta(hours=72)).isoformat()  # une demi-vie

        p_sdk = sdk.Pheromone(pheromone_id="PH-x", pheromone_type="NEED",
                              location="l", text="t", emitter="e",
                              timestamp=emitted, intensity=1.0)
        p_fw = fw.Pheromone(pheromone_id="PH-x", pheromone_type="NEED",
                            location="l", text="t", emitter="e",
                            timestamp=emitted, intensity=1.0)
        i_sdk = sdk.compute_intensity(p_sdk, sdk.DEFAULT_HALF_LIFE_HOURS, now)
        i_fw = fw.compute_current_intensity(p_fw, fw.DEFAULT_HALF_LIFE_HOURS, now)
        assert i_sdk == pytest.approx(i_fw, abs=1e-9)
        assert i_sdk == pytest.approx(0.5, abs=1e-9)
