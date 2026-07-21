"""Tests de l'algèbre des primitives de node (P0.3)."""

from __future__ import annotations

from pathlib import Path

from grimoire.tools import blueprint_primitives as bp
from grimoire.tools.forge_server import ForgeAPI


class TestPrimitives:
    def test_seven_orthogonal_primitives(self) -> None:
        assert set(bp.PRIMITIVES) == {
            "Unit",
            "Route",
            "Scatter",
            "Gather",
            "Gate",
            "Boundary",
            "Reference",
        }
        # Unit est la seule primitive « qui fait ».
        doers = [name for name, p in bp.PRIMITIVES.items() if p["doesWork"]]
        assert doers == ["Unit"]

    def test_every_xxl_case_maps_to_a_primitive(self) -> None:
        # Preuve P0.3 : chaque case XXL renvoie à l'une des 7 primitives.
        assert bp.XXL_MAPPING
        for case, entry in bp.XXL_MAPPING.items():
            assert entry["primitive"] in bp.PRIMITIVES, case

    def test_role_validation(self) -> None:
        assert bp.is_valid_role(None) is True  # absent == valide (additif)
        assert bp.is_valid_role("Gate") is True
        assert bp.is_valid_role("Widget") is False

    def test_catalogue_payload(self) -> None:
        cat = bp.primitives_catalogue()
        assert cat["schemaVersion"] == bp.PRIMITIVES_SCHEMA_VERSION
        assert set(cat["primitives"]) == set(bp.PRIMITIVES)
        assert cat["xxlMapping"] == bp.XXL_MAPPING

    def test_endpoint(self, tmp_path: Path) -> None:
        api = ForgeAPI(tmp_path, tmp_path, None)
        payload = api.primitives_view()
        assert set(payload["primitives"]) == set(bp.PRIMITIVES)

    def test_invalid_role_rejected_by_validate(self, tmp_path: Path) -> None:
        api = ForgeAPI(tmp_path, tmp_path, None)
        bp_doc = {
            "blueprintVersion": 1,
            "nodes": [
                {
                    "id": "a",
                    "kind": "pattern",
                    "ref": "ORC-01",
                    "role": "Widget",
                    "pins": [{"id": "out", "direction": "out", "contract": "x"}],
                }
            ],
            "edges": [],
        }
        errors = api.blueprint_validate(bp_doc)
        assert any("role invalide" in e for e in errors)

    def test_valid_role_accepted_by_validate(self, tmp_path: Path) -> None:
        api = ForgeAPI(tmp_path, tmp_path, None)
        bp_doc = {
            "blueprintVersion": 1,
            "nodes": [
                {
                    "id": "a",
                    "kind": "pattern",
                    "ref": "ORC-01",
                    "role": "Gate",
                    "pins": [{"id": "out", "direction": "out", "contract": "x"}],
                }
            ],
            "edges": [],
        }
        assert not any("role invalide" in e for e in api.blueprint_validate(bp_doc))
