"""Le nuage de la page Mémoire montre de vrais embeddings, ou rien.

Il était tiré au sort — ``random.Random(42)`` autour de cinq centres, avec les
vrais noms de types dessus. Un nuage inventé se lit exactement comme un vrai.

Ces tests tiennent les deux bouts : l'ACP sépare réellement des groupes, et un
backend sans embedding répond « rien » plutôt que de dessiner quelque chose.
"""

from __future__ import annotations

import math
import random
import statistics
from pathlib import Path

import pytest

from grimoire.tools import memory_vectors as mv

# ── L'ACP fait ce qu'elle prétend ────────────────────────────────────────────


def _cluster(centre: list[float], n: int, spread: float, rng: random.Random) -> list[list[float]]:
    return [[c + rng.gauss(0, spread) for c in centre] for _ in range(n)]


def test_the_projection_separates_groups_that_are_really_separate() -> None:
    """Trois nuages disjoints en dimension 64 doivent rester disjoints en 2D."""
    rng = random.Random(7)
    dim = 64
    centres = [
        [10.0 if i == 0 else 0.0 for i in range(dim)],
        [0.0] * dim,
        [-10.0 if i == 1 else 0.0 for i in range(dim)],
    ]
    vectors: list[list[float]] = []
    labels: list[int] = []
    for group, centre in enumerate(centres):
        block = _cluster(centre, 20, 0.4, rng)
        vectors += block
        labels += [group] * len(block)

    points = mv.project_2d(vectors)
    assert len(points) == len(vectors)

    centroids = []
    for group in range(3):
        xs = [points[i][0] for i in range(len(points)) if labels[i] == group]
        ys = [points[i][1] for i in range(len(points)) if labels[i] == group]
        centroids.append((statistics.mean(xs), statistics.mean(ys)))
        assert statistics.pstdev(xs) < 1.0, "un groupe compact doit le rester"

    for a in range(3):
        for b in range(a + 1, 3):
            gap = math.dist(centroids[a], centroids[b])
            assert gap > 5.0, f"groupes {a} et {b} confondus après projection"


def test_the_projection_is_deterministic() -> None:
    """La page ne doit pas bouger d'une régénération à l'autre."""
    rng = random.Random(11)
    vectors = _cluster([1.0, 2.0, 3.0, 4.0], 30, 0.5, rng)
    assert mv.project_2d(vectors) == mv.project_2d(vectors)


def test_identical_vectors_collapse_without_blowing_up() -> None:
    """Variance nulle : pas de division par zéro, pas de NaN."""
    points = mv.project_2d([[1.0, 1.0, 1.0]] * 5)
    assert len(points) == 5
    assert all(abs(x) < 1e-6 and abs(y) < 1e-6 for x, y in points)


def test_heterogeneous_dimensions_are_refused() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        mv.project_2d([[1.0, 2.0], [1.0]])


def test_an_empty_input_projects_to_nothing() -> None:
    assert mv.project_2d([]) == []


# ── Ce qui n'a pas de vecteur n'en invente pas ───────────────────────────────


def test_a_project_without_configuration_yields_no_projection(tmp_path: Path) -> None:
    assert mv.collect(tmp_path) == []
    assert mv.projection(tmp_path) is None


def test_a_backend_without_embeddings_answers_empty() -> None:
    """Le contrat par défaut : un backend lexical ou fichier n'a rien à rendre."""
    from grimoire.memory.backends.base import MemoryBackend

    assert MemoryBackend.vectors(object(), limit=10) == []  # type: ignore[arg-type]


def test_a_single_vector_is_not_a_cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une ACP sur un point ne veut rien dire — mieux vaut ne rien montrer."""
    monkeypatch.setattr(mv, "collect", lambda *_a, **_k: [("a", [1.0, 2.0])])
    assert mv.projection(tmp_path) is None


def test_a_real_cloud_is_reported_as_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(5)
    pairs = [(f"id-{i}", v) for i, v in enumerate(_cluster([1.0, 0.0, 0.0], 6, 0.2, rng))]
    monkeypatch.setattr(mv, "collect", lambda *_a, **_k: pairs)

    out = mv.projection(tmp_path, types_by_id={"id-0": "decision"})
    assert out is not None
    assert out["is_demo"] is False, "cette projection n'est pas une démonstration"
    assert out["sampled"] == 6
    assert out["dimensions"] == 3
    assert out["points"][0]["type"] == "decision"
    assert out["points"][1]["type"] == "memory", "type inconnu = type de base, pas d'invention"


# ── Le vrai client Qdrant, pas une doublure ──────────────────────────────────


def test_vectors_are_read_back_from_a_real_qdrant_store(tmp_path: Path) -> None:
    """Mode local de ``qdrant-client`` : vrai stockage, vraie API ``scroll``.

    Une doublure de client ne prouverait que la forme qu'on lui a donnée ; ici
    c'est le moteur qui répond, et c'est lui qui décide si ``with_vectors``
    rend une liste ou un dictionnaire de vecteurs nommés.
    """
    qdrant_client = pytest.importorskip("qdrant_client")
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from grimoire.memory.backends.qdrant import QdrantBackend

    client = qdrant_client.QdrantClient(path=str(tmp_path / "store"))
    client.create_collection("mem", vectors_config=VectorParams(size=8, distance=Distance.COSINE))
    rng = random.Random(3)
    points, labels = [], {}
    for group, base in enumerate(([1.0] + [0.0] * 7, [0.0, 1.0] + [0.0] * 6)):
        for k in range(12):
            pid = group * 12 + k
            points.append(PointStruct(
                id=pid,
                vector=[b + rng.gauss(0, 0.03) for b in base],
                payload={"text": "x"},
            ))
            labels[str(pid)] = group
    client.upsert("mem", points=points)

    backend = QdrantBackend.__new__(QdrantBackend)
    backend._client = client
    backend._collection = "mem"

    pairs = backend.vectors(limit=100)
    assert len(pairs) == 24
    assert len(pairs[0][1]) == 8

    coords = mv.project_2d([v for _, v in pairs])
    means = [
        statistics.mean([coords[i][0] for i, (pid, _) in enumerate(pairs) if labels[pid] == g])
        for g in (0, 1)
    ]
    assert abs(means[0] - means[1]) > 1.0, "les deux groupes doivent se séparer"
    client.close()
