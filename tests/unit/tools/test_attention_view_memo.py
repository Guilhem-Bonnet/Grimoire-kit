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


# ── Revue adverse de #1383 ──────────────────────────────────────────────────


class TestServedValuesCannotPolluteTheMemory:
    """C2 : ce que la vue sert est une copie profonde ; la mutation d'un payload, d'un pack ou d'un verdict ne reste pas."""

    def test_un_payload_d_evenement_modifie_ne_pollue_ni_le_scope_ni_le_suivant(
        self, tmp_path: Path, _ancien: None
    ) -> None:
        kernel = RuntimeKernel(tmp_path / "rt")
        with (tmp_path / "rt" / "run_events.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_event("e1", "w1").to_dict()) + "\n")
        with file_memo.view_scope():
            kernel.get_run_events("w1")[0].payload["reason"] = "POLLUE"
            assert kernel.get_run_events("w1")[0].payload["reason"] == "x"
            kernel.get_run_events()[0].payload["reason"] = "POLLUE"
        with file_memo.view_scope():
            assert kernel.get_run_events("w1")[0].payload["reason"] == "x"

    def test_un_pack_et_un_verdict_servis_sont_des_copies(self, tmp_path: Path, _ancien: None) -> None:
        service = TestEvidenceInView()._service(tmp_path)
        with file_memo.view_scope():
            served = service.list_packs("T-1")[0]
            object.__setattr__(served, "task_id", "POLLUE")  # figé, mais forcé : la mémoire ne doit pas le voir
            assert [p.task_id for p in service.list_packs()] == ["T-1", "T-2"]
            fetched = service.get_pack(served.id)
            assert fetched is not None
            object.__setattr__(fetched, "task_id", "POLLUE")
        with file_memo.view_scope():
            assert [p.task_id for p in service.list_packs()] == ["T-1", "T-2"]


class TestScopeDoesNotLeak:
    """C3 : les deux sites d'ouverture sont des ``with`` simples ; rien ne reste actif derrière eux."""

    def test_le_scope_se_referme_apres_une_exception(self) -> None:
        with pytest.raises(RuntimeError), file_memo.view_scope():
            assert file_memo._view_active.get() is True
            raise RuntimeError("boom")
        assert file_memo._view_active.get() is False

    def test_les_scopes_imbriques_se_referment_dans_l_ordre(self) -> None:
        with file_memo.view_scope():
            with file_memo.view_scope():
                pass
            assert file_memo._view_active.get() is True
        assert file_memo._view_active.get() is False

    def test_le_scope_ne_passe_pas_a_un_autre_fil(self) -> None:
        import threading

        seen: list[bool] = []
        with file_memo.view_scope():
            thread = threading.Thread(target=lambda: seen.append(file_memo._view_active.get()))
            thread.start()
            thread.join()
        assert seen == [False]

    def test_les_sites_du_cockpit_ne_laissent_rien_d_actif(self, tmp_path: Path) -> None:
        from grimoire.tools import flow_runs

        root = tmp_path / "proj"
        (root / "_grimoire-runtime-output" / "flows").mkdir(parents=True)
        flow_runs._attach_live_status(root, [{"runId": "inconnu"}])
        assert file_memo._view_active.get() is False


GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
states: [proposed, ready, in_progress, blocked, review, accepted, released, archived]
transitions:
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["evidence_pack", "review_gate"]
profile_strictness:
  starter: advisory
  governed: hard_fail
"""


class TestGateReadsRealEvidence:
    """T1 : le gate de la vue LIT des preuves ; sa sortie égale celle du chemin sans scope, à chaque étape."""

    def _project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
        from grimoire.missions.schemas import TaskState
        from grimoire.missions.service import TaskService

        monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit"))
        for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
            monkeypatch.delenv(var, raising=False)
        root = tmp_path / "proj"
        root.mkdir()
        service = TaskService(root)
        task_id = service.add("À relire", ("le test passe",), actor="t", owner="g", ready=True).task.id
        service.claim(task_id, "agent-a", host="host-claude-code-cli")
        service.transition(task_id, TaskState.RUNNING, "agent-a")
        service.transition(task_id, TaskState.NEEDS_VERIFICATION, "agent-a")
        standard = root / "_grimoire" / "standard"
        standard.mkdir(parents=True)
        (standard / "evidence-gates.yaml").write_text(GATES, encoding="utf-8")
        (standard / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
        return root, task_id

    @staticmethod
    def _view(root: Path) -> dict[str, Any]:
        from datetime import UTC, datetime

        from grimoire.tools import workspace_attention as wa

        view = wa.attention_view(
            root,
            now=datetime(2026, 11, 25, tzinfo=UTC),
            pr_reader=lambda *_a, **_k: [],
            service_probe=lambda *_a: "online",
        )
        view.pop("generated_at")
        return view

    def _plain(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        import contextlib

        with monkeypatch.context() as patch:
            patch.setattr(file_memo, "view_scope", contextlib.nullcontext)
            return self._view(root)

    def _verify(self, root: Path, task_id: str, *, passes: bool) -> None:
        service = EvidenceService(root / "_grimoire-runtime-output" / "evidence")
        pack = service.create_pack(
            task_id,
            EvidenceProfile.STANDARD,
            [
                EvidenceItem(
                    id="I-t",
                    kind=EvidenceKind.TEST,
                    uri="a://t",
                    digest="d",
                    summary="le test passe" if passes else "rien",
                ),
                EvidenceItem(
                    id="I-l",
                    kind=EvidenceKind.LOG,
                    uri="a://l",
                    digest="d",
                    summary="le test passe" if passes else "rien",
                ),
            ],
            acceptance=("le test passe",),
        )
        service.verify(pack, acceptance=("le test passe",))

    def _reasons(self, view: dict[str, Any]) -> list[str]:
        return [
            item["detail"] if "detail" in item else str(item)
            for section in view["sections"]
            for item in section["items"]
        ]

    def test_equivalence_a_froid_a_chaud_et_apres_chaque_ecriture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _ancien: None
    ) -> None:
        root, task_id = self._project(tmp_path, monkeypatch)

        def same() -> dict[str, Any]:
            memo = self._view(root)
            assert memo == self._plain(root, monkeypatch)
            return memo

        none_yet = same()  # froid : ni pack ni verdict
        assert same() == none_yet  # chaud
        self._verify(root, task_id, passes=False)  # verdict FAILED
        failed = same()
        assert failed != none_yet
        self._verify(root, task_id, passes=True)  # puis PASSED
        passed = same()
        assert passed != failed
        # Le verdict est bien passé par la mémoire d'affichage : sinon rien de ce test ne prouve la mémoire.
        assert any(kind == "evidence-verdicts" for kind, _ in file_memo._view_entries)
        assert any(kind == "evidence-packs" for kind, _ in file_memo._view_entries)

        # Réécriture à taille égale, date restaurée : « passed » devient « failed ».
        path = root / "_grimoire-runtime-output" / "evidence" / "verdicts.jsonl"
        info = path.stat()
        data = path.read_bytes()
        forged = data.replace(b'"verdict": "passed"', b'"verdict": "failed"')
        assert forged != data
        assert len(forged) == len(data)
        path.write_bytes(forged)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        again = same()
        assert again != passed


class TestRealRunStatusFollowsEachStep:
    """T2 : le statut d'un vrai run, lu à chaud par ``_attach_live_status``, suit chaque étape du moteur."""

    def test_en_cours_checkpoint_puis_termine(self, tmp_path: Path, _ancien: None) -> None:
        import io

        from grimoire.flows.engine import FlowEngine
        from grimoire.flows.executor import InteractiveNodeExecutor
        from grimoire.tools.flow_runs import list_flow_runs

        blueprint = {
            "blueprintVersion": 1,
            "id": "deux-nodes",
            "name": "Deux nodes",
            "nodes": [
                {
                    "id": "a",
                    "kind": "pattern",
                    "ref": "ORC-01",
                    "label": "A",
                    "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
                },
                {
                    "id": "b",
                    "kind": "pattern",
                    "ref": "QUA-04",
                    "label": "B",
                    "pins": [{"id": "in", "direction": "in", "contract": "c1"}],
                },
            ],
            "edges": [{"from": "a.out", "to": "b.in", "contract": "c1"}],
        }
        path = tmp_path / "deux-nodes.blueprint.json"
        path.write_text(json.dumps(blueprint), encoding="utf-8")
        engine = FlowEngine(
            kernel_root=tmp_path / "_grimoire-runtime-output" / "runtime",
            flows_root=tmp_path / "_grimoire-runtime-output" / "flows",
            project_root=tmp_path,
        )
        wfi, _ = engine.run(path, executor=InteractiveNodeExecutor(stream=io.StringIO()))

        def shown() -> tuple[str | None, str | None]:
            run = list_flow_runs(tmp_path)[0]
            direct = engine.status(wfi.id, include_contract=False)
            assert (run["status"], run["currentNode"]) == (direct.status, direct.current_node)  # même chose, sans scope
            again = list_flow_runs(tmp_path)[0]  # à chaud : même réponse
            assert (again["status"], again["currentNode"]) == (run["status"], run["currentNode"])
            return run["status"], run["currentNode"]

        steps = [shown()]
        engine.resume(wfi.id, output={"pins": {"out": {"contract": "c1"}}})
        steps.append(shown())
        assert steps[-1] == ("checkpointed", "b")
        engine.resume(wfi.id, output={"pins": {}})
        steps.append(shown())
        assert steps[-1][0] == "completed"
        assert len(set(steps)) == 3  # chaque étape a changé ce que la vue affiche
        assert any(kind == "kernel-run-events" for kind, _ in file_memo._view_entries)
