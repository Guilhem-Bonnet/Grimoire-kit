"""Fixture de graphe de code pour les e2e P81 (copie de celle des tests unitaires : ``tests`` n'est pas importable sous ``tests/e2e``)."""

from __future__ import annotations

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
            "id": f"pkg{i // 400}.mod{i // 40}:sym{i}",
            "kind": kinds[i % 3],
            "name": f"sym{i}",
            "file_path": f"src/pkg{i // 400}/mod{i // 40}.py",
            "line_start": i,
            "line_end": i + 3,
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
