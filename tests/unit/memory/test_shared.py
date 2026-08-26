"""Tests de la mémoire transverse — les six modes de corruption qu'elle traite."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from grimoire.core.config import GrimoireConfig
from grimoire.memory import shared as sh
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.manager import MemoryManager

TODAY = dt.date(2026, 8, 26)


def _config(**memory: object) -> GrimoireConfig:
    return GrimoireConfig.from_dict({
        "project": {"name": "Mon Super Projet", "type": "generic", "stack": []},
        "memory": {"backend": "local", **memory},
        "agents": {"archetype": "minimal"},
    })


def _entry(**metadata: object) -> MemoryEntry:
    return MemoryEntry(id="e1", text="un motif", metadata=dict(metadata))


# ── Opt-in : rien ne traverse sans déclaration ────────────────────────────────


class TestOptIn:
    def test_disabled_by_default(self) -> None:
        assert sh.is_enabled(_config()) is False
        assert sh.open_shared(_config()) is None

    def test_enabled_by_declaration(self) -> None:
        assert sh.is_enabled(_config(shared_collection="GrimoireShared")) is True

    def test_blank_declaration_is_not_enabled(self) -> None:
        assert sh.is_enabled(_config(shared_collection="   ")) is False


# ── Frontière physique : un autre store, pas un filtre ────────────────────────


class TestPhysicalBoundary:
    def test_shared_store_lives_outside_the_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un store « transverse » rangé dans le projet ne traverse rien."""
        home = tmp_path / "shared-home"
        monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(home))
        assert sh.shared_home() == home

    def test_shared_store_is_a_different_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deux fichiers, deux racines — pas deux vues du même store."""
        monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
        cfg = _config(shared_collection="GrimoireShared", collection_prefix="mon_projet")

        project = MemoryManager.from_config(cfg, project_root=tmp_path / "proj")
        shared = sh.open_shared(cfg)
        assert shared is not None

        project_file = Path(project.backend.health_check().detail["file"]).resolve()
        shared_file = Path(shared.backend.health_check().detail["file"]).resolve()
        assert project_file != shared_file
        # Le store transverse ne doit pas vivre sous le projet, sinon il ne
        # traverse rien.
        assert (tmp_path / "proj").resolve() not in shared_file.parents

    def test_project_and_shared_do_not_see_each_other(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La preuve qui compte : écrire d'un côté n'apparaît pas de l'autre."""
        monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
        cfg = _config(shared_collection="GrimoireShared", collection_prefix="mon_projet")

        project = MemoryManager.from_config(cfg, project_root=tmp_path / "proj")
        shared = sh.open_shared(cfg)
        assert shared is not None

        project.store("secret strictement local", metadata={"scope": "project"})
        sh.promote(
            shared,
            "les migrations Alembic cassent quand deux heads coexistent",
            domain="alembic",
            project_name="Mon Super Projet",
            today=TODAY,
        )

        project_texts = [e.text for e in project.get_all()]
        shared_texts = [e.text for e in shared.get_all()]
        assert "secret strictement local" in project_texts
        assert "secret strictement local" not in shared_texts
        assert not any("Alembic" in t for t in project_texts)


# ── Garde de promotion : un fait de projet ne monte pas ───────────────────────


class TestPromotionGuard:
    def test_pattern_is_promotable(self) -> None:
        verdict = sh.check_promotable(
            "les migrations Alembic cassent quand deux heads coexistent dans le dépôt",
            project_name="Mon Super Projet",
            domain="alembic",
        )
        assert verdict.ok, verdict.reasons

    def test_naming_the_project_is_refused(self) -> None:
        """« l'app X utilise Postgres » : sa vérité dépend d'un HEAD git."""
        verdict = sh.check_promotable(
            "Mon Super Projet utilise Postgres 16 en production depuis mars",
            project_name="Mon Super Projet",
            domain="postgres",
        )
        assert not verdict.ok
        assert any("nomme le projet" in r for r in verdict.reasons)

    def test_project_slug_is_also_caught(self) -> None:
        verdict = sh.check_promotable(
            "le déploiement de mon-super-projet exige deux réplicas au minimum",
            project_name="Mon Super Projet",
            domain="deploy",
        )
        assert not verdict.ok

    def test_url_is_refused(self) -> None:
        verdict = sh.check_promotable(
            "le endpoint https://api.interne/auth renvoie 401 sans en-tête",
            project_name="Autre", domain="auth",
        )
        assert not verdict.ok
        assert any("URL" in r for r in verdict.reasons)

    def test_absolute_path_is_refused(self) -> None:
        verdict = sh.check_promotable(
            "le fichier de config vit dans /srv/app/conf/settings.yaml sur les noeuds",
            project_name="Autre", domain="conf",
        )
        assert not verdict.ok
        assert any("chemin absolu" in r for r in verdict.reasons)

    def test_local_address_is_refused(self) -> None:
        verdict = sh.check_promotable(
            "le service d'index écoute sur localhost:6333 et refuse les connexions",
            project_name="Autre", domain="index",
        )
        assert not verdict.ok

    def test_domain_is_required(self) -> None:
        verdict = sh.check_promotable(
            "un motif parfaitement générique et suffisamment long pour passer",
            project_name="Autre", domain="",
        )
        assert not verdict.ok
        assert any("domaine" in r for r in verdict.reasons)

    def test_too_short_is_refused(self) -> None:
        assert not sh.check_promotable("trop court", project_name="Autre", domain="x").ok


class TestPromote:
    def _shared(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryManager:
        monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
        manager = sh.open_shared(_config(shared_collection="GrimoireShared"))
        assert manager is not None
        return manager

    def test_refused_promotion_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        shared = self._shared(tmp_path, monkeypatch)
        with pytest.raises(sh.SharedMemoryError, match="nomme le projet"):
            sh.promote(
                shared, "Mon Super Projet tourne sur Postgres 16 depuis toujours",
                domain="db", project_name="Mon Super Projet", today=TODAY,
            )

    def test_nothing_is_written_when_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shared = self._shared(tmp_path, monkeypatch)
        with pytest.raises(sh.SharedMemoryError):
            sh.promote(
                shared, "Mon Super Projet tourne sur Postgres 16 depuis toujours",
                domain="db", project_name="Mon Super Projet", today=TODAY,
            )
        assert shared.get_all() == []

    def test_force_records_the_bypass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un contournement doit rester visible à la relecture."""
        shared = self._shared(tmp_path, monkeypatch)
        entry = sh.promote(
            shared, "Mon Super Projet tourne sur Postgres 16 depuis toujours",
            domain="db", project_name="Mon Super Projet", force=True, today=TODAY,
        )
        assert entry.metadata["promotion_forced"] is True
        assert entry.metadata["promotion_warnings"]

    def test_provenance_is_recorded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        shared = self._shared(tmp_path, monkeypatch)
        entry = sh.promote(
            shared, "les migrations Alembic cassent quand deux heads coexistent",
            domain="Alembic", project_name="Mon Super Projet", today=TODAY,
        )
        assert entry.metadata["learned_in"] == ["mon-super-projet"]
        assert entry.metadata["domain"] == "alembic"
        assert entry.metadata["wing"] == "domain-alembic"
        assert entry.metadata["last_confirmed_at"] == "2026-08-26"
        assert entry.metadata["promotion_forced"] is False


# ── Décroissance de confiance ─────────────────────────────────────────────────


class TestFreshnessDecay:
    def test_recent_is_current(self) -> None:
        entry = _entry(last_confirmed_at="2026-08-01")
        assert sh.freshness_of(entry, today=TODAY)[0] == sh.FRESHNESS_CURRENT

    def test_ages_after_the_first_threshold(self) -> None:
        entry = _entry(last_confirmed_at="2026-04-01")  # 147 jours
        state, age = sh.freshness_of(entry, today=TODAY)
        assert state == sh.FRESHNESS_AGING
        assert age == 147

    def test_becomes_hypothesis_when_very_old(self) -> None:
        """Une connaissance que personne ne revérifie n'est plus un fait."""
        entry = _entry(last_confirmed_at="2025-01-01")
        assert sh.freshness_of(entry, today=TODAY)[0] == sh.FRESHNESS_HYPOTHESIS

    def test_contradiction_outweighs_recency(self) -> None:
        entry = _entry(last_confirmed_at="2026-08-25", contradicted_in=["autre-projet"])
        assert sh.freshness_of(entry, today=TODAY)[0] == sh.FRESHNESS_HYPOTHESIS

    def test_missing_date_is_hypothesis_not_current(self) -> None:
        """Sans date, on ne suppose pas la fraîcheur : on déclasse."""
        assert sh.freshness_of(_entry(), today=TODAY)[0] == sh.FRESHNESS_HYPOTHESIS

    def test_unparsable_date_is_hypothesis(self) -> None:
        entry = _entry(last_confirmed_at="hier")
        assert sh.freshness_of(entry, today=TODAY)[0] == sh.FRESHNESS_HYPOTHESIS

    def test_future_date_does_not_yield_negative_age(self) -> None:
        entry = _entry(last_confirmed_at="2027-01-01")
        state, age = sh.freshness_of(entry, today=TODAY)
        assert state == sh.FRESHNESS_CURRENT
        assert age == 0

    def test_boundaries(self) -> None:
        at_fresh = TODAY - dt.timedelta(days=sh.FRESH_DAYS)
        just_after = TODAY - dt.timedelta(days=sh.FRESH_DAYS + 1)
        assert sh.freshness_of(_entry(last_confirmed_at=at_fresh.isoformat()), today=TODAY)[0] == sh.FRESHNESS_CURRENT
        assert sh.freshness_of(_entry(last_confirmed_at=just_after.isoformat()), today=TODAY)[0] == sh.FRESHNESS_AGING


class TestConfirm:
    def test_confirmation_restores_freshness(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
        shared = sh.open_shared(_config(shared_collection="GrimoireShared"))
        assert shared is not None
        entry = sh.promote(
            shared, "les migrations Alembic cassent quand deux heads coexistent",
            domain="alembic", project_name="Projet A",
            today=dt.date(2025, 1, 1),
        )
        assert sh.freshness_of(shared.recall(entry.id), today=TODAY)[0] == sh.FRESHNESS_HYPOTHESIS

        sh.confirm(shared, entry.id, project_name="Projet B", today=TODAY)
        refreshed = next(e for e in shared.get_all() if e.text == entry.text)
        state, _ = sh.freshness_of(refreshed, today=TODAY)
        assert state == sh.FRESHNESS_CURRENT
        assert "projet-b" in refreshed.metadata["confirmed_in"]


# ── Restitution en deux passes, étiquetée ─────────────────────────────────────


class TestLayeredRecall:
    def _pair(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
        cfg = _config(shared_collection="GrimoireShared", collection_prefix="mon_projet")
        project = MemoryManager.from_config(cfg, project_root=tmp_path / "proj")
        return project, sh.open_shared(cfg)

    def test_scopes_stay_separate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, shared = self._pair(tmp_path, monkeypatch)
        project.store("alembic est configuré avec une seule head ici")
        sh.promote(
            shared, "les migrations alembic cassent quand deux heads coexistent",
            domain="alembic", project_name="Projet A", today=TODAY,
        )
        result = sh.layered_recall(project, shared, "alembic", today=TODAY)
        assert all(r.scope == sh.SCOPE_PROJECT for r in result.project)
        assert all(r.scope == sh.SCOPE_SHARED for r in result.shared)

    def test_project_comes_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """La vérité locale prime sur le motif importé (ORC-06)."""
        project, shared = self._pair(tmp_path, monkeypatch)
        project.store("alembic est configuré avec une seule head ici")
        sh.promote(
            shared, "les migrations alembic cassent quand deux heads coexistent",
            domain="alembic", project_name="Projet A", today=TODAY,
        )
        combined = sh.layered_recall(project, shared, "alembic", today=TODAY).all
        assert combined[0].scope == sh.SCOPE_PROJECT

    def test_shared_results_carry_a_caveat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un motif appris ailleurs ne doit jamais passer pour vérifié ici."""
        project, shared = self._pair(tmp_path, monkeypatch)
        sh.promote(
            shared, "les migrations alembic cassent quand deux heads coexistent",
            domain="alembic", project_name="Projet A", today=dt.date(2025, 1, 1),
        )
        result = sh.layered_recall(project, shared, "alembic", today=TODAY)
        assert result.shared
        item = result.shared[0]
        assert item.freshness == sh.FRESHNESS_HYPOTHESIS
        assert "à vérifier" in item.caveat
        assert item.learned_in == ("projet-a",)

    def test_works_without_a_shared_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GRIMOIRE_SHARED_HOME", str(tmp_path / "shared"))
        cfg = _config()
        project = MemoryManager.from_config(cfg, project_root=tmp_path / "proj")
        project.store("un souvenir local")
        result = sh.layered_recall(project, None, "souvenir", today=TODAY)
        assert result.shared == []
        assert result.to_dict()["shared"] == []


# ── Aides internes : cas limites ──────────────────────────────────────────────


class TestInternals:
    def test_empty_project_name_yields_no_alias(self) -> None:
        """Sans nom de projet, la garde ne doit pas inventer d'alias vide."""
        assert sh._project_aliases("   ") == ()
        verdict = sh.check_promotable(
            "un motif parfaitement generique et assez long pour passer la garde",
            project_name="", domain="x",
        )
        assert verdict.ok, verdict.reasons

    def test_as_tuple_tolerates_bad_metadata(self) -> None:
        assert sh._as_tuple(["a", "b"]) == ("a", "b")
        assert sh._as_tuple("pas une liste") == ()
        assert sh._as_tuple(None) == ()

    def test_confirm_falls_back_when_backend_has_no_update(self) -> None:
        """Un backend sans `update` doit quand meme voir sa confirmation prise."""

        class _NoUpdate:
            update = None

            def __init__(self) -> None:
                self.stored: list[dict[str, object]] = []

            def recall(self, entry_id: str) -> MemoryEntry:
                return MemoryEntry(id=entry_id, text="motif", metadata={"confirmed_in": []})

            def store(self, text: str, *, tags: tuple[str, ...] = (),
                      metadata: dict[str, object] | None = None) -> MemoryEntry:
                self.stored.append(dict(metadata or {}))
                return MemoryEntry(id="e1", text=text, metadata=dict(metadata or {}))

        backend = _NoUpdate()
        entry = sh.confirm(backend, "e1", project_name="Projet C", today=TODAY)
        assert entry is not None
        assert entry.metadata["confirmed_in"] == ["projet-c"]
        assert entry.metadata["last_confirmed_at"] == "2026-08-26"

    def test_confirm_returns_none_for_unknown_entry(self) -> None:
        class _Empty:
            def recall(self, entry_id: str) -> None:
                return None

        assert sh.confirm(_Empty(), "absent", project_name="P", today=TODAY) is None
