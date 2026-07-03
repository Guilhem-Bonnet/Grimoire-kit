import type { JsonValue, TaskStatus } from '../contracts/events';

import type { BoardDecisionCard } from './board-view';
import type { GameState, ToolCallLogEntry, WorkflowStepLogEntry } from './game-state';
import { createTaskView, type TaskInspectionView } from './task-view';
import { createVerificationView, type TaskVerificationGate } from './verification-view';

export const MISSION_LEDGER_MISSION_STATUS_ORDER = [
  'blocked',
  'verifying',
  'active',
  'ready',
  'planned',
  'completed',
  'archived'
] as const;

export const MISSION_LEDGER_WORK_ITEM_STATUS_ORDER = [
  'blocked',
  'review',
  'in_progress',
  'ready',
  'backlog',
  'done',
  'cancelled'
] as const;

export const MISSION_LEDGER_EVIDENCE_KIND_ORDER = [
  'test_report',
  'coverage_report',
  'screenshot',
  'log_excerpt',
  'artifact',
  'manual_assertion'
] as const;

export const MISSION_LEDGER_VERIFICATION_STATUS_ORDER = [
  'queued',
  'verifying',
  'needs_work',
  'accepted',
  'rejected'
] as const;

export const MISSION_LEDGER_ESCALATION_SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'] as const;

export type MissionLedgerMissionStatus = (typeof MISSION_LEDGER_MISSION_STATUS_ORDER)[number];
export type MissionLedgerWorkItemType =
  | 'task'
  | 'workflow_step'
  | 'verification_gate'
  | 'incident'
  | 'decision'
  | 'handoff';
export type MissionLedgerWorkItemStatus = (typeof MISSION_LEDGER_WORK_ITEM_STATUS_ORDER)[number];
export type MissionLedgerPriority = 'low' | 'medium' | 'high';
export type MissionLedgerDependencyType = 'blocks' | 'relates_to' | 'supersedes' | 'requires_verification_of';
export type MissionLedgerDependencyStatus = 'active' | 'satisfied';
export type MissionLedgerAssignmentRole = 'owner' | 'handoff';
export type MissionLedgerAssignmentStatus = 'active' | 'released';
export type MissionLedgerEvidenceKind = (typeof MISSION_LEDGER_EVIDENCE_KIND_ORDER)[number];
export type MissionLedgerVerificationStatus = (typeof MISSION_LEDGER_VERIFICATION_STATUS_ORDER)[number];
export type MissionLedgerVerificationVerdict = 'pass' | 'fail' | 'warn' | 'inconclusive';
export type MissionLedgerEscalationSeverity = (typeof MISSION_LEDGER_ESCALATION_SEVERITY_ORDER)[number];
export type MissionLedgerEscalationStatus = 'open' | 'resolved';
export type MissionLedgerAttestationType = 'verification_attestation' | 'review_attestation' | 'compliance_note';

export interface MissionLedgerMission {
  missionId: string;
  title: string;
  status: MissionLedgerMissionStatus;
  priority: MissionLedgerPriority;
  owner: string;
  createdAt: string;
  updatedAt: string;
  sourceRefs: readonly string[];
  labels: readonly string[];
  traceRefs: readonly string[];
  itemIds: readonly string[];
  blockedItemIds: readonly string[];
  verifyingItemIds: readonly string[];
  activeItemCount: number;
  completedItemCount: number;
}

export interface MissionLedgerWorkItem {
  itemId: string;
  missionId: string;
  title: string;
  type: MissionLedgerWorkItemType;
  status: MissionLedgerWorkItemStatus;
  priority: MissionLedgerPriority;
  actor: string;
  source: string;
  requestId: string;
  idempotencyKey: string;
  createdAt: string;
  updatedAt: string;
  traceId: string | null;
  taskRef: string | null;
  sequenceId: number | null;
  actionId: string | null;
  verificationRef: string | null;
  evidenceRefs: readonly string[];
  controlRefs: readonly string[];
}

export interface MissionLedgerDependency {
  dependencyId: string;
  fromItemId: string;
  toItemId: string;
  type: MissionLedgerDependencyType;
  status: MissionLedgerDependencyStatus;
}

export interface MissionLedgerWorkflowInstanceRef {
  workflowInstanceId: string;
  itemId: string;
  recipeRef: string;
  status: MissionLedgerWorkItemStatus;
  checkpointRef: string | null;
  currentStepId: string | null;
  traceId: string | null;
}

export interface MissionLedgerAssignment {
  assignmentId: string;
  itemId: string;
  assignee: string;
  role: MissionLedgerAssignmentRole;
  status: MissionLedgerAssignmentStatus;
  assignedAt: string;
  releasedAt: string | null;
}

export interface MissionLedgerEvidenceRecord {
  evidenceId: string;
  itemId: string;
  evidenceRef: string;
  kind: MissionLedgerEvidenceKind;
  summary: string;
  source: string;
  createdAt: string;
  traceId: string | null;
  metadata: Record<string, JsonValue>;
}

export interface MissionLedgerVerificationRecord {
  verificationId: string;
  itemId: string;
  verificationRef: string;
  status: MissionLedgerVerificationStatus;
  verdict: MissionLedgerVerificationVerdict;
  checkedBy: string;
  checkedAt: string;
  evidenceRefs: readonly string[];
  policyRefs: readonly string[];
  traceId: string | null;
}

export interface MissionLedgerEscalationRecord {
  escalationId: string;
  itemId: string;
  severity: MissionLedgerEscalationSeverity;
  reason: string;
  openedAt: string;
  openedBy: string;
  status: MissionLedgerEscalationStatus;
  contextRefs: readonly string[];
}

export interface MissionLedgerAttestationRecord {
  attestationId: string;
  verificationId: string;
  subjectRef: string;
  author: string;
  type: MissionLedgerAttestationType;
  summary: string;
  createdAt: string;
  metadata: Record<string, JsonValue>;
}

export interface MissionLedgerSummary {
  missionCount: number;
  blockedMissionCount: number;
  verifyingMissionCount: number;
  activeMissionCount: number;
  completedMissionCount: number;
  workItemCount: number;
  verificationCount: number;
  evidenceCount: number;
  openEscalationCount: number;
}

export interface MissionLedgerView {
  protocolVersion: string;
  lastSequenceId: number;
  missions: readonly MissionLedgerMission[];
  workItems: readonly MissionLedgerWorkItem[];
  dependencies: readonly MissionLedgerDependency[];
  workflowInstances: readonly MissionLedgerWorkflowInstanceRef[];
  assignments: readonly MissionLedgerAssignment[];
  evidenceRecords: readonly MissionLedgerEvidenceRecord[];
  verificationRecords: readonly MissionLedgerVerificationRecord[];
  escalationRecords: readonly MissionLedgerEscalationRecord[];
  attestationRecords: readonly MissionLedgerAttestationRecord[];
  summary: MissionLedgerSummary;
}

const MISSION_STATUS_RANK: Record<MissionLedgerMissionStatus, number> = {
  blocked: 0,
  verifying: 1,
  active: 2,
  ready: 3,
  planned: 4,
  completed: 5,
  archived: 6
};

const WORK_ITEM_STATUS_RANK: Record<MissionLedgerWorkItemStatus, number> = {
  blocked: 0,
  review: 1,
  in_progress: 2,
  ready: 3,
  backlog: 4,
  done: 5,
  cancelled: 6
};

const VERIFICATION_STATUS_RANK: Record<MissionLedgerVerificationStatus, number> = {
  queued: 0,
  verifying: 1,
  needs_work: 2,
  accepted: 3,
  rejected: 4
};

export function createMissionLedgerView(state: GameState): MissionLedgerView {
  const taskView = createTaskView(state);
  const verificationView = createVerificationView(state);
  const verificationByTaskId = Object.fromEntries(verificationView.tasks.map((task) => [task.taskId, task]));
  const missions: MissionLedgerMission[] = [];
  const workItems: MissionLedgerWorkItem[] = [];
  const dependencies: MissionLedgerDependency[] = [];
  const workflowInstances: MissionLedgerWorkflowInstanceRef[] = [];
  const assignments: MissionLedgerAssignment[] = [];
  const evidenceRecordsById = new Map<string, MissionLedgerEvidenceRecord>();
  const verificationRecords: MissionLedgerVerificationRecord[] = [];
  const escalationRecords: MissionLedgerEscalationRecord[] = [];
  const attestationRecords: MissionLedgerAttestationRecord[] = [];

  for (const taskInspection of taskView.tasks) {
    const verificationGate = verificationByTaskId[taskInspection.task.id] ?? null;
    const missionId = `mission:task:${taskInspection.task.id}`;
    const taskItemId = `task:${taskInspection.task.id}`;
    const verificationItemId = `verification:${taskInspection.task.id}`;
    const createdAt = deriveCreatedAt(taskInspection, state.hydratedAt);
    const updatedAt = taskInspection.lastActivityAt ?? createdAt;

    const taskItem = createTaskWorkItem(missionId, taskInspection, createdAt, updatedAt);
    const missionItemIds = [taskItem.itemId];
    workItems.push(taskItem);

    const stepItems = taskInspection.recentWorkflowSteps.map((step) => createWorkflowStepWorkItem(missionId, taskInspection.task.id, step));
    for (const stepItem of stepItems) {
      workItems.push(stepItem);
      missionItemIds.push(stepItem.itemId);
      dependencies.push({
        dependencyId: `dep:${taskItemId}:${stepItem.itemId}`,
        fromItemId: taskItemId,
        toItemId: stepItem.itemId,
        type: 'relates_to',
        status: 'satisfied'
      });
    }

    const verificationItem = createVerificationWorkItem(missionId, taskInspection, verificationGate, verificationItemId, updatedAt);
    if (verificationItem !== null) {
      workItems.push(verificationItem);
      missionItemIds.push(verificationItem.itemId);
      dependencies.push({
        dependencyId: `dep:${taskItemId}:${verificationItem.itemId}`,
        fromItemId: taskItemId,
        toItemId: verificationItem.itemId,
        type: 'requires_verification_of',
        status: verificationGate?.isReadyForDone === true ? 'satisfied' : 'active'
      });
    }

    workflowInstances.push(...createWorkflowInstances(taskInspection, taskItem.itemId, verificationGate));
    assignments.push(...createAssignments(taskInspection, taskItem.itemId, createdAt));

    for (const toolCall of taskInspection.recentToolCalls) {
      const evidence = createToolCallEvidence(taskItem.itemId, toolCall);
      evidenceRecordsById.set(evidence.evidenceId, evidence);
    }

    for (const decisionCard of taskInspection.decisionCards) {
      const evidence = createDecisionCardEvidence(taskItem.itemId, decisionCard);
      evidenceRecordsById.set(evidence.evidenceId, evidence);
    }

    if (verificationGate !== null && verificationItem !== null) {
      const verificationRecord = createVerificationRecord(verificationItem.itemId, taskInspection, verificationGate, updatedAt);
      if (verificationRecord !== null) {
        verificationRecords.push(verificationRecord);
        attestationRecords.push(createAttestationRecord(taskInspection.task.id, verificationRecord));
        verificationGate.verificationChain.evidenceRefs.forEach((evidenceRef, index) => {
          const evidence = createVerificationEvidence(verificationItem.itemId, verificationGate, evidenceRef, index, updatedAt);
          evidenceRecordsById.set(evidence.evidenceId, evidence);
        });
      }
    }

    escalationRecords.push(...createTaskEscalations(taskInspection, taskItem.itemId, verificationItemId, verificationGate, updatedAt));

    const missionWorkItems = [taskItem, ...stepItems, ...(verificationItem === null ? [] : [verificationItem])];
    const missionStatus = deriveMissionStatus(taskInspection.task.status, verificationGate, escalationRecords, missionItemIds);
    missions.push({
      missionId,
      title: taskInspection.task.title,
      status: missionStatus,
      priority: deriveMissionPriority(missionStatus, taskInspection.alerts.length),
      owner: taskInspection.assigneeAgentId ?? 'unassigned',
      createdAt,
      updatedAt,
      sourceRefs: [`task:${taskInspection.task.id}`],
      labels: uniqueStrings([
        ...taskInspection.alerts.map((alert) => alert.code.toLowerCase()),
        ...taskInspection.traceIds.map((traceId) => `trace:${traceId}`)
      ]),
      traceRefs: uniqueStrings([
        ...taskInspection.traceIds,
        ...(verificationGate?.verificationChain.traceId === null || verificationGate?.verificationChain.traceId === undefined
          ? []
          : [verificationGate.verificationChain.traceId])
      ]),
      itemIds: missionItemIds.sort(),
      blockedItemIds: missionWorkItems.filter((item) => item.status === 'blocked').map((item) => item.itemId),
      verifyingItemIds: missionWorkItems
        .filter((item) => item.status === 'review' || item.status === 'in_progress')
        .map((item) => item.itemId),
      activeItemCount: missionWorkItems.filter((item) => item.status !== 'done' && item.status !== 'cancelled').length,
      completedItemCount: missionWorkItems.filter((item) => item.status === 'done').length
    });
  }

  const evidenceRecords = Array.from(evidenceRecordsById.values()).sort(compareEvidenceRecords);
  const sortedEscalations = escalationRecords.sort(compareEscalationRecords);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    missions: missions.sort(compareMissions),
    workItems: workItems.sort(compareWorkItems),
    dependencies: dependencies.sort((left, right) => left.dependencyId.localeCompare(right.dependencyId)),
    workflowInstances: workflowInstances.sort(compareWorkflowInstances),
    assignments: assignments.sort(compareAssignments),
    evidenceRecords,
    verificationRecords: verificationRecords.sort(compareVerificationRecords),
    escalationRecords: sortedEscalations,
    attestationRecords: attestationRecords.sort((left, right) => right.createdAt.localeCompare(left.createdAt)),
    summary: {
      missionCount: missions.length,
      blockedMissionCount: missions.filter((mission) => mission.status === 'blocked').length,
      verifyingMissionCount: missions.filter((mission) => mission.status === 'verifying').length,
      activeMissionCount: missions.filter((mission) => mission.status === 'active').length,
      completedMissionCount: missions.filter((mission) => mission.status === 'completed').length,
      workItemCount: workItems.length,
      verificationCount: verificationRecords.length,
      evidenceCount: evidenceRecords.length,
      openEscalationCount: sortedEscalations.filter((record) => record.status === 'open').length
    }
  };
}

function createTaskWorkItem(
  missionId: string,
  taskInspection: TaskInspectionView,
  createdAt: string,
  updatedAt: string
): MissionLedgerWorkItem {
  const requestId =
    readLatestMetadataString(taskInspection.recentWorkflowSteps, ['requestId', 'request_id', 'correlationId', 'correlation_id']) ??
    `task:${taskInspection.task.id}`;

  return {
    itemId: `task:${taskInspection.task.id}`,
    missionId,
    title: taskInspection.task.title,
    type: 'task',
    status: mapTaskStatus(taskInspection.task.status),
    priority: deriveMissionPriorityFromTask(taskInspection.task.status, taskInspection.alerts.length),
    actor: taskInspection.assigneeAgentId ?? 'unassigned',
    source: 'runtime/task',
    requestId,
    idempotencyKey: `task:${taskInspection.task.id}`,
    createdAt,
    updatedAt,
    traceId: taskInspection.traceIds[0] ?? null,
    taskRef: taskInspection.task.id,
    sequenceId: taskInspection.recentEntries[0]?.sequenceId ?? null,
    actionId: null,
    verificationRef: null,
    evidenceRefs: [],
    controlRefs: []
  };
}

function createWorkflowStepWorkItem(
  missionId: string,
  taskId: string,
  workflowStep: WorkflowStepLogEntry
): MissionLedgerWorkItem {
  const actionId = readRecordString(workflowStep.metadata, ['actionId', 'action_id']);
  const verificationRef = readRecordString(workflowStep.metadata, ['verificationRef', 'verification_ref']);
  const evidenceRefs = readRecordStringList(workflowStep.metadata, ['evidenceRefs', 'evidence_refs']);
  const controlRefs = readRecordStringList(workflowStep.metadata, ['controlsExecuted', 'controls_executed', 'controls']).map(
    (control) => `control://${control}`
  );

  return {
    itemId: `workflow:${workflowStep.sequenceId}`,
    missionId,
    title: workflowStep.step,
    type: inferWorkflowStepType(workflowStep),
    status: mapWorkflowStepStatus(workflowStep),
    priority: 'medium',
    actor: workflowStep.agentId ?? 'system',
    source: `runtime/workflow:${workflowStep.sourceEventType}`,
    requestId:
      readRecordString(workflowStep.metadata, ['requestId', 'request_id', 'correlationId', 'correlation_id']) ??
      `workflow:${workflowStep.sequenceId}`,
    idempotencyKey: `workflow:${workflowStep.sequenceId}`,
    createdAt: workflowStep.timestamp,
    updatedAt: workflowStep.timestamp,
    traceId: workflowStep.traceId ?? null,
    taskRef: taskId,
    sequenceId: workflowStep.sequenceId,
    actionId,
    verificationRef,
    evidenceRefs,
    controlRefs
  };
}

function createVerificationWorkItem(
  missionId: string,
  taskInspection: TaskInspectionView,
  verificationGate: TaskVerificationGate | null,
  verificationItemId: string,
  updatedAt: string
): MissionLedgerWorkItem | null {
  if (verificationGate === null && taskInspection.task.status !== 'done' && taskInspection.task.status !== 'review') {
    return null;
  }

  const verificationStatus = deriveVerificationWorkItemStatus(taskInspection.task.status, verificationGate);

  return {
    itemId: verificationItemId,
    missionId,
    title: `Verification gate for ${taskInspection.task.title}`,
    type: 'verification_gate',
    status: verificationStatus,
    priority: verificationStatus === 'blocked' ? 'high' : 'medium',
    actor: taskInspection.assigneeAgentId ?? 'verification',
    source: 'runtime/verification',
    requestId: verificationGate?.verificationChain.actionId ?? `verification:${taskInspection.task.id}`,
    idempotencyKey: verificationGate?.verificationChain.verificationRef ?? verificationItemId,
    createdAt: updatedAt,
    updatedAt,
    traceId: verificationGate?.verificationChain.traceId ?? taskInspection.traceIds[0] ?? null,
    taskRef: taskInspection.task.id,
    sequenceId: null,
    actionId: verificationGate?.verificationChain.actionId ?? null,
    verificationRef: verificationGate?.verificationChain.verificationRef ?? null,
    evidenceRefs: verificationGate?.verificationChain.evidenceRefs ?? [],
    controlRefs: (verificationGate?.verificationChain.controlsExecuted ?? []).map((control) => `control://${control}`)
  };
}

function createWorkflowInstances(
  taskInspection: TaskInspectionView,
  itemId: string,
  verificationGate: TaskVerificationGate | null
): MissionLedgerWorkflowInstanceRef[] {
  const traceIds = uniqueStrings([
    ...taskInspection.traceIds,
    ...(verificationGate?.verificationChain.traceId === null || verificationGate?.verificationChain.traceId === undefined
      ? []
      : [verificationGate.verificationChain.traceId])
  ]);

  return traceIds.map((traceId) => {
    const steps = taskInspection.recentWorkflowSteps.filter((step) => step.traceId === traceId);
    const latestStep = steps[0] ?? null;
    const routingStep = steps.find((step) => step.sourceEventType === 'routing') ?? latestStep;

    return {
      workflowInstanceId: `workflow-instance:${taskInspection.task.id}:${traceId}`,
      itemId,
      recipeRef: routingStep === null ? 'runtime/workflow' : `runtime/${routingStep.sourceEventType}`,
      status: latestStep === null ? mapTaskStatus(taskInspection.task.status) : mapWorkflowStepStatus(latestStep),
      checkpointRef: latestStep?.step ?? null,
      currentStepId: latestStep === null ? null : `workflow:${latestStep.sequenceId}`,
      traceId
    };
  });
}

function createAssignments(taskInspection: TaskInspectionView, itemId: string, assignedAt: string): MissionLedgerAssignment[] {
  const assignees = uniqueStrings([
    ...(taskInspection.assigneeAgentId === null ? [] : [taskInspection.assigneeAgentId]),
    ...taskInspection.handoffAgentIds.filter((agentId) => agentId !== taskInspection.assigneeAgentId)
  ]);

  return assignees.map((assignee, index) => ({
    assignmentId: `assignment:${taskInspection.task.id}:${assignee}`,
    itemId,
    assignee,
    role: index === 0 && assignee === taskInspection.assigneeAgentId ? 'owner' : 'handoff',
    status: taskInspection.task.status === 'done' ? 'released' : 'active',
    assignedAt,
    releasedAt: taskInspection.task.status === 'done' ? taskInspection.lastActivityAt ?? assignedAt : null
  }));
}

function createToolCallEvidence(itemId: string, toolCall: ToolCallLogEntry): MissionLedgerEvidenceRecord {
  const path = readRecordString(toolCall.params, ['path']);
  const evidenceRef = path === null ? `tool-call:${toolCall.sequenceId}` : `artifact://${path}`;

  return {
    evidenceId: `evidence:tool:${toolCall.sequenceId}`,
    itemId,
    evidenceRef,
    kind: path === null ? 'log_excerpt' : 'artifact',
    summary: path === null ? `${toolCall.tool} executed` : `${toolCall.tool} touched ${path}`,
    source: `tool_call:${toolCall.sourceEventType}`,
    createdAt: toolCall.timestamp,
    traceId: toolCall.traceId ?? null,
    metadata: cloneJsonRecord(toolCall.params)
  };
}

function createDecisionCardEvidence(itemId: string, decisionCard: BoardDecisionCard): MissionLedgerEvidenceRecord {
  return {
    evidenceId: `evidence:decision:${decisionCard.sequenceId}`,
    itemId,
    evidenceRef: `decision-card:${decisionCard.sequenceId}`,
    kind: 'manual_assertion',
    summary: decisionCard.title,
    source: `decision_card:${decisionCard.sourceEventType}`,
    createdAt: decisionCard.timestamp,
    traceId: decisionCard.traceId,
    metadata: {
      detail: decisionCard.detail,
      evidenceCount: decisionCard.evidence.length,
      supportingToolCalls: decisionCard.supportingToolCalls.map((toolCall) => toolCall.tool)
    }
  };
}

function createVerificationEvidence(
  itemId: string,
  verificationGate: TaskVerificationGate,
  evidenceRef: string,
  index: number,
  createdAt: string
): MissionLedgerEvidenceRecord {
  return {
    evidenceId: `evidence:verification:${itemId}:${index}`,
    itemId,
    evidenceRef,
    kind: inferEvidenceKind(evidenceRef),
    summary: `Verification evidence ${index + 1}`,
    source: 'verification_chain',
    createdAt,
    traceId: verificationGate.verificationChain.traceId,
    metadata: {
      origin: 'verification_chain'
    }
  };
}

function createVerificationRecord(
  itemId: string,
  taskInspection: TaskInspectionView,
  verificationGate: TaskVerificationGate,
  checkedAt: string
): MissionLedgerVerificationRecord | null {
  const verificationRef = verificationGate.verificationChain.verificationRef;
  if (verificationRef === null) {
    return null;
  }

  const verdict = normalizeVerificationVerdict(verificationGate.verificationChain.verdict);
  const status = deriveVerificationStatus(verificationGate, verdict);

  return {
    verificationId: `verification-record:${taskInspection.task.id}`,
    itemId,
    verificationRef,
    status,
    verdict,
    checkedBy: taskInspection.assigneeAgentId ?? 'verification',
    checkedAt,
    evidenceRefs: verificationGate.verificationChain.evidenceRefs,
    policyRefs: verificationGate.verificationChain.controlsExecuted.map((control) => `control://${control}`),
    traceId: verificationGate.verificationChain.traceId
  };
}

function createAttestationRecord(
  taskId: string,
  verificationRecord: MissionLedgerVerificationRecord
): MissionLedgerAttestationRecord {
  return {
    attestationId: `attestation:${verificationRecord.verificationId}`,
    verificationId: verificationRecord.verificationId,
    subjectRef: `task:${taskId}`,
    author: verificationRecord.checkedBy,
    type: 'verification_attestation',
    summary: `Verification ${verificationRecord.verdict.toUpperCase()} for ${verificationRecord.verificationRef}`,
    createdAt: verificationRecord.checkedAt,
    metadata: {
      status: verificationRecord.status,
      traceId: verificationRecord.traceId ?? 'unscoped'
    }
  };
}

function createTaskEscalations(
  taskInspection: TaskInspectionView,
  taskItemId: string,
  verificationItemId: string,
  verificationGate: TaskVerificationGate | null,
  openedAt: string
): MissionLedgerEscalationRecord[] {
  const records = taskInspection.alerts.map((alert) => ({
    escalationId: `escalation:${taskInspection.task.id}:${alert.code}`,
    itemId: taskItemId,
    severity: mapAlertSeverity(alert.level, alert.code),
    reason: alert.message,
    openedAt,
    openedBy: 'runtime-observability',
    status: 'open' as const,
    contextRefs: uniqueStrings([
      ...taskInspection.traceIds,
      `task:${taskInspection.task.id}`
    ])
  }));

  if (verificationGate !== null && !verificationGate.isReadyForDone) {
    records.push({
      escalationId: `escalation:${taskInspection.task.id}:verification`,
      itemId: verificationItemId,
      severity: verificationGate.unmetRequirementCodes.some((code) => code.includes('CRITICAL') || code.includes('BLOCKING'))
        ? 'high'
        : 'medium',
      reason: `Verification gate blocked by ${verificationGate.unmetRequirementCodes.join(', ')}`,
      openedAt,
      openedBy: 'verification-gate',
      status: 'open',
      contextRefs: uniqueStrings([
        ...(verificationGate.verificationChain.traceId === null ? [] : [verificationGate.verificationChain.traceId]),
        ...(verificationGate.verificationChain.verificationRef === null
          ? []
          : [verificationGate.verificationChain.verificationRef]),
        ...verificationGate.verificationChain.evidenceRefs
      ])
    });
  }

  return records;
}

function deriveMissionStatus(
  taskStatus: TaskStatus,
  verificationGate: TaskVerificationGate | null,
  escalations: readonly MissionLedgerEscalationRecord[],
  itemIds: readonly string[]
): MissionLedgerMissionStatus {
  const relevantEscalations = escalations.filter((record) => itemIds.includes(record.itemId) && record.status === 'open');
  if (relevantEscalations.some((record) => record.severity === 'critical' || record.severity === 'high')) {
    return 'blocked';
  }

  if (taskStatus === 'backlog') {
    return 'planned';
  }

  if (taskStatus === 'todo') {
    return 'ready';
  }

  if (taskStatus === 'in_progress') {
    return 'active';
  }

  if (taskStatus === 'review') {
    return 'verifying';
  }

  if (taskStatus === 'done') {
    return verificationGate === null || verificationGate.isReadyForDone ? 'completed' : 'blocked';
  }

  return 'archived';
}

function deriveMissionPriority(status: MissionLedgerMissionStatus, alertCount: number): MissionLedgerPriority {
  if (status === 'blocked') {
    return 'high';
  }

  if (status === 'verifying' || status === 'active' || alertCount > 0) {
    return 'medium';
  }

  return 'low';
}

function deriveMissionPriorityFromTask(taskStatus: TaskStatus, alertCount: number): MissionLedgerPriority {
  if (taskStatus === 'review' || alertCount > 0) {
    return 'high';
  }

  if (taskStatus === 'in_progress' || taskStatus === 'done') {
    return 'medium';
  }

  return 'low';
}

function deriveVerificationStatus(
  verificationGate: TaskVerificationGate,
  verdict: MissionLedgerVerificationVerdict
): MissionLedgerVerificationStatus {
  if (verdict === 'pass' && verificationGate.isReadyForDone) {
    return 'accepted';
  }

  if (verdict === 'fail') {
    return 'rejected';
  }

  if (!verificationGate.isReadyForDone || verdict === 'warn') {
    return 'needs_work';
  }

  return 'verifying';
}

function deriveVerificationWorkItemStatus(
  taskStatus: TaskStatus,
  verificationGate: TaskVerificationGate | null
): MissionLedgerWorkItemStatus {
  if (verificationGate?.verificationChain.verdict === 'PASS' && verificationGate.isReadyForDone) {
    return 'done';
  }

  if (verificationGate?.verificationChain.verdict === 'FAIL') {
    return 'blocked';
  }

  if (taskStatus === 'review' || taskStatus === 'done') {
    return verificationGate?.isReadyForDone === true ? 'done' : 'review';
  }

  return 'in_progress';
}

function inferWorkflowStepType(workflowStep: WorkflowStepLogEntry): MissionLedgerWorkItemType {
  if (workflowStep.sourceEventType === 'decision') {
    return 'decision';
  }

  if (workflowStep.sourceEventType === 'verification_gate') {
    return 'verification_gate';
  }

  if (/handoff/iu.test(workflowStep.step) || /handoff/iu.test(workflowStep.detail)) {
    return 'handoff';
  }

  return 'workflow_step';
}

function mapTaskStatus(taskStatus: TaskStatus): MissionLedgerWorkItemStatus {
  switch (taskStatus) {
    case 'backlog':
      return 'backlog';
    case 'todo':
      return 'ready';
    case 'in_progress':
      return 'in_progress';
    case 'review':
      return 'review';
    case 'done':
      return 'done';
  }
}

function mapWorkflowStepStatus(workflowStep: WorkflowStepLogEntry): MissionLedgerWorkItemStatus {
  const verdict = normalizeVerificationVerdict(readRecordString(workflowStep.metadata, ['verdict', 'result']));
  if (workflowStep.sourceEventType === 'verification_gate') {
    if (verdict === 'pass') {
      return 'done';
    }

    if (verdict === 'fail') {
      return 'blocked';
    }

    return 'review';
  }

  return 'done';
}

function normalizeVerificationVerdict(
  verdict: TaskVerificationGate['verificationChain']['verdict'] | string | null
): MissionLedgerVerificationVerdict {
  if (typeof verdict !== 'string') {
    return 'inconclusive';
  }

  switch (verdict.toLowerCase()) {
    case 'pass':
      return 'pass';
    case 'fail':
      return 'fail';
    case 'warn':
      return 'warn';
    default:
      return 'inconclusive';
  }
}

function inferEvidenceKind(evidenceRef: string): MissionLedgerEvidenceKind {
  const normalized = evidenceRef.toLowerCase();
  if (normalized.includes('coverage')) {
    return 'coverage_report';
  }

  if (normalized.includes('screenshot') || normalized.endsWith('.png') || normalized.endsWith('.jpg')) {
    return 'screenshot';
  }

  if (normalized.includes('test')) {
    return 'test_report';
  }

  if (normalized.includes('log')) {
    return 'log_excerpt';
  }

  return 'artifact';
}

function mapAlertSeverity(
  level: 'warning' | 'info',
  code: string
): MissionLedgerEscalationSeverity {
  if (code.includes('MISSING') || code.includes('WITHOUT_EVIDENCE')) {
    return 'high';
  }

  if (level === 'warning') {
    return 'medium';
  }

  return 'low';
}

function deriveCreatedAt(taskInspection: TaskInspectionView, hydratedAt: string | null): string {
  const oldestEntry = taskInspection.recentEntries[taskInspection.recentEntries.length - 1];
  return oldestEntry?.timestamp ?? taskInspection.lastActivityAt ?? hydratedAt ?? new Date(0).toISOString();
}

function readLatestMetadataString(
  entries: readonly WorkflowStepLogEntry[],
  keys: readonly string[]
): string | null {
  for (const entry of entries) {
    const value = readRecordString(entry.metadata, keys);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

function readRecordString(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value !== 'string') {
      continue;
    }

    const normalized = value.trim();
    if (normalized.length > 0) {
      return normalized;
    }
  }

  return null;
}

function readRecordStringList(record: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (!Array.isArray(value)) {
      continue;
    }

    const normalized = value
      .filter((entry): entry is string => typeof entry === 'string')
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
    if (normalized.length > 0) {
      return [...new Set(normalized)];
    }
  }

  return [];
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))].sort((left, right) => left.localeCompare(right));
}

function cloneJsonRecord(record: Record<string, JsonValue>): Record<string, JsonValue> {
  return JSON.parse(JSON.stringify(record)) as Record<string, JsonValue>;
}

function compareMissions(left: MissionLedgerMission, right: MissionLedgerMission): number {
  if (left.status !== right.status) {
    return MISSION_STATUS_RANK[left.status] - MISSION_STATUS_RANK[right.status];
  }

  if (left.updatedAt !== right.updatedAt) {
    return right.updatedAt.localeCompare(left.updatedAt);
  }

  return left.title.localeCompare(right.title);
}

function compareWorkItems(left: MissionLedgerWorkItem, right: MissionLedgerWorkItem): number {
  if (left.status !== right.status) {
    return WORK_ITEM_STATUS_RANK[left.status] - WORK_ITEM_STATUS_RANK[right.status];
  }

  if (left.updatedAt !== right.updatedAt) {
    return right.updatedAt.localeCompare(left.updatedAt);
  }

  if (left.sequenceId !== right.sequenceId) {
    return (right.sequenceId ?? -1) - (left.sequenceId ?? -1);
  }

  return left.title.localeCompare(right.title);
}

function compareWorkflowInstances(
  left: MissionLedgerWorkflowInstanceRef,
  right: MissionLedgerWorkflowInstanceRef
): number {
  if (left.status !== right.status) {
    return WORK_ITEM_STATUS_RANK[left.status] - WORK_ITEM_STATUS_RANK[right.status];
  }

  return left.workflowInstanceId.localeCompare(right.workflowInstanceId);
}

function compareAssignments(left: MissionLedgerAssignment, right: MissionLedgerAssignment): number {
  if (left.status !== right.status) {
    return left.status === 'active' ? -1 : 1;
  }

  return left.assignmentId.localeCompare(right.assignmentId);
}

function compareEvidenceRecords(
  left: MissionLedgerEvidenceRecord,
  right: MissionLedgerEvidenceRecord
): number {
  if (left.createdAt !== right.createdAt) {
    return right.createdAt.localeCompare(left.createdAt);
  }

  return left.evidenceId.localeCompare(right.evidenceId);
}

function compareVerificationRecords(
  left: MissionLedgerVerificationRecord,
  right: MissionLedgerVerificationRecord
): number {
  if (left.status !== right.status) {
    return VERIFICATION_STATUS_RANK[left.status] - VERIFICATION_STATUS_RANK[right.status];
  }

  if (left.checkedAt !== right.checkedAt) {
    return right.checkedAt.localeCompare(left.checkedAt);
  }

  return left.verificationId.localeCompare(right.verificationId);
}

function compareEscalationRecords(
  left: MissionLedgerEscalationRecord,
  right: MissionLedgerEscalationRecord
): number {
  if (left.severity !== right.severity) {
    return MISSION_LEDGER_ESCALATION_SEVERITY_ORDER.indexOf(left.severity) - MISSION_LEDGER_ESCALATION_SEVERITY_ORDER.indexOf(right.severity);
  }

  if (left.openedAt !== right.openedAt) {
    return right.openedAt.localeCompare(left.openedAt);
  }

  return left.escalationId.localeCompare(right.escalationId);
}