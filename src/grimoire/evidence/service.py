"""Evidence Service — collect, store, and verify evidence packs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireEvidenceError
from grimoire.evidence.schemas import (
    EvidenceCoverage,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    EvidenceProfile,
    VerdictDecision,
    VerdictResult,
    VerificationCheck,
    VerificationVerdict,
)

# Minimum required evidence kinds per profile
_PROFILE_REQUIREMENTS: dict[EvidenceProfile, frozenset[EvidenceKind]] = {
    EvidenceProfile.LIGHT: frozenset({EvidenceKind.TEST}),
    EvidenceProfile.STANDARD: frozenset({EvidenceKind.TEST, EvidenceKind.LOG}),
    EvidenceProfile.STRICT: frozenset({EvidenceKind.TEST, EvidenceKind.LOG, EvidenceKind.DIFF}),
    EvidenceProfile.SECURITY_CRITICAL: frozenset({EvidenceKind.TEST, EvidenceKind.LOG, EvidenceKind.DIFF, EvidenceKind.REPORT}),
    EvidenceProfile.RELEASE: frozenset({EvidenceKind.TEST, EvidenceKind.LOG, EvidenceKind.DIFF, EvidenceKind.DOC, EvidenceKind.REPORT}),
}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class EvidenceService:
    """Manages evidence packs and verification verdicts for tasks.

    Evidence packs are stored as JSONL in the provided root directory.
    Verification verdicts are stored in a separate JSONL file.

    Usage::

        svc = EvidenceService(Path("_grimoire-runtime-output/evidence"))
        pack = svc.create_pack("GAO-task-001", EvidenceProfile.STRICT, items=[...])
        verdict = svc.verify(pack, acceptance=["tests pass", "no regressions"])
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._packs_path = root / "packs.jsonl"
        self._verdicts_path = root / "verdicts.jsonl"

    def _load_packs(self) -> dict[str, EvidencePack]:
        packs: dict[str, EvidencePack] = {}
        if not self._packs_path.exists():
            return packs
        for line in self._packs_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                packs_raw = json.loads(line)
                pack = EvidencePack.from_dict(packs_raw)
                packs[pack.id] = pack
            except (json.JSONDecodeError, KeyError):
                pass
        return packs

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def create_pack(
        self,
        task_id: str,
        profile: EvidenceProfile,
        items: list[EvidenceItem],
        *,
        workflow_instance_id: str = "",
        acceptance: tuple[str, ...] = (),
        pack_id: str | None = None,
    ) -> EvidencePack:
        if not items:
            raise GrimoireEvidenceError("EvidencePack requires at least one item")
        # Auto-generate id matching EVD-GAO-<area>-<seq> pattern
        if pack_id is None:
            existing = self._load_packs()
            task_packs = [p for p in existing.values() if p.task_id == task_id]
            seq = len(task_packs) + 1
            pack_id = f"EVD-{task_id}-{seq:03d}"
        # Compute coverage
        covered: list[str] = []
        missing: list[str] = list(acceptance)
        # Simple heuristic: an acceptance criterion is covered if any item summary mentions it
        for criterion in acceptance:
            for item in items:
                if criterion.lower() in item.summary.lower() or criterion.lower() in item.uri.lower():
                    covered.append(criterion)
                    if criterion in missing:
                        missing.remove(criterion)
                    break
        coverage = EvidenceCoverage(
            acceptance_covered=tuple(covered),
            acceptance_missing=tuple(missing),
        )
        pack = EvidencePack(
            id=pack_id,
            task_id=task_id,
            profile=profile,
            items=tuple(items),
            created_at=_now_iso(),
            workflow_instance_id=workflow_instance_id,
            coverage=coverage,
        )
        self._append(self._packs_path, pack.to_dict())
        return pack

    def get_pack(self, pack_id: str) -> EvidencePack | None:
        return self._load_packs().get(pack_id)

    def list_packs(self, task_id: str | None = None) -> list[EvidencePack]:
        packs = list(self._load_packs().values())
        if task_id is not None:
            packs = [p for p in packs if p.task_id == task_id]
        return packs

    def verify(
        self,
        pack: EvidencePack,
        *,
        acceptance: tuple[str, ...] = (),
        verdict_id: str | None = None,
    ) -> VerificationVerdict:
        """Run verification checks against an EvidencePack and produce a verdict."""
        required_kinds = _PROFILE_REQUIREMENTS.get(pack.profile, frozenset())
        present_kinds = {item.kind for item in pack.items}
        checks: list[VerificationCheck] = []

        # Check: evidence items present
        checks.append(VerificationCheck(
            id="evidence-present",
            result=VerdictResult.PASSED if pack.items else VerdictResult.FAILED,
            reason="" if pack.items else "No evidence items in pack",
        ))

        # Check: profile requirements met
        for kind in required_kinds:
            checks.append(VerificationCheck(
                id=f"kind-{kind.value}-present",
                result=VerdictResult.PASSED if kind in present_kinds else VerdictResult.FAILED,
                reason="" if kind in present_kinds else f"Missing evidence kind: {kind.value}",
            ))

        # Check: all digests are non-empty
        empty_digests = [item.id for item in pack.items if not item.digest]
        checks.append(VerificationCheck(
            id="digests-valid",
            result=VerdictResult.FAILED if empty_digests else VerdictResult.PASSED,
            reason=f"Empty digest on items: {empty_digests}" if empty_digests else "",
        ))

        # Check: acceptance coverage
        if acceptance:
            coverage = pack.coverage
            missing = list(acceptance)
            if coverage:
                missing = list(coverage.acceptance_missing)
            checks.append(VerificationCheck(
                id="acceptance-coverage",
                result=VerdictResult.FAILED if missing else VerdictResult.PASSED,
                reason=f"Uncovered criteria: {missing}" if missing else "",
            ))

        overall = VerdictResult.PASSED if all(c.result == VerdictResult.PASSED for c in checks) else VerdictResult.FAILED
        reopen = overall == VerdictResult.FAILED
        decision = VerdictDecision(
            close_task=overall == VerdictResult.PASSED,
            reopen_task=reopen,
            create_incident=reopen,
        )

        if verdict_id is None:
            seq = sum(1 for _ in self._load_verdicts(pack.task_id)) + 1
            verdict_id = f"ver-{pack.task_id}-{seq:03d}"

        verdict = VerificationVerdict(
            id=verdict_id,
            task_id=pack.task_id,
            evidence_pack_id=pack.id,
            verdict=overall,
            profile=pack.profile,
            checks=tuple(checks),
            decision=decision,
            created_at=_now_iso(),
        )
        self._append(self._verdicts_path, verdict.to_dict())
        return verdict

    def _load_verdicts(self, task_id: str | None = None) -> list[VerificationVerdict]:
        verdicts: list[VerificationVerdict] = []
        if not self._verdicts_path.exists():
            return verdicts
        for line in self._verdicts_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                v = VerificationVerdict.from_dict(raw)
                if task_id is None or v.task_id == task_id:
                    verdicts.append(v)
            except (json.JSONDecodeError, KeyError):
                pass
        return verdicts

    def get_latest_verdict(self, task_id: str) -> VerificationVerdict | None:
        verdicts = self._load_verdicts(task_id)
        return verdicts[-1] if verdicts else None

    def list_verdicts(self, task_id: str | None = None) -> list[VerificationVerdict]:
        """Return verification verdicts, optionally scoped to one task."""
        return self._load_verdicts(task_id)
