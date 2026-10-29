"""P23 — retirer sans effacer, rétablir, réétiqueter ; filtrés à la lecture comme ``superseded_by``.

Le prédicat ``retracted`` (qui, quand, pourquoi) est posé par ``MemoryManager``,
journalisé dans ``memory-changes.jsonl`` (P14), lu par toutes les lectures
servies (recherche, hybride, taxonomie, rappel typé, ``get_all``) et s'annule
sans rien perdre. Les gestes échouent fermé : backend sans ``update``, relecture
sans la marque, souvenir d'un autre projet.

Chaque test tourne sur chaque backend local disponible : ``local`` (JSON),
``lexical`` (SQLite FTS5), ``tantivy-local``, ``mempalace`` (ChromaDB persistant,
lecture par identifiant seulement : sa recherche télécharge un modèle) et Qdrant
embarqué (``:memory:``, vecteurs déterministes sans modèle).
"""

from __future__ import annotations

import functools
import hashlib
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.gestures import entry_state, run_gesture
from grimoire.memory.journal import JOURNAL_RELPATH
from grimoire.memory.manager import MemoryManager
from grimoire.memory.supersession import INCLUDE_ALL, RETRACTED_KEY, is_retracted

QUERY = "décision backend vectoriel"
TEXT_A = "Décision : le backend vectoriel retenu est Qdrant."
TEXT_B = "Décision : le backend vectoriel de secours est Weaviate."


@functools.cache
def _chroma_native_refusal() -> str:
    """Pourquoi l'index natif de chromadb (hnswlib) ne se charge pas ici ; ``""`` s'il se charge.

    Constat du 2026-10-28 (CI, Python 3.13) : sur certaines machines GitHub, l'import de ``local_hnsw`` meurt d'une
    instruction illégale (SIGILL) ; le worker xdist tué, le job restait suspendu jusqu'à son plafond. L'import est
    donc essayé une fois dans un sous-processus : seul lui meurt.
    """
    probe = subprocess.run([sys.executable, "-c", "import chromadb.segment.impl.vector.local_hnsw"],
                           capture_output=True, text=True, timeout=120, check=False)
    if probe.returncode == 0:
        return ""
    if probe.returncode == -signal.SIGILL:
        return "chromadb : l'index natif (hnswlib) meurt d'une instruction illégale sur ce processeur (SIGILL)"
    return f"chromadb : l'index natif ne se charge pas (code {probe.returncode}) : {probe.stderr.strip()[-200:]}"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    # Rien ne sort du test : ni le HOME réel, ni la télémétrie de ChromaDB.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "False")


def _offline_chroma_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le plongement par défaut de ChromaDB télécharge un modèle : on lui substitue un hachage."""
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    def fake(self: object, input: list[str]) -> list[list[float]]:
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()] * 12 for t in input]  # 32 * 12 = 384

    monkeypatch.setattr(ONNXMiniLM_L6_V2, "__call__", fake)


def _make_qdrant(tmp_path: Path) -> MemoryBackend:
    pytest.importorskip("qdrant_client")
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    from grimoire.memory.backends._qdrant_mixin import QdrantStorageMixin

    class _EmbeddedQdrant(QdrantStorageMixin, MemoryBackend):
        def __init__(self) -> None:
            self._collection = "retraction-tests"
            self._client = QdrantClient(":memory:")
            self._client.create_collection(
                collection_name=self._collection, vectors_config=VectorParams(size=8, distance=Distance.COSINE),
            )

        def _embed(self, text: str) -> list[float]:
            digest = hashlib.sha256(text.encode()).digest()
            return [b / 255 for b in digest[:8]]

        def health_check(self) -> BackendStatus:
            return BackendStatus(backend="qdrant-test", healthy=True, entries=self.count())

    return _EmbeddedQdrant()


def _make_backend(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None) -> MemoryBackend:
    if kind == "local":
        return LocalMemoryBackend(tmp_path / "mem.json")
    if kind == "lexical":
        from grimoire.memory.backends.lexical import LexicalMemoryBackend

        return LexicalMemoryBackend(tmp_path / "mem.sqlite3")
    if kind == "tantivy":
        pytest.importorskip("tantivy")
        from grimoire.memory.backends.tantivy_local import TantivyMemoryBackend

        return TantivyMemoryBackend(tmp_path / "tantivy")
    if kind == "mempalace":
        pytest.importorskip("chromadb")
        if refusal := _chroma_native_refusal():
            pytest.skip(refusal)
        from grimoire.memory.backends.mempalace import MemPalaceBackend

        assert monkeypatch is not None
        _offline_chroma_embeddings(monkeypatch)

        return MemPalaceBackend(palace_path=str(tmp_path / "palace"))
    return _make_qdrant(tmp_path)


#: ``mempalace`` n'a pas de recherche sans modèle d'embedding téléchargé : on n'y teste que la lecture par id.
_SEARCHABLE = ("local", "lexical", "tantivy", "qdrant")
_ALL = (*_SEARCHABLE, "mempalace")


@pytest.fixture(params=_ALL)
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def mgr(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryManager:
    return MemoryManager(_make_backend(kind, tmp_path, monkeypatch), project_name="demo", project_root=tmp_path)


def _journal(root: Path) -> list[dict[str, Any]]:
    path = root / JOURNAL_RELPATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _seed(mgr: MemoryManager) -> tuple[str, str]:
    a = mgr.store(TEXT_A, user_id="u", tags=("adr",)).id
    b = mgr.store(TEXT_B, user_id="u", tags=("adr",)).id
    return a, b


def _ids(entries: list[MemoryEntry]) -> set[str]:
    return {e.id for e in entries}


class TestRetractionPredicate:
    def test_retract_marks_who_when_why_and_keeps_the_memory(self, mgr: MemoryManager) -> None:
        a, _ = _seed(mgr)
        got = mgr.retract(a, reason="décision annulée par le comité", actor="alice", session_id="s-1")
        assert got is not None
        mark = (mgr.recall(a) or got).metadata[RETRACTED_KEY]
        assert mark["reason"] == "décision annulée par le comité"
        assert mark["by"] == "alice"
        assert mark["session_id"] == "s-1"
        assert mark["at"]
        # Retirer n'efface pas : texte, étiquettes et provenance d'origine restent lisibles.
        kept = mgr.recall(a)
        assert kept is not None
        assert kept.text == TEXT_A
        assert "adr" in kept.tags
        assert is_retracted(kept)
        assert entry_state(kept, mgr.recall) == "retracted"
        assert mgr.backend.count() == 2

    def test_unretract_clears_the_mark(self, mgr: MemoryManager) -> None:
        a, _ = _seed(mgr)
        mgr.retract(a, reason="erreur")
        mgr.unretract(a, reason="finalement valable")
        back = mgr.recall(a)
        assert back is not None
        assert not is_retracted(back)
        assert entry_state(back, mgr.recall) == "live"

    def test_unknown_id_is_none_not_a_fake_success(self, mgr: MemoryManager) -> None:
        assert mgr.retract("absent", reason="x") is None
        assert mgr.unretract("absent") is None
        assert mgr.update_tags("absent", add=["x"]) is None

    def test_a_reason_is_mandatory(self, mgr: MemoryManager) -> None:
        a, _ = _seed(mgr)
        with pytest.raises(GrimoireMemoryError, match="raison"):
            mgr.retract(a, reason="   ")
        assert not is_retracted(mgr.recall(a))  # type: ignore[arg-type]

    def test_reason_is_redacted_of_secrets(self, mgr: MemoryManager) -> None:
        a, _ = _seed(mgr)
        mgr.retract(a, reason="clé AKIAIOSFODNN7EXAMPLE exposée")
        reason = (mgr.recall(a) or pytest.fail("perdu")).metadata[RETRACTED_KEY]["reason"]
        assert "AKIAIOSFODNN7EXAMPLE" not in reason

    def test_retracting_twice_or_restoring_a_live_memory_is_refused(self, mgr: MemoryManager) -> None:
        a, _ = _seed(mgr)
        with pytest.raises(GrimoireMemoryError, match="non retiré"):
            mgr.unretract(a)
        mgr.retract(a, reason="r")
        with pytest.raises(GrimoireMemoryError, match="déjà retiré"):
            mgr.retract(a, reason="r2")


class TestReadsFilterRetracted:
    """Les mêmes lectures que ``superseded_by`` : recherche, hybride, taxonomie, rappel typé, listes."""

    @pytest.fixture
    def seeded(self, kind: str, mgr: MemoryManager) -> tuple[MemoryManager, str, str]:
        if kind not in _SEARCHABLE:
            pytest.skip("pas de recherche hors ligne sur ce backend")
        a, b = _seed(mgr)
        mgr.retract(a, reason="périmée")
        return mgr, a, b

    def test_search_hides_then_includes(self, seeded: tuple[MemoryManager, str, str]) -> None:
        mgr, a, b = seeded
        assert _ids(mgr.search(QUERY)) == {b}
        assert _ids(mgr.search(QUERY, include_retracted=True)) == {a, b}

    def test_taxonomy_search_hides_then_includes(self, seeded: tuple[MemoryManager, str, str]) -> None:
        mgr, a, b = seeded
        assert _ids(mgr.search_taxonomy(QUERY)) == {b}
        assert a in _ids(mgr.search_taxonomy(QUERY, include_retracted=True))

    def test_recall_typed_hides_then_includes(self, kind: str, tmp_path: Path) -> None:
        mgr = MemoryManager(_make_backend(kind, tmp_path), project_name="demo", project_root=tmp_path) if kind in _SEARCHABLE else None
        if mgr is None:
            pytest.skip("pas de recherche hors ligne sur ce backend")
        # remember() range par identifiant déterministe et type : les valeurs de l'agent sont l'entrée.
        keep = mgr.remember("decisions", "agent", TEXT_B)
        drop = mgr.remember("decisions", "agent", TEXT_A)
        mgr.retract(drop.id, reason="périmée")
        assert _ids(mgr.recall_typed(QUERY, type_="decisions", agent="agent")) == {keep.id}
        assert drop.id in _ids(mgr.recall_typed(QUERY, type_="decisions", agent="agent", include_retracted=True))

    def test_get_all_get_all_filtered_and_taxonomy_hide_then_include(self, kind: str, mgr: MemoryManager) -> None:
        a, b = _seed(mgr)
        mgr.retract(a, reason="périmée")
        assert _ids(mgr.get_all()) == {b}
        assert _ids(mgr.get_all_filtered()) == {b}
        assert _ids(mgr.get_all(include_retracted=True)) == {a, b}
        # La pagination se fait APRÈS le filtre : une page ne se vide pas à moitié.
        assert _ids(mgr.get_all(offset=0, limit=1)) == {b}
        total = sum(w["total"] for w in mgr.taxonomy().values())
        assert total == 1
        every = sum(w["total"] for w in mgr.taxonomy(include_superseded=True, include_retracted=True).values())
        assert every == 2

    def test_maintenance_paths_keep_everything(self, mgr: MemoryManager) -> None:
        """Export, migration et projection lisent ``INCLUDE_ALL`` : un retrait ne les fait rien perdre."""
        a, b = _seed(mgr)
        mgr.retract(a, reason="périmée")
        assert _ids(mgr.get_all(**INCLUDE_ALL)) == {a, b}
        assert _ids(mgr.get_all_filtered(**INCLUDE_ALL)) == {a, b}

    def test_retracted_and_superseded_compose(self, kind: str, tmp_path: Path) -> None:
        if kind not in _SEARCHABLE:
            pytest.skip("pas de recherche hors ligne sur ce backend")
        mgr = MemoryManager(_make_backend(kind, tmp_path), project_name="demo", project_root=tmp_path)
        a, b = _seed(mgr)
        c = mgr.store("Décision : le backend vectoriel final est Qdrant 1.19.", user_id="u").id
        mgr.supersede(a, c)
        mgr.retract(b, reason="doublon")
        assert _ids(mgr.search(QUERY)) == {c}
        assert _ids(mgr.search(QUERY, include_superseded=True)) == {a, c}
        assert _ids(mgr.search(QUERY, include_retracted=True)) == {b, c}


class TestHybridKeepsRetractedOut:
    def test_companion_copy_late_on_the_mark_still_hidden(self, tmp_path: Path) -> None:
        """Le compagnon lexical est une copie : sa marque peut être en retard, le store principal fait foi."""
        companion = LocalMemoryBackend(tmp_path / "lexical.json")
        mgr = MemoryManager(
            LocalMemoryBackend(tmp_path / "primary.json"), lexical_companion=companion,
            project_name="demo", project_root=tmp_path,
        )
        a, b = _seed(mgr)
        assert _ids(mgr.hybrid_search(QUERY)) == {a, b}
        mgr.retract(a, reason="périmée")
        assert _ids(mgr.hybrid_search(QUERY)) == {b}
        assert _ids(mgr.hybrid_search(QUERY, include_retracted=True)) == {a, b}
        # Copie en retard : le compagnon n'a pas reçu la marque (écriture miroir perdue).
        stale = companion.recall(a)
        assert stale is not None
        companion.update(a, metadata={k: v for k, v in stale.metadata.items() if k != RETRACTED_KEY})
        assert not is_retracted(companion.recall(a))  # type: ignore[arg-type]
        assert _ids(mgr.hybrid_search(QUERY)) == {b}


class TestJournal:
    def test_every_gesture_writes_a_row_without_content(self, mgr: MemoryManager, tmp_path: Path) -> None:
        a, _ = _seed(mgr)
        mgr.retract(a, reason="périmée", actor="alice")
        mgr.unretract(a, reason="relue", actor="alice")
        mgr.update_tags(a, add=["revue"], remove=["adr"], reason="classement", actor="alice")
        rows = [r for r in _journal(tmp_path) if r["op"] in {"retract", "unretract", "update_tags"}]
        assert [r["op"] for r in rows] == ["retract", "unretract", "update_tags"]
        assert all(r["before_id"] == a and r["actor"] == "alice" and r["fingerprint"] for r in rows)
        assert rows[0]["reason"] == "périmée"
        assert rows[2]["tags_added"] == ["revue"]
        assert rows[2]["tags_removed"] == ["adr"]
        raw = (tmp_path / JOURNAL_RELPATH).read_text(encoding="utf-8")
        assert TEXT_A not in raw

    def test_supersede_is_journaled_through_the_manager(self, mgr: MemoryManager, tmp_path: Path) -> None:
        a, b = _seed(mgr)
        mgr.supersede(a, b, reason="remplacée")
        assert [r["op"] for r in _journal(tmp_path)][-1] == "supersede"


class TestTags:
    def test_add_remove_replace(self, mgr: MemoryManager) -> None:
        a, _ = _seed(mgr)
        assert set((mgr.update_tags(a, add=["x", "y"]) or pytest.fail("perdu")).tags) == {"adr", "x", "y"}
        assert set((mgr.update_tags(a, remove=["x"]) or pytest.fail("perdu")).tags) == {"adr", "y"}
        assert set((mgr.update_tags(a, replace=["z"]) or pytest.fail("perdu")).tags) == {"z"}
        assert (mgr.update_tags(a, replace=[]) or pytest.fail("perdu")).tags == ()
        # Le texte et les métadonnées d'origine ne bougent pas.
        kept = mgr.recall(a)
        assert kept is not None
        assert kept.text == TEXT_A

    def test_noop_writes_nothing(self, mgr: MemoryManager, tmp_path: Path) -> None:
        a, _ = _seed(mgr)
        before = len(_journal(tmp_path))
        mgr.update_tags(a, add=["adr"])
        assert len(_journal(tmp_path)) == before

    def test_refusals(self, mgr: MemoryManager) -> None:
        a, _ = _seed(mgr)
        with pytest.raises(GrimoireMemoryError, match="exclusif"):
            mgr.update_tags(a, add=["x"], replace=["y"])
        with pytest.raises(GrimoireMemoryError, match="Rien à faire"):
            mgr.update_tags(a)
        with pytest.raises(GrimoireMemoryError, match="secret"):
            mgr.update_tags(a, add=["AKIAIOSFODNN7EXAMPLE"])
        with pytest.raises(GrimoireMemoryError, match="64 caractères"):
            mgr.update_tags(a, add=["x" * 65])
        with pytest.raises(GrimoireMemoryError, match="Trop d'étiquettes"):
            mgr.update_tags(a, add=[f"t{i}" for i in range(40)])


class TestProjectBoundary:
    """Aucun geste n'agit sur le souvenir d'un autre projet."""

    @pytest.fixture
    def foreign(self, mgr: MemoryManager) -> str:
        entry = mgr.backend.store(
            "Souvenir d'un autre projet", user_id="u", tags=("t",),
            metadata={"project_name": "autre", "source_kind": "memory"},
        )
        return entry.id

    def test_every_gesture_refuses_and_leaves_the_memory_intact(self, mgr: MemoryManager, foreign: str) -> None:
        own = mgr.store("Souvenir du projet courant", user_id="u").id
        for call in (
            lambda: mgr.retract(foreign, reason="x"),
            lambda: mgr.unretract(foreign),
            lambda: mgr.update_tags(foreign, add=["x"]),
            lambda: mgr.supersede(foreign, own),
            lambda: mgr.supersede(own, foreign),
            lambda: mgr.delete(foreign),
        ):
            with pytest.raises(GrimoireMemoryError, match="appartient au projet"):
                call()
        kept = mgr.backend.recall(foreign)
        assert kept is not None
        assert kept.metadata.get(RETRACTED_KEY) is None
        assert kept.tags == ("t",)
        assert not (mgr.backend.recall(own) or pytest.fail("perdu")).metadata.get("superseded_by")

    def test_cli_and_mcp_entry_point_refuse_too(self, mgr: MemoryManager, foreign: str) -> None:
        with pytest.raises(GrimoireMemoryError, match="appartient au projet"):
            run_gesture(mgr, "retract", foreign, reason="x")


class _NoUpdate(LocalMemoryBackend):
    def update(self, *a: Any, **k: Any) -> MemoryEntry | None:
        raise NotImplementedError("pas de update")


class _ForgetfulUpdate(LocalMemoryBackend):
    """Dit réussir mais ne garde ni la marque ni les étiquettes."""

    def update(self, entry_id: str, **_: Any) -> MemoryEntry | None:
        return self.recall(entry_id)


class TestFailClosed:
    def test_backend_without_update_refuses_and_deletes_nothing(self, tmp_path: Path) -> None:
        mgr = MemoryManager(_NoUpdate(tmp_path / "m.json"), project_name="demo", project_root=tmp_path)
        a = mgr.store(TEXT_A, user_id="u").id
        for call in (
            lambda: mgr.retract(a, reason="x"),
            lambda: mgr.update_tags(a, add=["x"]),
        ):
            with pytest.raises(GrimoireMemoryError, match="ne sait pas modifier"):
                call()
        kept = mgr.recall(a)
        assert kept is not None
        assert kept.text == TEXT_A
        assert not is_retracted(kept)
        assert mgr.backend.count() == 1
        assert not [r for r in _journal(tmp_path) if r["op"] in {"retract", "update_tags"}]

    def test_unconfirmed_write_is_an_error_not_a_silent_success(self, tmp_path: Path) -> None:
        mgr = MemoryManager(_ForgetfulUpdate(tmp_path / "m.json"), project_name="demo", project_root=tmp_path)
        a = mgr.store(TEXT_A, user_id="u").id
        with pytest.raises(GrimoireMemoryError, match="non confirmé"):
            mgr.retract(a, reason="x")
        with pytest.raises(GrimoireMemoryError, match="non confirmé"):
            mgr.update_tags(a, add=["x"])
        assert not [r for r in _journal(tmp_path) if r["op"] in {"retract", "update_tags"}]

    def test_supersede_refuses_a_retracted_replacement(self, tmp_path: Path) -> None:
        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"), project_name="demo", project_root=tmp_path)
        a, b = _seed(mgr)
        mgr.retract(b, reason="faux")
        with pytest.raises(GrimoireMemoryError, match="Remplaçant retiré"):
            mgr.supersede(a, b)


class TestGraphMirror:
    def test_retraction_is_projected_to_the_graph(self, tmp_path: Path) -> None:
        seen: list[MemoryEntry] = []

        class _Graph:
            def upsert_memory(self, entry: MemoryEntry) -> None:
                seen.append(entry)

        mgr = MemoryManager(
            LocalMemoryBackend(tmp_path / "m.json"), project_name="demo", project_root=tmp_path,
            memory_graph=_Graph(),
        )
        a, _ = _seed(mgr)
        mgr.retract(a, reason="périmée")
        assert is_retracted(seen[-1])
        mgr.unretract(a)
        assert not is_retracted(seen[-1])


class TestProvenanceCardShowsTheCycle:
    def test_a_retracted_memory_read_on_request_says_retiré(self, tmp_path: Path) -> None:
        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"), project_name="demo", project_root=tmp_path)
        a, b = _seed(mgr)
        mgr.retract(a, reason="périmée")
        by_id = {e.id: e for e in mgr.search(QUERY, include_retracted=True)}
        assert by_id[a].provenance["cycle"] == "retiré"
        assert by_id[b].provenance["cycle"] == "actif"


# ── Relecture adversariale de la PR #932 : perte et remise en service à tort ─────────────────


class TestUnretractRemovesTheKey:
    def test_the_key_is_gone_on_replace_backends_and_neutral_on_merge_backends(
        self, kind: str, mgr: MemoryManager,
    ) -> None:
        a, _ = _seed(mgr)
        mgr.retract(a, reason="r")
        mgr.unretract(a)
        meta = (mgr.backend.recall(a) or pytest.fail("perdu")).metadata
        assert not is_retracted(mgr.backend.recall(a))  # type: ignore[arg-type]
        if kind != "mempalace":  # MemPalace fusionne les métadonnées : la clé ne peut qu'y être neutralisée
            assert RETRACTED_KEY not in meta


class TestRollbackAfterUnconfirmedRetraction:
    def test_rollback_clears_the_mark_and_keeps_the_original_error(self, tmp_path: Path) -> None:
        """Un backend qui écrit la marque mais la relit mal : la marque ne doit pas rester posée."""

        class _Lying(LocalMemoryBackend):
            def recall(self, entry_id: str) -> MemoryEntry | None:
                got = super().recall(entry_id)
                if got is not None and got.metadata.get(RETRACTED_KEY):
                    return None if self.hide else got  # type: ignore[has-type]
                return got

        backend = _Lying(tmp_path / "m.json")
        backend.hide = False  # type: ignore[attr-defined]
        mgr = MemoryManager(backend, project_name="demo", project_root=tmp_path)
        a = mgr.store(TEXT_A, user_id="u").id
        backend.hide = True  # type: ignore[attr-defined]
        with pytest.raises(GrimoireMemoryError, match="Retrait non confirmé"):
            mgr.retract(a, reason="x")
        backend.hide = False  # type: ignore[attr-defined]
        assert not is_retracted(backend.recall(a))  # type: ignore[arg-type]

    def test_a_failed_undo_chains_the_original_error(self, tmp_path: Path) -> None:
        class _Breaking(LocalMemoryBackend):
            calls = 0

            def update(self, *a: Any, **k: Any) -> MemoryEntry | None:
                self.calls += 1
                if self.calls >= 2:
                    raise OSError("disque plein")
                return self.recall(a[0])  # la première écriture « réussit » sans rien garder

        backend = _Breaking(tmp_path / "m.json")
        mgr = MemoryManager(backend, project_name="demo", project_root=tmp_path)
        a = mgr.store(TEXT_A, user_id="u").id
        with pytest.raises(GrimoireMemoryError, match="Annulation impossible aussi : disque plein") as caught:
            mgr.retract(a, reason="x")
        assert "Retrait non confirmé" in str(caught.value)
        assert isinstance(caught.value.__cause__, GrimoireMemoryError)
        assert "Retrait non confirmé" in str(caught.value.__cause__)


class TestConsolidateNeverMixesStates:
    """``memory gc`` dédoublonne sur (texte, utilisateur) : un retiré ou remplacé n'est jamais regroupé avec un vivant."""

    @pytest.fixture(params=("local", "lexical", "tantivy"))
    def cmgr(self, request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryManager:
        return MemoryManager(
            _make_backend(str(request.param), tmp_path, monkeypatch), project_name="demo", project_root=tmp_path,
        )

    def test_retracted_copy_is_kept_with_its_reason_and_the_live_copy_too(self, cmgr: MemoryManager) -> None:
        live = cmgr.store(TEXT_A, user_id="u").id
        gone = cmgr.store(TEXT_A, user_id="u").id
        cmgr.retract(gone, reason="doublon erroné")
        assert cmgr.consolidate() == 0
        kept = {e.id: e for e in cmgr.get_all(**INCLUDE_ALL)}
        assert set(kept) == {live, gone}
        assert is_retracted(kept[gone])
        assert kept[gone].metadata[RETRACTED_KEY]["reason"] == "doublon erroné"
        assert not is_retracted(kept[live])

    def test_retracting_the_oldest_does_not_delete_the_live_copy(self, cmgr: MemoryManager) -> None:
        first = cmgr.store(TEXT_A, user_id="u").id
        second = cmgr.store(TEXT_A, user_id="u").id
        cmgr.retract(first, reason="erreur")
        cmgr.consolidate()
        assert _ids(cmgr.get_all()) == {second}, "il doit rester un souvenir servi"
        assert _ids(cmgr.get_all(**INCLUDE_ALL)) == {first, second}

    def test_superseded_copy_is_not_grouped_either(self, cmgr: MemoryManager) -> None:
        old = cmgr.store(TEXT_A, user_id="u").id
        twin = cmgr.store(TEXT_A, user_id="u").id
        new = cmgr.store(TEXT_B, user_id="u").id
        cmgr.supersede(old, new)
        cmgr.consolidate()
        assert _ids(cmgr.get_all(**INCLUDE_ALL)) == {old, twin, new}

    def test_plain_duplicates_are_still_removed(self, cmgr: MemoryManager) -> None:
        cmgr.store(TEXT_A, user_id="u")
        cmgr.store(TEXT_A, user_id="u")
        assert cmgr.consolidate() == 1
        assert cmgr.backend.count() == 1


class TestRememberKeepsTheMarks:
    """Réenregistrer le même texte ne remet pas en service un souvenir retiré, ni ne le fait oublier."""

    def test_remember_again_keeps_the_retraction(self, kind: str, mgr: MemoryManager) -> None:
        first = mgr.remember("decisions", "agentA", TEXT_A, tags=("t1", "t2"), source="src://a")
        mgr.retract(first.id, reason="fausse")
        again = mgr.remember("decisions", "agentA", TEXT_A, tags=("t1",))
        assert again.id == first.id
        kept = mgr.backend.recall(first.id)
        assert kept is not None
        assert is_retracted(kept), f"{kind} : le retrait a été effacé par remember"
        assert kept.metadata[RETRACTED_KEY]["reason"] == "fausse"

    def test_remember_again_keeps_the_replacement(self, mgr: MemoryManager) -> None:
        old = mgr.remember("decisions", "agentA", TEXT_A)
        new = mgr.remember("decisions", "agentA", TEXT_B)
        mgr.supersede(old.id, new.id)
        mgr.remember("decisions", "agentA", TEXT_A)
        assert (mgr.backend.recall(old.id) or pytest.fail("perdu")).metadata.get("superseded_by") == new.id

    def test_upsert_keeps_the_retraction_too(self, kind: str, mgr: MemoryManager) -> None:
        if kind in ("qdrant", "mempalace"):
            pytest.skip("pas d'upsert à identifiant fourni sur ce backend")
        mgr.upsert("fixed-id", TEXT_A, user_id="u")
        mgr.retract("fixed-id", reason="fausse")
        mgr.upsert("fixed-id", TEXT_A, user_id="u")
        assert is_retracted(mgr.backend.recall("fixed-id"))  # type: ignore[arg-type]
