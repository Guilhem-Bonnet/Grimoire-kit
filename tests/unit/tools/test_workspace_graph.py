"""Savoir › Carte 3D (P81) : la route du graphe rend tout, en colonnes, et garde sa disposition."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np

from grimoire.tools import workspace_graph


def write_codegraph(root: Path, nodes: int, edges: int, seed: int = 3) -> None:
    """Un instantané de graphe de code à la taille demandée, en grappes de 40 modules."""
    rng = np.random.default_rng(seed)
    kinds = ("function", "method", "class")
    rows = [
        {
            "id": f"pkg{i // 400}.mod{i // 40}:sym{i}", "kind": kinds[i % 3], "name": f"sym{i}",
            "file_path": f"src/pkg{i // 400}/mod{i // 40}.py", "line_start": i, "line_end": i + 3,
        }
        for i in range(nodes)
    ]
    base = rng.integers(0, nodes, size=edges)
    near = np.clip(base + rng.integers(-60, 60, size=edges), 0, nodes - 1)
    kinds_e = ("calls", "imports", "references", "tested_by")
    links = [
        {"from_node": rows[a]["id"], "to_node": rows[b]["id"], "kind": kinds_e[k % 4]}
        for k, (a, b) in enumerate(zip(base.tolist(), near.tolist(), strict=True))
    ]
    target = root / workspace_graph.CODEGRAPH_SNAPSHOT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"nodes": rows, "edges": links}), encoding="utf-8")


def column(payload: str, dtype: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(payload), dtype=dtype)


def test_la_route_rend_tous_les_noeuds_et_liens_en_colonnes(tmp_path: Path) -> None:
    write_codegraph(tmp_path, 3000, 9000)
    view = workspace_graph.graph_view(tmp_path)

    n = view["counts"]["nodes"]
    assert n == 3000
    assert len(view["nodes"]["label"]) == n and len(view["nodes"]["ref"]) == n
    assert column(view["nodes"]["kind"], "u1").size == n
    assert column(view["nodes"]["community"], "<u2").size == n
    assert column(view["layout"]["positions"], "<f4").size == 3 * n
    src, dst = column(view["edges"]["src"], "<i4"), column(view["edges"]["dst"], "<i4")
    assert src.size == dst.size == view["counts"]["edges"] > 8000, "liens en paires d'indices"
    assert int(src.max()) < n and int(dst.max()) < n and int(src.min()) >= 0
    assert view["sources"]["code"]["count"] == 3000


def test_une_seconde_lecture_reutilise_la_disposition_gardee(tmp_path: Path) -> None:
    write_codegraph(tmp_path, 1500, 4000)
    before = dict(workspace_graph.LAYOUT_STATS)
    first = workspace_graph.graph_view(tmp_path)
    second = workspace_graph.graph_view(tmp_path)

    assert first["fingerprint"] == second["fingerprint"]
    assert first["layout"]["reused"] is False and second["layout"]["reused"] is True
    assert workspace_graph.LAYOUT_STATS["computed"] == before["computed"] + 1
    assert workspace_graph.LAYOUT_STATS["reused"] == before["reused"] + 1
    assert first["layout"]["positions"] == second["layout"]["positions"]

    write_codegraph(tmp_path, 1500, 4001)
    third = workspace_graph.graph_view(tmp_path)
    assert third["fingerprint"] != first["fingerprint"] and third["layout"]["reused"] is False
    assert len(list((tmp_path / workspace_graph.LAYOUT_DIR).glob("*.npz"))) == 1, "les anciennes dispositions sont purgées"


def test_un_projet_vide_rend_un_graphe_vide_avec_ses_motifs(tmp_path: Path) -> None:
    view = workspace_graph.graph_view(tmp_path)
    assert view["counts"] == {"nodes": 0, "edges": 0}
    assert view["sources"]["code"]["reason"]


def test_les_communautes_regroupent_les_grappes(tmp_path: Path) -> None:
    src = np.array([0, 1, 2, 3, 4, 5], dtype=np.int32)
    dst = np.array([1, 2, 0, 4, 5, 3], dtype=np.int32)
    comm = workspace_graph.communities(6, src, dst)
    assert comm[0] == comm[1] == comm[2] and comm[3] == comm[4] == comm[5] and comm[0] != comm[3]


def test_la_route_est_enregistree() -> None:
    from grimoire.tools import workspace_routes

    assert "/api/workspace/memory/graph" in workspace_routes.GET_ROUTES
