"""Tests for the Evidence Service module."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grimoire.core.exceptions import GrimoireEvidenceError
from grimoire.evidence.schemas import (
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    EvidenceProfile,
    VerdictResult,
)
from grimoire.evidence.service import EvidenceService


@pytest.fixture
def svc(tmp_path):
    return EvidenceService(tmp_path / "evidence")


def _test_item(kind: EvidenceKind = EvidenceKind.TEST, summary: str = "tests pass") -> EvidenceItem:
    return EvidenceItem.from_text(f"item-{kind.value}", kind, "output text", summary=summary)


def test_create_pack_requires_items(svc):
    with pytest.raises(GrimoireEvidenceError, match="at least one item"):
        svc.create_pack("GAO-task-001", EvidenceProfile.STANDARD, [])


def test_create_pack_assigns_id(svc):
    pack = svc.create_pack("GAO-task-001", EvidenceProfile.STANDARD, [_test_item()])
    assert pack.id.startswith("EVD-GAO-task-001")
    assert pack.task_id == "GAO-task-001"


def test_create_pack_persists(svc, tmp_path):
    svc.create_pack("GAO-task-001", EvidenceProfile.STANDARD, [_test_item()])
    svc2 = EvidenceService(tmp_path / "evidence")
    packs = svc2.list_packs("GAO-task-001")
    assert len(packs) == 1


def test_evidence_item_from_text_digest(svc):
    item = EvidenceItem.from_text("id1", EvidenceKind.LOG, "some log content")
    assert item.digest.startswith("sha256-")
    assert len(item.digest) > 10


def test_evidence_item_from_file(tmp_path):
    p = tmp_path / "test_output.txt"
    p.write_text("PASSED: 42 tests")
    item = EvidenceItem.from_file("item-test", EvidenceKind.TEST, p, summary="tests pass")
    assert item.digest.startswith("sha256-")
    assert item.uri == str(p)


def test_verify_passes_for_complete_pack(svc):
    items = [
        _test_item(EvidenceKind.TEST, "tests pass"),
        _test_item(EvidenceKind.LOG, "log"),
    ]
    pack = svc.create_pack("GAO-task-001", EvidenceProfile.STANDARD, items)
    verdict = svc.verify(pack)
    assert verdict.verdict == VerdictResult.PASSED
    assert verdict.decision.close_task is True
    assert verdict.decision.reopen_task is False


def test_verify_fails_for_missing_kind(svc):
    # STRICT requires TEST + LOG + DIFF
    items = [_test_item(EvidenceKind.TEST)]
    pack = svc.create_pack("GAO-task-002", EvidenceProfile.STRICT, items)
    verdict = svc.verify(pack)
    assert verdict.verdict == VerdictResult.FAILED
    assert verdict.decision.create_incident is True


def test_verify_fails_for_empty_items(svc):
    # Bypass create_pack validation by constructing directly
    pack = EvidencePack(
        id="EVD-GAO-task-003-001",
        task_id="GAO-task-003",
        profile=EvidenceProfile.LIGHT,
        items=(),
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    verdict = svc.verify(pack)
    assert verdict.verdict == VerdictResult.FAILED


def test_multiple_packs_incremented_id(svc):
    pack1 = svc.create_pack("GAO-task-001", EvidenceProfile.LIGHT, [_test_item()])
    pack2 = svc.create_pack("GAO-task-001", EvidenceProfile.LIGHT, [_test_item()])
    assert pack1.id != pack2.id
    assert pack2.id.endswith("-002")


def test_get_latest_verdict(svc):
    pack = svc.create_pack("GAO-task-001", EvidenceProfile.LIGHT, [_test_item()])
    svc.verify(pack)
    verdict = svc.get_latest_verdict("GAO-task-001")
    assert verdict is not None
    assert verdict.task_id == "GAO-task-001"


def test_get_latest_verdict_returns_none_if_no_verdict(svc):
    assert svc.get_latest_verdict("GAO-nonexistent-001") is None


def test_list_verdicts(svc):
    pack = svc.create_pack("GAO-task-001", EvidenceProfile.LIGHT, [_test_item()])
    verdict = svc.verify(pack)

    assert svc.list_verdicts() == [verdict]
    assert svc.list_verdicts("GAO-task-001") == [verdict]
    assert svc.list_verdicts("GAO-other-001") == []
