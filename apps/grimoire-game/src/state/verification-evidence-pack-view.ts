import type { AgentRole, VerificationEvidenceRef } from '../contracts/events';

import type { GameState } from './game-state';
import { createMissionPackByTaskId, type MissionPackSnapshot } from './mission-pack';
import {
  createMissionLedgerView,
  type MissionLedgerAttestationRecord,
  type MissionLedgerEvidenceRecord,
  type MissionLedgerVerificationRecord,
  type MissionLedgerVerificationStatus,
  type MissionLedgerVerificationVerdict,
  type MissionLedgerView,
  type MissionLedgerWorkItem
} from './mission-ledger-view';
import {
  createVerificationView,
  type TaskVerificationGate,
  type VerificationLinkedExternalReview
} from './verification-view';

export interface VerificationEvidencePack {
  packId: string;
  missionId: string;
  missionTitle: string;
  itemId: string;
  taskRef: string | null;
  verificationId: string;
  verificationRef: string;
  actionId: string | null;
  status: MissionLedgerVerificationStatus;
  verdict: MissionLedgerVerificationVerdict;
  checkedBy: string;
  checkedAt: string;
  traceId: string | null;
  gateSequenceId: number | null;
  correlationId: string | null;
  requestId: string | null;
  idempotencyKey: string | null;
  actorId: string | null;
  actorRole: AgentRole | null;
  controlRefs: readonly string[];
  controlsExecuted: readonly string[];
  evidenceRefs: readonly string[];
  typedEvidenceRefs: readonly VerificationEvidenceRef[];
  unmetControlRefs: readonly string[];
  linkedSequenceIds: readonly number[];
  evidence: readonly MissionLedgerEvidenceRecord[];
  externalReviews: readonly VerificationLinkedExternalReview[];
  attestation: MissionLedgerAttestationRecord | null;
  missionPack: MissionPackSnapshot | null;
  proofCoverage: MissionPackProofCoverage | null;
}

interface VerificationGateAuditRecord {
  sequenceId: number;
  actionId: string;
  verificationRef: string;
  traceId: string | null;
  taskId: string | null;
  controlsExecuted: readonly string[];
  typedEvidenceRefs: readonly VerificationEvidenceRef[];
  unmetControlRefs: readonly string[];
  correlationId: string | null;
  requestId: string | null;
  idempotencyKey: string | null;
  actorId: string | null;
  actorRole: AgentRole | null;
  linkedSequenceIds: readonly number[];
}

export interface MissionPackProofCoverage {
  expectedProofCount: number;
  satisfiedExpectedProofRefs: readonly string[];
  missingExpectedProofRefs: readonly string[];
  coverageRatio: number;
  fullyCovered: boolean;
}

export interface VerificationEvidencePackViewSummary {
  packCount: number;
  attestedCount: number;
  unattestedCount: number;
  missingEvidenceCount: number;
  missionPackLinkedCount: number;
  missionPackCoveredCount: number;
  missingExpectedProofCount: number;
}

export interface VerificationEvidencePackView {
  protocolVersion: string;
  lastSequenceId: number;
  packs: readonly VerificationEvidencePack[];
  summary: VerificationEvidencePackViewSummary;
}

export interface VerificationEvidencePackQuery {
  missionId?: string;
  itemId?: string;
  verificationId?: string;
  verificationRef?: string;
  taskRef?: string;
  evidenceRef?: string;
}

export interface VerificationEvidencePackQueryResult {
  packs: readonly VerificationEvidencePack[];
  totalCount: number;
}

export function createVerificationEvidencePackView(state: GameState): VerificationEvidencePackView {
  const ledger = createMissionLedgerView(state);
  const verificationView = createVerificationView(state);
  const verificationGateAuditByRef = createVerificationGateAuditByRef(state);
  const workItemsById = Object.fromEntries(ledger.workItems.map((item) => [item.itemId, item]));
  const missionsById = Object.fromEntries(ledger.missions.map((mission) => [mission.missionId, mission]));
  const attestationByVerificationId = Object.fromEntries(
    ledger.attestationRecords.map((attestation) => [attestation.verificationId, attestation])
  );
  const evidenceByRef = createEvidenceByRefIndex(ledger.evidenceRecords);
  const verificationByRef = Object.fromEntries(
    verificationView.tasks
      .filter((task): task is TaskVerificationGate => task.verificationChain.verificationRef !== null)
      .map((task) => [task.verificationChain.verificationRef, task])
  );
  const missionPackByTaskId = createMissionPackByTaskId(state);
  const packs = ledger.verificationRecords
    .map((verificationRecord) =>
      createVerificationEvidencePack(
        verificationRecord,
        workItemsById[verificationRecord.itemId] ?? null,
        missionsById,
        evidenceByRef,
        verificationByRef[verificationRecord.verificationRef] ?? null,
        verificationGateAuditByRef[verificationRecord.verificationRef] ?? null,
        attestationByVerificationId[verificationRecord.verificationId] ?? null,
        missionPackByTaskId[workItemsById[verificationRecord.itemId]?.taskRef ?? ''] ?? null
      )
    )
    .sort(compareVerificationEvidencePacks);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    packs,
    summary: {
      packCount: packs.length,
      attestedCount: packs.filter((pack) => pack.attestation !== null).length,
      unattestedCount: packs.filter((pack) => pack.attestation === null).length,
      missingEvidenceCount: packs.filter((pack) => pack.evidence.length === 0).length,
      missionPackLinkedCount: packs.filter((pack) => pack.missionPack !== null).length,
      missionPackCoveredCount: packs.filter((pack) => pack.proofCoverage?.fullyCovered === true).length,
      missingExpectedProofCount: packs.reduce(
        (count, pack) => count + (pack.proofCoverage?.missingExpectedProofRefs.length ?? 0),
        0
      )
    }
  };
}

export function queryVerificationEvidencePacks(
  view: VerificationEvidencePackView,
  query: VerificationEvidencePackQuery
): VerificationEvidencePackQueryResult {
  const packs = view.packs.filter((pack) => matchesVerificationEvidencePack(pack, query));

  return {
    packs,
    totalCount: packs.length
  };
}

function createVerificationEvidencePack(
  verificationRecord: MissionLedgerVerificationRecord,
  workItem: MissionLedgerWorkItem | null,
  missionsById: Record<string, MissionLedgerView['missions'][number]>,
  evidenceByRef: Record<string, MissionLedgerEvidenceRecord[]>,
  verificationGate: TaskVerificationGate | null,
  gateAudit: VerificationGateAuditRecord | null,
  attestation: MissionLedgerAttestationRecord | null,
  missionPack: MissionPackSnapshot | null
): VerificationEvidencePack {
  const missionId = workItem?.missionId ?? 'mission:unknown';
  const missionTitle = missionsById[missionId]?.title ?? missionId;
  const evidence = verificationRecord.evidenceRefs.flatMap((evidenceRef) => evidenceByRef[evidenceRef] ?? []);
  const typedEvidenceRefs = gateAudit?.typedEvidenceRefs ?? verificationRecord.evidenceRefs.map(toVerificationEvidenceRef);
  const proofCoverage =
    missionPack === null
      ? null
      : createMissionPackProofCoverage(missionPack, verificationRecord, workItem, attestation);

  return {
    packId: `evidence-pack:${verificationRecord.verificationId}`,
    missionId,
    missionTitle,
    itemId: verificationRecord.itemId,
    taskRef: workItem?.taskRef ?? null,
    verificationId: verificationRecord.verificationId,
    verificationRef: verificationRecord.verificationRef,
    actionId: workItem?.actionId ?? gateAudit?.actionId ?? null,
    status: verificationRecord.status,
    verdict: verificationRecord.verdict,
    checkedBy: verificationRecord.checkedBy,
    checkedAt: verificationRecord.checkedAt,
    traceId: verificationRecord.traceId,
    gateSequenceId: gateAudit?.sequenceId ?? null,
    correlationId: gateAudit?.correlationId ?? null,
    requestId: gateAudit?.requestId ?? null,
    idempotencyKey: gateAudit?.idempotencyKey ?? null,
    actorId: gateAudit?.actorId ?? null,
    actorRole: gateAudit?.actorRole ?? null,
    controlRefs: verificationRecord.policyRefs,
    controlsExecuted:
      gateAudit?.controlsExecuted ?? verificationGate?.verificationChain.controlsExecuted ?? stripControlRefPrefix(verificationRecord.policyRefs),
    evidenceRefs: verificationRecord.evidenceRefs,
    typedEvidenceRefs,
    unmetControlRefs: gateAudit?.unmetControlRefs ?? [],
    linkedSequenceIds: gateAudit?.linkedSequenceIds ?? [],
    evidence,
    externalReviews: verificationGate?.verificationChain.linkedExternalReviews ?? [],
    attestation,
    missionPack,
    proofCoverage
  };
}

function createVerificationGateAuditByRef(state: GameState): Record<string, VerificationGateAuditRecord> {
  const gatesByRef: Record<string, VerificationGateAuditRecord> = {};

  for (const workflowStep of state.recentWorkflowSteps) {
    if (workflowStep.sourceEventType !== 'verification_gate') {
      continue;
    }

    const verificationRef = readMetadataString(workflowStep.metadata, 'verificationRef');
    const actionId = readMetadataString(workflowStep.metadata, 'actionId');
    const typedEvidenceRefs = readTypedEvidenceRefs(workflowStep.metadata);
    const controlsExecuted = readMetadataStringArray(workflowStep.metadata, 'controlsExecuted');

    if (verificationRef === null || actionId === null || typedEvidenceRefs.length === 0 || controlsExecuted.length === 0) {
      continue;
    }

    const currentRecord = gatesByRef[verificationRef];
    if (currentRecord !== undefined && currentRecord.sequenceId >= workflowStep.sequenceId) {
      continue;
    }

    gatesByRef[verificationRef] = {
      sequenceId: workflowStep.sequenceId,
      actionId,
      verificationRef,
      traceId: workflowStep.traceId ?? null,
      taskId: workflowStep.taskId ?? null,
      controlsExecuted,
      typedEvidenceRefs,
      unmetControlRefs: readMetadataStringArray(workflowStep.metadata, 'unmetControls'),
      correlationId: readMetadataString(workflowStep.metadata, 'correlationId'),
      requestId: readMetadataString(workflowStep.metadata, 'requestId'),
      idempotencyKey: readMetadataString(workflowStep.metadata, 'idempotencyKey'),
      actorId: readMetadataString(workflowStep.metadata, 'actorId'),
      actorRole: readAgentRole(workflowStep.metadata.actorRole),
      linkedSequenceIds: collectLinkedSequenceIds(state, workflowStep.sequenceId, workflowStep.traceId ?? null, workflowStep.taskId ?? null)
    };
  }

  return gatesByRef;
}


function createMissionPackProofCoverage(
  missionPack: MissionPackSnapshot,
  verificationRecord: MissionLedgerVerificationRecord,
  workItem: MissionLedgerWorkItem | null,
  attestation: MissionLedgerAttestationRecord | null
): MissionPackProofCoverage {
  const actualProofRefs = new Set(
    uniqueStrings([
      verificationRecord.verificationRef,
      ...verificationRecord.evidenceRefs,
      ...verificationRecord.policyRefs,
      `evidence-pack:${verificationRecord.verificationId}`,
      ...(workItem?.actionId === null || workItem?.actionId === undefined ? [] : [`action:${workItem.actionId}`]),
      ...(attestation === null ? [] : [`attestation:${attestation.attestationId}`])
    ])
  );
  const satisfiedExpectedProofRefs = missionPack.expectedProofRefs.filter((proofRef) => actualProofRefs.has(proofRef));
  const missingExpectedProofRefs = missionPack.expectedProofRefs.filter((proofRef) => !actualProofRefs.has(proofRef));
  const expectedProofCount = missionPack.expectedProofRefs.length;

  return {
    expectedProofCount,
    satisfiedExpectedProofRefs,
    missingExpectedProofRefs,
    coverageRatio:
      expectedProofCount === 0 ? 0 : Number((satisfiedExpectedProofRefs.length / expectedProofCount).toFixed(2)),
    fullyCovered: expectedProofCount > 0 && missingExpectedProofRefs.length === 0
  };
}

function createEvidenceByRefIndex(
  evidenceRecords: readonly MissionLedgerEvidenceRecord[]
): Record<string, MissionLedgerEvidenceRecord[]> {
  const evidenceByRef: Record<string, MissionLedgerEvidenceRecord[]> = {};

  for (const evidenceRecord of evidenceRecords) {
    const currentEntries = evidenceByRef[evidenceRecord.evidenceRef] ?? [];
    currentEntries.push(evidenceRecord);
    evidenceByRef[evidenceRecord.evidenceRef] = currentEntries;
  }

  return Object.fromEntries(
    Object.entries(evidenceByRef).map(([evidenceRef, entries]) => [evidenceRef, entries.sort(compareEvidenceRecords)])
  );
}

function matchesVerificationEvidencePack(
  pack: VerificationEvidencePack,
  query: VerificationEvidencePackQuery
): boolean {
  if (query.missionId !== undefined && pack.missionId !== query.missionId) {
    return false;
  }

  if (query.itemId !== undefined && pack.itemId !== query.itemId) {
    return false;
  }

  if (query.verificationId !== undefined && pack.verificationId !== query.verificationId) {
    return false;
  }

  if (query.verificationRef !== undefined && pack.verificationRef !== query.verificationRef) {
    return false;
  }

  if (query.taskRef !== undefined && pack.taskRef !== query.taskRef) {
    return false;
  }

  if (query.evidenceRef !== undefined && !pack.evidenceRefs.includes(query.evidenceRef)) {
    return false;
  }

  return true;
}

function compareVerificationEvidencePacks(left: VerificationEvidencePack, right: VerificationEvidencePack): number {
  if (left.checkedAt !== right.checkedAt) {
    return right.checkedAt.localeCompare(left.checkedAt);
  }

  return left.verificationRef.localeCompare(right.verificationRef);
}

function compareEvidenceRecords(left: MissionLedgerEvidenceRecord, right: MissionLedgerEvidenceRecord): number {
  if (left.createdAt !== right.createdAt) {
    return right.createdAt.localeCompare(left.createdAt);
  }

  return left.evidenceId.localeCompare(right.evidenceId);
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))].sort((left, right) => left.localeCompare(right));
}

function collectLinkedSequenceIds(
  state: GameState,
  gateSequenceId: number,
  traceId: string | null,
  taskId: string | null
): number[] {
  const sequenceIds = new Set<number>([gateSequenceId]);

  for (const workflowStep of state.recentWorkflowSteps) {
    if (workflowStep.sequenceId > gateSequenceId) {
      continue;
    }

    if ((traceId !== null && workflowStep.traceId === traceId) || (taskId !== null && workflowStep.taskId === taskId)) {
      sequenceIds.add(workflowStep.sequenceId);
    }
  }

  for (const toolCall of state.recentToolCalls) {
    if (toolCall.sequenceId > gateSequenceId) {
      continue;
    }

    const toolTaskId = readToolTaskId(toolCall.params);
    if ((traceId !== null && toolCall.traceId === traceId) || (taskId !== null && toolTaskId === taskId)) {
      sequenceIds.add(toolCall.sequenceId);
    }
  }

  return [...sequenceIds].sort((left, right) => left - right);
}

function readTypedEvidenceRefs(metadata: Record<string, unknown>): VerificationEvidenceRef[] {
  const typedEvidenceRefs = metadata.typedEvidenceRefs;

  if (Array.isArray(typedEvidenceRefs)) {
    const parsedEvidenceRefs = typedEvidenceRefs
      .map((value) => {
        if (typeof value !== 'object' || value === null || Array.isArray(value)) {
          return null;
        }

        const kind = readMetadataString(value, 'kind');
        const ref = readMetadataString(value, 'ref');

        if (
          ref === null ||
          (kind !== 'artifact' && kind !== 'coverage' && kind !== 'log' && kind !== 'screenshot' && kind !== 'test')
        ) {
          return null;
        }

        return { kind, ref } as VerificationEvidenceRef;
      })
      .filter((value): value is VerificationEvidenceRef => value !== null);

    if (parsedEvidenceRefs.length > 0) {
      return parsedEvidenceRefs;
    }
  }

  return readMetadataStringArray(metadata, 'evidenceRefs').map(toVerificationEvidenceRef);
}

function readMetadataString(metadata: Record<string, unknown>, key: string): string | null {
  const value = metadata[key];

  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim();
  return normalized.length === 0 ? null : normalized;
}

function readMetadataStringArray(metadata: Record<string, unknown>, key: string): string[] {
  const value = metadata[key];

  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function readToolTaskId(params: Record<string, unknown>): string | null {
  const taskId = params.task_id;
  if (typeof taskId === 'string' && taskId.trim().length > 0) {
    return taskId.trim();
  }

  const alternateTaskId = params.taskId;
  return typeof alternateTaskId === 'string' && alternateTaskId.trim().length > 0 ? alternateTaskId.trim() : null;
}

function readAgentRole(value: unknown): AgentRole | null {
  return value === 'agent' || value === 'orchestrator' || value === 'spectator' ? value : null;
}

function stripControlRefPrefix(controlRefs: readonly string[]): string[] {
  return controlRefs.map((controlRef) => controlRef.replace(/^control:\/\//u, ''));
}

function toVerificationEvidenceRef(ref: string): VerificationEvidenceRef {
  return {
    kind: inferVerificationEvidenceKind(ref),
    ref
  };
}

function inferVerificationEvidenceKind(ref: string): VerificationEvidenceRef['kind'] {
  const normalized = ref.trim().toLowerCase();

  if (normalized.startsWith('tests://')) {
    return 'test';
  }

  if (normalized.startsWith('log://')) {
    return 'log';
  }

  if (normalized.startsWith('coverage://')) {
    return 'coverage';
  }

  if (normalized.startsWith('screenshot://')) {
    return 'screenshot';
  }

  return 'artifact';
}