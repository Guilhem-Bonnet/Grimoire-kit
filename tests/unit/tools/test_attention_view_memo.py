"""Lectures d'affichage du cockpit gardées dans un ``view_scope`` (#1243, second lot).

Défaut empêché : preuves, verdicts et journal d'événements du kernel relus et reparsés pour chacune des ~240 tâches
(ou des 47 runs) d'une requête « À traiter ». Défaut à ne pas rouvrir : un chemin de décision servi depuis la mémoire,
ou une valeur périmée après une écriture.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import file_memo
from grimoire.evidence.schemas import (
    EvidenceItem,
    EvidenceKind,
    EvidenceProfile,
    VerdictDecision,
    VerdictResult,
    VerificationVerdict,
)
from grimoire.evidence.service import EvidenceService
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import RunEvent, RunEventType


@pytest.fixture(autouse=True)
def _vide() -> Iterator[None]:
    file_memo.clear()
    yield
    file_memo.clear()


@pytest.fixture
def _ancien(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ctime`` ne se vieillit pas depuis l'espace utilisateur : on abaisse la fenêtre d'horloge."""
    monkeypatch.setattr(file_memo, "COARSE_CLOCK_NS", -(10**18))


def _counter(path: Path, calls: list[bytes]) -> Any:
    def compute(data: bytes) -> str:
        calls.append(data)
        return data.decode("utf-8").upper()

    return compute


class TestViewDerived:
    def test_hors_scope_rien_n_est_garde(self, tmp_path: Path, _ancien: None) -> None:
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        calls: list[bytes] = []
        for _ in range(2):
            assert file_memo.view_derived(path, "k", _counter(path, calls)) == "ALPHA"
        assert len(calls) == 2

    def test_dans_le_scope_un_fichier_immobile_n_est_lu_qu_une_fois(self, tmp_path: Path, _ancien: None) -> None:
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        calls: list[bytes] = []
        with file_memo.view_scope():
            for _ in range(3):
                assert file_memo.view_derived(path, "k", _counter(path, calls)) == "ALPHA"
        assert len(calls) == 1

    def test_le_scope_se_referme(self, tmp_path: Path, _ancien: None) -> None:
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        calls: list[bytes] = []
        with file_memo.view_scope():
            file_memo.view_derived(path, "k", _counter(path, calls))
        file_memo.view_derived(path, "k", _counter(path, calls))  # dehors : relu, même si la mémoire existe
        assert len(calls) == 2

    def test_une_ecriture_est_vue(self, tmp_path: Path, _ancien: None) -> None:
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        calls: list[bytes] = []
        with file_memo.view_scope():
            assert file_memo.view_derived(path, "k", _counter(path, calls)) == "ALPHA"
            path.write_text("bravo!")
            assert file_memo.view_derived(path, "k", _counter(path, calls)) == "BRAVO!"

    def test_meme_taille_et_date_restauree_est_vue_par_ctime(self, tmp_path: Path, _ancien: None) -> None:
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        calls: list[bytes] = []
        with file_memo.view_scope():
            file_memo.view_derived(path, "k", _counter(path, calls))
            info = path.stat()
            path.write_text("bravo")
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            assert file_memo.view_derived(path, "k", _counter(path, calls)) == "BRAVO"

    def test_un_fichier_recent_n_est_pas_garde(self, tmp_path: Path) -> None:
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        calls: list[bytes] = []
        with file_memo.view_scope():
            file_memo.view_derived(path, "k", _counter(path, calls))
            file_memo.view_derived(path, "k", _counter(path, calls))
        assert len(calls) == 2

    def test_une_erreur_n_est_jamais_gardee(self, tmp_path: Path, _ancien: None) -> None:
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        boom = {"on": True}

        def compute(data: bytes) -> str:
            if boom["on"]:
                raise ValueError("cassé")
            return "ok"

        with file_memo.view_scope():
            with pytest.raises(ValueError, match="cassé"):
                file_memo.view_derived(path, "k", compute)
            boom["on"] = False
            assert file_memo.view_derived(path, "k", compute) == "ok"

    def test_la_memoire_est_bornee(self, tmp_path: Path, _ancien: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(file_memo, "_VIEW_MAX_ENTRIES", 1)
        first, second = tmp_path / "a.txt", tmp_path / "b.txt"
        first.write_text("a")
        second.write_text("b")
        calls: list[bytes] = []
        with file_memo.view_scope():
            file_memo.view_derived(first, "k", _counter(first, calls))
            file_memo.view_derived(second, "k", _counter(second, calls))
            assert len(file_memo._view_entries) == 1

    def test_sous_windows_rien_n_est_garde(
        self, tmp_path: Path, _ancien: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(file_memo, "_WINDOWS", True)
        path = tmp_path / "a.txt"
        path.write_text("alpha")
        calls: list[bytes] = []
        with file_memo.view_scope():
            file_memo.view_derived(path, "k", _counter(path, calls))
            file_memo.view_derived(path, "k", _counter(path, calls))
        assert len(calls) == 2


def _item() -> EvidenceItem:
    return EvidenceItem(id="I-1", kind=EvidenceKind.TEST, uri="t", digest="d", summary="pytest passe")


def _legacy_packs(path: Path) -> dict[str, Any]:
    """L'ancienne lecture de ``_load_packs`` : un parse par appel, rien de gardé."""
    from grimoire.evidence.schemas import EvidencePack

    out: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            pack = EvidencePack.from_dict(json.loads(line))
            out[pack.id] = pack
        except (json.JSONDecodeError, KeyError):
            pass
    return out


class TestEvidenceInView:
    def _service(self, tmp_path: Path) -> EvidenceService:
        service = EvidenceService(tmp_path / "evidence")
        service.create_pack("T-1", EvidenceProfile.LIGHT, [_item()])
        service.create_pack("T-2", EvidenceProfile.LIGHT, [_item()])
        with (tmp_path / "evidence" / "packs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("{pas du json\n")
        return service

    def test_packs_egaux_a_l_ancienne_lecture_dans_et_hors_scope(self, tmp_path: Path, _ancien: None) -> None:
        service = self._service(tmp_path)
        legacy = _legacy_packs(tmp_path / "evidence" / "packs.jsonl")
        plain = service.list_packs()
        with file_memo.view_scope():
            first = service.list_packs()
            second = service.list_packs("T-1")
        assert plain == first == list(legacy.values())
        assert [p.task_id for p in second] == ["T-1"]

    def test_un_pack_ajoute_est_vu_dans_le_scope(self, tmp_path: Path, _ancien: None) -> None:
        service = self._service(tmp_path)
        with file_memo.view_scope():
            assert len(service.list_packs()) == 2
            service.create_pack("T-3", EvidenceProfile.LIGHT, [_item()])
            assert len(service.list_packs()) == 3

    def test_verdicts_egaux_et_une_ecriture_est_vue(self, tmp_path: Path, _ancien: None) -> None:
        service = self._service(tmp_path)
        pack = service.list_packs("T-1")[0]

        def verdict(vid: str, result: VerdictResult) -> VerificationVerdict:
            return VerificationVerdict(
                id=vid,
                task_id="T-1",
                evidence_pack_id=pack.id,
                verdict=result,
                profile=EvidenceProfile.LIGHT,
                checks=(),
                decision=VerdictDecision(
                    close_task=result is VerdictResult.PASSED, reopen_task=False, create_incident=False
                ),
                created_at="2026-11-25T00:00:00+00:00",
            )

        service.record_verdict(verdict("V-1", VerdictResult.FAILED))
        plain = service.list_verdicts("T-1")
        with file_memo.view_scope():
            assert service.list_verdicts("T-1") == plain
            assert service.get_latest_verdict("T-1") is not None
            assert service.get_latest_verdict("T-1").id == "V-1"  # type: ignore[union-attr]
            assert service.list_verdicts("T-autre") == []
            service.record_verdict(verdict("V-2", VerdictResult.PASSED))
            assert service.get_latest_verdict("T-1").id == "V-2"  # type: ignore[union-attr]
            assert len(service.list_verdicts()) == 2

    def test_dossier_sans_journal(self, tmp_path: Path) -> None:
        service = EvidenceService(tmp_path / "vide")
        with file_memo.view_scope():
            assert service.list_packs() == []
            assert service.list_verdicts() == []


def _event(eid: str, wfi: str, kind: RunEventType = RunEventType.STEP_FAILED) -> RunEvent:
    return RunEvent(
        id=eid,
        run_id="r",
        mission_id="m",
        task_id="t",
        workflow_instance_id=wfi,
        event_type=kind,
        actor_id="a",
        host_id="h",
        created_at="2026-11-25T00:00:00+00:00",
        payload={"step_id": "s", "reason": "x"},
    )


class TestKernelEventsInView:
    def _write(self, root: Path, events: list[RunEvent]) -> RuntimeKernel:
        kernel = RuntimeKernel(root)
        with (root / "run_events.jsonl").open("w", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event.to_dict()) + "\n")
            fh.write("{casse\n")
        return kernel

    def test_memes_evenements_dans_et_hors_scope_filtres_par_run(self, tmp_path: Path, _ancien: None) -> None:
        kernel = self._write(tmp_path / "rt", [_event("e1", "w1"), _event("e2", "w2"), _event("e3", "w1")])
        plain_all, plain_w1 = kernel.get_run_events(), kernel.get_run_events("w1")
        with file_memo.view_scope():
            assert kernel.get_run_events() == plain_all
            assert kernel.get_run_events("w1") == plain_w1
            assert kernel.get_run_events("w1") == plain_w1
        assert [e.id for e in plain_w1] == ["e1", "e3"]
        assert len(plain_all) == 3

    def test_un_evenement_ajoute_est_vu(self, tmp_path: Path, _ancien: None) -> None:
        root = tmp_path / "rt"
        kernel = self._write(root, [_event("e1", "w1")])
        with file_memo.view_scope():
            assert len(kernel.get_run_events("w1")) == 1
            with (root / "run_events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_event("e9", "w1").to_dict()) + "\n")
            assert len(kernel.get_run_events("w1")) == 2

    def test_sans_journal(self, tmp_path: Path) -> None:
        assert RuntimeKernel(tmp_path / "rt").get_run_events() == []

    def test_statut_des_runs_dans_attach_live_status(self, tmp_path: Path, _ancien: None) -> None:
        """``flow_runs._attach_live_status`` ouvre le scope : même sortie qu'avec un moteur sans scope."""
        from grimoire.tools import flow_runs

        root = tmp_path / "proj"
        (root / "_grimoire-runtime-output" / "flows").mkdir(parents=True)
        runs = [{"runId": "inconnu", "blueprintId": "bp", "createdAt": "x"}]
        flow_runs._attach_live_status(root, runs)
        assert runs[0]["status"] is None
        assert runs[0]["currentNode"] is None
