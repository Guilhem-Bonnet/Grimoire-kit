"""Savoir › Carte 3D (P81) : le graphe complet du projet, en format compact.

Une route en lecture seule, ``GET /api/workspace/memory/graph``, rend **tous**
les nœuds et tous les liens — souvenirs, graphe de code, tâches — sans
échantillonnage. Le format est colonne par colonne : des ids entiers (l'indice
du nœud), des liens en paires d'indices, des tableaux typés encodés en base64
(``Int32``/``Uint16``/``Uint8``/``Float32`` petit-boutiste). Un graphe de
27 k nœuds et 110 k liens tient ainsi dans quelques Mo, sans un objet JSON par
nœud.

La disposition initiale est calculée ici, une fois, puis gardée sur disque par
empreinte du graphe (``_grimoire-output/graph-layout/<empreinte>.npz``) : une
seconde lecture sans changement ne recalcule rien. Elle part des communautés
(propagation d'étiquettes, vectorisée) posées sur une sphère ; l'affinage par
forces (Barnes-Hut) tourne ensuite dans un Web Worker côté navigateur.

Sources, chacune nommée dans ``sources`` :

- souvenirs : le store de la mémoire du projet (entrées visibles, ni
  rétractées) ; liens ``remplace`` (supersession) et ``mémoire pour`` (tâche) ;
- code : ``_grimoire-output/codegraph.json`` si présent (nœuds et arêtes de
  ``CodeGraph``), sinon l'index du dossier ``src`` du projet ;
- tâches : le Mission Ledger ; liens de dépendance.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from grimoire.core.fsio import atomic_write

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["GRAPH_SCHEMA_VERSION", "LAYOUT_STATS", "graph_view"]

GRAPH_SCHEMA_VERSION = 1

#: Natures de nœud, dans l'ordre de ``kind`` (Uint8).
KINDS = ("souvenir", "code", "tache")
#: Types de lien, dans l'ordre de ``etype`` (Uint8).
EDGE_TYPES = (
    "remplace",
    "contredit",
    "memoire-pour",
    "depend",
    "imports",
    "calls",
    "defines",
    "inherits",
    "references",
    "tested_by",
    "overrides",
)
_EDGE_INDEX = {name: i for i, name in enumerate(EDGE_TYPES)}

LAYOUT_DIR = Path("_grimoire-output") / "graph-layout"
CODEGRAPH_SNAPSHOT = Path("_grimoire-output") / "codegraph.json"

#: Compteurs observables par les tests : ``computed`` ne bouge pas à une relecture inchangée.
LAYOUT_STATS = {"computed": 0, "reused": 0}
_LOCK = threading.Lock()
_SCAN_BATCH = 2000


def _b64(array: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode("ascii")


# ── Collecte ────────────────────────────────────────────────────────────────


class _Builder:
    """Accumule nœuds et liens ; les ids de source deviennent des indices."""

    def __init__(self) -> None:
        self.index: dict[str, int] = {}
        self.kind: list[int] = []
        self.label: list[str] = []
        self.group: list[str] = []
        self.ref: list[str] = []
        self.src: list[int] = []
        self.dst: list[int] = []
        self.etype: list[int] = []

    def node(self, key: str, kind: str, label: str, group: str, ref: str) -> int:
        found = self.index.get(key)
        if found is not None:
            return found
        i = len(self.kind)
        self.index[key] = i
        self.kind.append(KINDS.index(kind))
        self.label.append(label)
        self.group.append(group)
        self.ref.append(ref)
        return i

    def edge(self, a: str, b: str, etype: str) -> None:
        ia, ib = self.index.get(a), self.index.get(b)
        if ia is None or ib is None or ia == ib:
            return
        self.src.append(ia)
        self.dst.append(ib)
        self.etype.append(_EDGE_INDEX[etype])


def _collect_memory(project_root: Path, out: _Builder, sources: dict[str, Any]) -> list[tuple[str, str, str]]:
    from grimoire.memory.supersession import SUPERSEDED_KEY, is_retracted
    from grimoire.tools import workspace_knowledge as wk

    cfg, mgr, reason = wk.open_readonly(project_root)
    if mgr is None:
        sources["souvenirs"] = {"count": 0, "reason": reason}
        return []
    owners = wk.ownership(cfg, project_root)
    pending: list[tuple[str, str, str]] = []
    cursor = 0
    try:
        while True:
            batch = mgr.backend.get_all(offset=cursor, limit=_SCAN_BATCH)
            for entry in batch:
                if owners.visibility(entry) != "own" or is_retracted(entry):
                    continue
                _, type_ = wk.classify(entry)
                title = wk._split_text(entry.text)[0] or entry.id
                out.node(f"m:{entry.id}", "souvenir", title, type_, entry.id)
                meta = entry.metadata or {}
                newer = str(meta.get(SUPERSEDED_KEY) or "").strip()
                if newer:
                    pending.append((f"m:{newer}", f"m:{entry.id}", "remplace"))
                for other in _as_list(meta.get("contradicts")):
                    pending.append((f"m:{entry.id}", f"m:{other}", "contredit"))
                if entry.task_ref:
                    pending.append((f"m:{entry.id}", f"t:{entry.task_ref}", "memoire-pour"))
            cursor += len(batch)
            if len(batch) < _SCAN_BATCH:
                break
    except Exception as exc:
        sources["souvenirs"] = {"count": len(out.kind), "reason": f"lecture du store impossible : {exc}"}
        return pending
    sources["souvenirs"] = {"count": len(out.kind), "reason": None}
    return pending


def _as_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x]
    return []


def _collect_tasks(project_root: Path, out: _Builder, sources: dict[str, Any]) -> list[tuple[str, str, str]]:
    from grimoire.missions.board import board_status_of
    from grimoire.missions.service import TaskService

    before = len(out.kind)
    links: list[tuple[str, str, str]] = []
    try:
        service = TaskService(project_root.resolve())
        if not service.has_ledger:
            sources["taches"] = {"count": 0, "reason": "aucun Mission Ledger"}
            return links
        for task in service.list_tasks(None, None):
            out.node(f"t:{task.id}", "tache", f"{task.id} — {task.title}", board_status_of(task.status), task.id)
            for dep in task.dependencies:
                links.append((f"t:{task.id}", f"t:{dep.target}", "depend"))
    except Exception as exc:
        sources["taches"] = {"count": len(out.kind) - before, "reason": f"ledger illisible : {exc}"}
        return links
    sources["taches"] = {"count": len(out.kind) - before, "reason": None}
    return links


def _code_payload(project_root: Path) -> tuple[Iterable[dict[str, Any]], Iterable[dict[str, Any]], str | None]:
    snapshot = project_root / CODEGRAPH_SNAPSHOT
    if snapshot.is_file():
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        return data.get("nodes", []), data.get("edges", []), None
    src = project_root / "src"
    if not src.is_dir():
        return [], [], "aucun graphe de code (ni instantané, ni dossier src)"
    from grimoire.memory.projections import build_code_graph

    graph = build_code_graph(project_root, [src])
    return [n.to_dict() for n in graph.nodes], [e.to_dict() for e in graph.edges], None


def _collect_code(project_root: Path, out: _Builder, sources: dict[str, Any]) -> list[tuple[str, str, str]]:
    before = len(out.kind)
    links: list[tuple[str, str, str]] = []
    try:
        nodes, edges, reason = _code_payload(project_root)
        for n in nodes:
            parts = str(n.get("file_path", "")).replace("\\", "/").split("/")
            group = "/".join(parts[:2]) if len(parts) > 1 else (parts[0] or "code")
            out.node(f"c:{n['id']}", "code", str(n.get("name") or n["id"]), group, str(n["id"]))
        for e in edges:
            kind = str(e.get("kind", "references"))
            if kind in _EDGE_INDEX:
                links.append((f"c:{e['from_node']}", f"c:{e['to_node']}", kind))
    except Exception as exc:
        sources["code"] = {"count": len(out.kind) - before, "reason": f"graphe de code illisible : {exc}"}
        return links
    sources["code"] = {"count": len(out.kind) - before, "reason": reason}
    return links


# ── Disposition initiale ────────────────────────────────────────────────────


def communities(n: int, src: np.ndarray, dst: np.ndarray, rounds: int = 8) -> np.ndarray:
    """Propagation d'étiquettes vectorisée : chaque nœud prend l'étiquette la plus fréquente de ses voisins."""
    label = np.arange(n, dtype=np.int64)
    if src.size == 0:
        return label
    u = np.concatenate([src, dst]).astype(np.int64)
    v = np.concatenate([dst, src]).astype(np.int64)
    for _ in range(rounds):
        key = u * n + label[v]
        uniq, counts = np.unique(key, return_counts=True)
        node, lab = uniq // n, uniq % n
        order = np.lexsort((-lab, -counts, node))  # par nœud, puis fréquence décroissante
        node, lab = node[order], lab[order]
        first = np.concatenate([[True], node[1:] != node[:-1]])
        new = label.copy()
        new[node[first]] = lab[first]
        if np.array_equal(new, label):
            break
        label = new
    _, dense = np.unique(label, return_inverse=True)
    return dense.astype(np.int64)


def layout(n: int, src: np.ndarray, dst: np.ndarray, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """``(positions Nx3 Float32, communauté N Uint16)`` — communautés sur une sphère, nœuds autour."""
    rng = np.random.default_rng(seed)
    comm = communities(n, src, dst)
    count = int(comm.max()) + 1 if n else 0
    sizes = np.bincount(comm, minlength=count)
    # Les communautés les plus grandes d'abord ; au-delà de 4096 le reste partage la dernière couleur.
    rank = np.argsort(-sizes, kind="stable")
    where = np.empty(count, dtype=np.int64)
    where[rank] = np.arange(count)
    comm = np.minimum(where[comm], 4095)
    k = np.arange(count, dtype=np.float64) + 0.5
    phi = np.arccos(1 - 2 * k / max(count, 1))
    theta = np.pi * (1 + 5**0.5) * k
    big = 60.0 * max(n, 1) ** (1 / 3)
    centers = big * np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)
    radius = 3.0 * np.cbrt(sizes[rank].astype(np.float64))
    return _place(n, comm, count, centers, radius, rng), comm.astype(np.uint16)


def _place(
    n: int, comm: np.ndarray, count: int, centers: np.ndarray, radius: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    c = np.minimum(comm, count - 1)
    spread = radius[c][:, None]
    placed: np.ndarray = (centers[c] + rng.normal(size=(n, 3)) * spread).astype(np.float32)
    return placed


def _fingerprint(out: _Builder, src: np.ndarray, dst: np.ndarray) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(sorted(out.index), separators=(",", ":")).encode())
    order = np.lexsort((np.asarray(out.etype), dst, src)) if src.size else np.array([], dtype=np.int64)
    h.update(src[order].tobytes())
    h.update(dst[order].tobytes())
    h.update(np.asarray(out.etype, dtype=np.uint8)[order].tobytes())
    return h.hexdigest()[:20]


def _layout_cached(
    project_root: Path, fp: str, n: int, src: np.ndarray, dst: np.ndarray
) -> tuple[np.ndarray, np.ndarray, bool]:
    folder = project_root / LAYOUT_DIR
    path = folder / f"{fp}.npz"
    with _LOCK:
        if path.is_file():
            try:
                with np.load(path) as data:
                    pos, comm = data["pos"], data["comm"]
                if pos.shape == (n, 3):
                    LAYOUT_STATS["reused"] += 1
                    return pos, comm, True
            except (OSError, ValueError, KeyError):
                pass
        pos, comm = layout(n, src, dst)
        LAYOUT_STATS["computed"] += 1
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("*.npz"):
                old.unlink(missing_ok=True)
            buffer = io.BytesIO()
            np.savez(buffer, pos=pos, comm=comm)
            atomic_write(path, buffer.getvalue())
        except OSError:
            pass  # lecture seule : la disposition est rendue sans être gardée
        return pos, comm, False


# ── Route ───────────────────────────────────────────────────────────────────


def graph_view(project_root: Path) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/graph`` — tout le graphe, colonne par colonne."""
    out = _Builder()
    sources: dict[str, Any] = {}
    memory_links = _collect_memory(project_root, out, sources)
    links = _collect_tasks(project_root, out, sources) + _collect_code(project_root, out, sources)
    for a, b, t in memory_links + links:
        out.edge(a, b, t)
    n = len(out.kind)
    src = np.asarray(out.src, dtype=np.int32)
    dst = np.asarray(out.dst, dtype=np.int32)
    fp = _fingerprint(out, src, dst)
    pos, comm, reused = _layout_cached(project_root, fp, n, src, dst)
    groups = sorted(set(out.group))
    gindex = {g: i for i, g in enumerate(groups)}
    return {
        "schemaVersion": GRAPH_SCHEMA_VERSION,
        "fingerprint": fp,
        "counts": {"nodes": n, "edges": int(src.size)},
        "kinds": list(KINDS),
        "edgeTypes": list(EDGE_TYPES),
        "groups": groups,
        "sources": sources,
        "layout": {"reused": reused, "communities": int(comm.max()) + 1 if n else 0, "positions": _b64(pos, "<f4")},
        "nodes": {
            "kind": _b64(np.asarray(out.kind), "u1"),
            "group": _b64(np.asarray([gindex[g] for g in out.group]), "<u2"),
            "community": _b64(comm, "<u2"),
            "label": out.label,
            "ref": out.ref,
        },
        "edges": {"src": _b64(src, "<i4"), "dst": _b64(dst, "<i4"), "type": _b64(np.asarray(out.etype), "u1")},
    }
