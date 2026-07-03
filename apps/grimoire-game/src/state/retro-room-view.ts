import type {
  GameStateSnapshot,
  JsonValue,
  TaskStatus
} from '../contracts/events';

import {
  BOARD_ACTIVE_TASK_STATUSES,
  BOARD_TASK_STATUS_ORDER,
  createBoardView,
  type BoardDecisionCard
} from './board-view';
import { createAuditView, type AuditEntry } from './audit-view';
import type { GameState, WorkflowStepLogEntry } from './game-state';
import { hydrateGameState } from './game-state';
import {
  createObservabilityPanelView,
  type ObservabilityAttentionKind,
  type ObservabilityAttentionSeverity
} from './observability-panel-view';
import { createSessionView, type SessionStatus } from './session-view';

export const RETRO_ROOM_DIFF_CATEGORY_ORDER = ['blocker', 'progression', 'decision', 'output'] as const;

export type RetroRoomDiffCategory = (typeof RETRO_ROOM_DIFF_CATEGORY_ORDER)[number];
export type RetroRoomDiffSeverity = 'critical' | 'warning' | 'info';

export interface RetroRoomSnapshotOptions {
  snapshotId?: string;
  label?: string;
  generatedAt?: string;
}

export interface RetroRoomSessionSnapshot {
  traceId: string;
  title: string;
  status: SessionStatus;
  taskIds: readonly string[];
  updatedAt: string;
  entryCount: number;
  decisionCount: number;
  toolCallCount: number;
}

export interface RetroRoomTaskSnapshot {
  taskId: string;
  title: string;
  status: TaskStatus;
  assigneeId: string | null;
  traceIds: readonly string[];
  decisionTitles: readonly string[];
  evidenceRefs: readonly string[];
  blockerAlertIds: readonly string[];
  blockerCount: number;
  ticketRefs: readonly string[];
}

export interface RetroRoomAlertSnapshot {
  alertKey: string;
  kind: ObservabilityAttentionKind;
  severity: ObservabilityAttentionSeverity;
  label: string;
  detail: string;
  taskId: string | null;
  traceId: string | null;
  evidenceRefs: readonly string[];
  ticketRefs: readonly string[];
}

export interface RetroRoomDecisionSnapshot {
  decisionKey: string;
  title: string;
  detail: string;
  sourceEventType: string;
  sequenceId: number;
  timestamp: string;
  taskId: string | null;
  traceId: string | null;
  evidenceRefs: readonly string[];
  ticketRefs: readonly string[];
}

export interface RetroRoomOutputSnapshot {
  outputKey: string;
  step: string;
  detail: string;
  sourceEventType: string;
  sequenceId: number;
  timestamp: string;
  taskId: string | null;
  traceId: string | null;
  verificationRef: string | null;
  expectedOutput: string | null;
  evidenceRefs: readonly string[];
  outputRefs: readonly string[];
  expectedProofRefs: readonly string[];
  missingExpectedProofRefs: readonly string[];
  ticketRefs: readonly string[];
}

export interface RetroRoomBoardSnapshot {
  taskCountByStatus: Record<TaskStatus, number>;
  activeTaskCount: number;
  completedTaskCount: number;
  blockedTaskCount: number;
  alertCount: number;
  criticalAlertCount: number;
  warningAlertCount: number;
}

export interface RetroRoomSnapshotSummary {
  sessionCount: number;
  traceCount: number;
  taskCount: number;
  activeTaskCount: number;
  completedTaskCount: number;
  blockedTaskCount: number;
  alertCount: number;
  criticalAlertCount: number;
  warningAlertCount: number;
  decisionCount: number;
  outputCount: number;
  evidenceRefCount: number;
  missingExpectedProofCount: number;
}

export interface RetroRoomSnapshot {
  snapshotId: string;
  label: string;
  generatedAt: string;
  protocolVersion: string;
  lastSequenceId: number;
  board: RetroRoomBoardSnapshot;
  sessions: readonly RetroRoomSessionSnapshot[];
  tasks: readonly RetroRoomTaskSnapshot[];
  alerts: readonly RetroRoomAlertSnapshot[];
  decisions: readonly RetroRoomDecisionSnapshot[];
  outputs: readonly RetroRoomOutputSnapshot[];
  summary: RetroRoomSnapshotSummary;
}

export interface RetroRoomDiffRefs {
  taskIds: readonly string[];
  traceIds: readonly string[];
  ticketRefs: readonly string[];
  evidenceRefs: readonly string[];
}

export interface RetroRoomDiffItem {
  diffId: string;
  category: RetroRoomDiffCategory;
  severity: RetroRoomDiffSeverity;
  focus: string;
  message: string;
  leftValue: string | null;
  rightValue: string | null;
  refs: RetroRoomDiffRefs;
}

export interface RetroRoomTraceComparison {
  sharedTraceIds: readonly string[];
  onlyLeftTraceIds: readonly string[];
  onlyRightTraceIds: readonly string[];
}

export interface RetroRoomViewSummary {
  diffCount: number;
  criticalDiffCount: number;
  warningDiffCount: number;
  infoDiffCount: number;
  blockerDiffCount: number;
  progressionDiffCount: number;
  decisionDiffCount: number;
  outputDiffCount: number;
  sharedTraceCount: number;
  addedTraceCount: number;
  removedTraceCount: number;
}

export interface RetroRoomView {
  left: RetroRoomSnapshot;
  right: RetroRoomSnapshot;
  traceComparison: RetroRoomTraceComparison;
  diffItems: readonly RetroRoomDiffItem[];
  focusItems: readonly RetroRoomDiffItem[];
  summary: RetroRoomViewSummary;
}

interface RetroOutputContext {
  evidenceRefsByTaskId: Map<string, string[]>;
  evidenceRefsByTraceId: Map<string, string[]>;
}

const RETRO_ROOM_DIFF_SEVERITY_RANK: Record<RetroRoomDiffSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2
};

const RETRO_ROOM_DIFF_CATEGORY_RANK: Record<RetroRoomDiffCategory, number> = {
  blocker: 0,
  progression: 1,
  decision: 2,
  output: 3
};

const TASK_STATUS_RANK: Record<TaskStatus, number> = Object.fromEntries(
  BOARD_TASK_STATUS_ORDER.map((status, index) => [status, index])
) as Record<TaskStatus, number>;

const TICKET_REF_PATTERN = /\b[A-Z]+-TKT-\d+\b/gu;

export function createRetroRoomSnapshot(
  state: GameState,
  options: RetroRoomSnapshotOptions = {}
): RetroRoomSnapshot {
  const board = createBoardView(state);
  const audit = createAuditView(state);
  const observability = createObservabilityPanelView(state);
  const sessionView = createSessionView(state);
  const taskTicketRefs = createTaskTicketRefIndex(state.tasks);
  const outputs = collectRetroOutputs(state.recentWorkflowSteps, state.tasks);
  const outputContext = createRetroOutputContext(outputs);
  const alerts = collectRetroAlerts(observability.attentionItems, outputContext, taskTicketRefs);
  const decisions = collectRetroDecisions(board.decisionCards, outputContext, taskTicketRefs);
  const traceIdsByTaskId = createTraceIdsByTaskIdIndex(audit.entries);
  const blockerAlertIdsByTaskId = createBlockerAlertIdsByTaskIdIndex(alerts);
  const generatedAt =
    options.generatedAt ?? deriveRetroSnapshotTimestamp(state, audit.entries);
  const taskCountByStatus = Object.fromEntries(
    BOARD_TASK_STATUS_ORDER.map((status) => [
      status,
      board.taskColumns.find((column) => column.status === status)?.count ?? 0
    ])
  ) as Record<TaskStatus, number>;
  const sessions = sessionView.sessions.map((record) => ({
    traceId: record.summary.traceId,
    title: record.summary.title,
    status: record.summary.status,
    taskIds: record.summary.taskIds,
    updatedAt: record.summary.updatedAt,
    entryCount: record.summary.entryCount,
    decisionCount: record.summary.decisionCount,
    toolCallCount: record.summary.toolCallCount
  }));
  const tasks = Object.values(state.tasks)
    .map((task) => ({
      taskId: task.id,
      title: task.title,
      status: task.status,
      assigneeId: task.assigneeId ?? null,
      traceIds: uniqueStrings([
        ...(traceIdsByTaskId.get(task.id) ?? []),
        ...outputs.filter((output) => output.taskId === task.id).flatMap((output) => output.traceId === null ? [] : [output.traceId]),
        ...decisions.filter((decision) => decision.taskId === task.id).flatMap((decision) => decision.traceId === null ? [] : [decision.traceId])
      ]),
      decisionTitles: uniqueStrings(
        decisions
          .filter((decision) => decision.taskId === task.id)
          .map((decision) => decision.title)
      ),
      evidenceRefs: uniqueStrings([
        ...(outputContext.evidenceRefsByTaskId.get(task.id) ?? []),
        ...outputs.filter((output) => output.taskId === task.id).flatMap((output) => output.outputRefs)
      ]),
      blockerAlertIds: blockerAlertIdsByTaskId.get(task.id) ?? [],
      blockerCount: (blockerAlertIdsByTaskId.get(task.id) ?? []).length,
      ticketRefs: taskTicketRefs.get(task.id) ?? []
    }))
    .sort(compareRetroTaskSnapshots);
  const allEvidenceRefs = uniqueStrings(outputs.flatMap((output) => output.evidenceRefs));
  const boardSnapshot: RetroRoomBoardSnapshot = {
    taskCountByStatus,
    activeTaskCount: BOARD_ACTIVE_TASK_STATUSES.reduce(
      (count, status) => count + (taskCountByStatus[status] ?? 0),
      0
    ),
    completedTaskCount: taskCountByStatus.done ?? 0,
    blockedTaskCount: observability.source.summary.blockedTaskCount,
    alertCount: alerts.length,
    criticalAlertCount: alerts.filter((alert) => alert.severity === 'critical').length,
    warningAlertCount: alerts.filter((alert) => alert.severity === 'warning').length
  };

  return {
    snapshotId:
      options.snapshotId ??
      `retro-snapshot:${generatedAt}:${state.lastSequenceId}`,
    label: options.label ?? `Snapshot ${state.lastSequenceId}`,
    generatedAt,
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    board: boardSnapshot,
    sessions,
    tasks,
    alerts,
    decisions,
    outputs,
    summary: {
      sessionCount: sessions.length,
      traceCount: sessions.length,
      taskCount: tasks.length,
      activeTaskCount: boardSnapshot.activeTaskCount,
      completedTaskCount: boardSnapshot.completedTaskCount,
      blockedTaskCount: boardSnapshot.blockedTaskCount,
      alertCount: boardSnapshot.alertCount,
      criticalAlertCount: boardSnapshot.criticalAlertCount,
      warningAlertCount: boardSnapshot.warningAlertCount,
      decisionCount: decisions.length,
      outputCount: outputs.length,
      evidenceRefCount: allEvidenceRefs.length,
      missingExpectedProofCount: outputs.reduce(
        (count, output) => count + output.missingExpectedProofRefs.length,
        0
      )
    }
  };
}

export function createRetroRoomSnapshotFromGameStateSnapshot(
  snapshot: GameStateSnapshot,
  options: RetroRoomSnapshotOptions = {}
): RetroRoomSnapshot {
  return createRetroRoomSnapshot(hydrateGameState(snapshot, snapshot.generatedAt), {
    ...options,
    generatedAt: options.generatedAt ?? snapshot.generatedAt
  });
}

export function createRetroRoomView(
  left: RetroRoomSnapshot,
  right: RetroRoomSnapshot
): RetroRoomView {
  const traceComparison = createTraceComparison(left, right);
  const diffItems = [
    ...createRetroBlockerDiffs(left, right),
    ...createRetroProgressionDiffs(left, right),
    ...createRetroDecisionDiffs(left, right),
    ...createRetroOutputDiffs(left, right)
  ].sort(compareRetroDiffItems);
  const focusItems =
    diffItems.filter((item) => item.severity !== 'info').slice(0, 8);

  return {
    left,
    right,
    traceComparison,
    diffItems,
    focusItems: focusItems.length > 0 ? focusItems : diffItems.slice(0, 8),
    summary: {
      diffCount: diffItems.length,
      criticalDiffCount: diffItems.filter((item) => item.severity === 'critical').length,
      warningDiffCount: diffItems.filter((item) => item.severity === 'warning').length,
      infoDiffCount: diffItems.filter((item) => item.severity === 'info').length,
      blockerDiffCount: diffItems.filter((item) => item.category === 'blocker').length,
      progressionDiffCount: diffItems.filter((item) => item.category === 'progression').length,
      decisionDiffCount: diffItems.filter((item) => item.category === 'decision').length,
      outputDiffCount: diffItems.filter((item) => item.category === 'output').length,
      sharedTraceCount: traceComparison.sharedTraceIds.length,
      addedTraceCount: traceComparison.onlyRightTraceIds.length,
      removedTraceCount: traceComparison.onlyLeftTraceIds.length
    }
  };
}

export function createRetroRoomViewFromStates(
  leftState: GameState,
  rightState: GameState,
  leftOptions: RetroRoomSnapshotOptions = {},
  rightOptions: RetroRoomSnapshotOptions = {}
): RetroRoomView {
  return createRetroRoomView(
    createRetroRoomSnapshot(leftState, leftOptions),
    createRetroRoomSnapshot(rightState, rightOptions)
  );
}

export function createRetroRoomViewFromGameStateSnapshots(
  leftSnapshot: GameStateSnapshot,
  rightSnapshot: GameStateSnapshot,
  leftOptions: RetroRoomSnapshotOptions = {},
  rightOptions: RetroRoomSnapshotOptions = {}
): RetroRoomView {
  return createRetroRoomView(
    createRetroRoomSnapshotFromGameStateSnapshot(leftSnapshot, leftOptions),
    createRetroRoomSnapshotFromGameStateSnapshot(rightSnapshot, rightOptions)
  );
}

function collectRetroAlerts(
  attentionItems: ReturnType<typeof createObservabilityPanelView>['attentionItems'],
  outputContext: RetroOutputContext,
  taskTicketRefs: Map<string, string[]>
): RetroRoomAlertSnapshot[] {
  const alertsByKey = new Map<string, RetroRoomAlertSnapshot>();

  for (const attentionItem of attentionItems) {
    const alertKey = createRetroAlertKey(attentionItem);
    if (alertsByKey.has(alertKey)) {
      continue;
    }

    alertsByKey.set(alertKey, {
      alertKey,
      kind: attentionItem.kind,
      severity: attentionItem.severity,
      label: attentionItem.label,
      detail: attentionItem.detail,
      taskId: attentionItem.taskId,
      traceId: attentionItem.traceId,
      evidenceRefs: uniqueStrings([
        ...(attentionItem.taskId === null
          ? []
          : (outputContext.evidenceRefsByTaskId.get(attentionItem.taskId) ?? [])),
        ...(attentionItem.traceId === null
          ? []
          : (outputContext.evidenceRefsByTraceId.get(attentionItem.traceId) ?? []))
      ]),
      ticketRefs: uniqueStrings([
        ...extractTicketRefs(attentionItem.label, attentionItem.detail),
        ...(attentionItem.taskId === null ? [] : taskTicketRefs.get(attentionItem.taskId) ?? [])
      ])
    });
  }

  return [...alertsByKey.values()].sort(compareRetroAlertSnapshots);
}

function collectRetroDecisions(
  decisionCards: readonly BoardDecisionCard[],
  outputContext: RetroOutputContext,
  taskTicketRefs: Map<string, string[]>
): RetroRoomDecisionSnapshot[] {
  const decisionsByKey = new Map<string, RetroRoomDecisionSnapshot>();

  for (const decisionCard of [...decisionCards].sort((left, right) => right.sequenceId - left.sequenceId)) {
    const decisionKey = createRetroDecisionKey(decisionCard);
    if (decisionsByKey.has(decisionKey)) {
      continue;
    }

    decisionsByKey.set(decisionKey, {
      decisionKey,
      title: decisionCard.title,
      detail: decisionCard.detail,
      sourceEventType: decisionCard.sourceEventType,
      sequenceId: decisionCard.sequenceId,
      timestamp: decisionCard.timestamp,
      taskId: decisionCard.taskId,
      traceId: decisionCard.traceId,
      evidenceRefs: uniqueStrings([
        ...(decisionCard.taskId === null ? [] : outputContext.evidenceRefsByTaskId.get(decisionCard.taskId) ?? []),
        ...(decisionCard.traceId === null ? [] : outputContext.evidenceRefsByTraceId.get(decisionCard.traceId) ?? [])
      ]),
      ticketRefs: uniqueStrings([
        ...extractTicketRefs(decisionCard.title, decisionCard.detail),
        ...(decisionCard.taskId === null ? [] : taskTicketRefs.get(decisionCard.taskId) ?? [])
      ])
    });
  }

  return [...decisionsByKey.values()].sort(compareRetroDecisionSnapshots);
}

function collectRetroOutputs(
  workflowSteps: readonly WorkflowStepLogEntry[],
  tasks: Record<string, GameState['tasks'][string]>
): RetroRoomOutputSnapshot[] {
  const outputsByKey = new Map<string, RetroRoomOutputSnapshot>();

  for (const workflowStep of [...workflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    const output = toRetroOutputSnapshot(workflowStep, tasks);
    if (output === null || outputsByKey.has(output.outputKey)) {
      continue;
    }

    outputsByKey.set(output.outputKey, output);
  }

  return [...outputsByKey.values()].sort(compareRetroOutputSnapshots);
}

function toRetroOutputSnapshot(
  workflowStep: WorkflowStepLogEntry,
  tasks: Record<string, GameState['tasks'][string]>
): RetroRoomOutputSnapshot | null {
  const metadata = workflowStep.metadata as Record<string, JsonValue>;
  const missionPack = asRecord(metadata.missionPack) ?? asRecord(metadata.mission_pack);
  const evidenceRefs = uniqueStrings(readMetadataStringListByKeys(metadata, ['evidenceRefs', 'evidence_refs']));
  const outputRefs = uniqueStrings(
    readMetadataStringListByKeys(metadata, [
      'outputRefs',
      'output_refs',
      'artifactRefs',
      'artifact_refs',
      'content_refs'
    ])
  );
  const verificationRef = readMetadataStringByKeys(metadata, ['verificationRef', 'verification_ref']);
  const expectedOutput =
    readMetadataStringByKeys(missionPack ?? metadata, [
      'expectedOutput',
      'expected_output',
      'sortie_attendue'
    ]) ?? null;
  const expectedProofRefs = uniqueStrings(
    [
      ...readMetadataStringListByKeys(metadata, [
        'expectedProofRefs',
        'expected_proof_refs',
        'expectedProof',
        'expected_proof',
        'preuve_attendue'
      ]),
      ...(missionPack === null
        ? []
        : readMetadataStringListByKeys(missionPack, [
            'expectedProofRefs',
            'expected_proof_refs',
            'expectedProof',
            'expected_proof',
            'preuve_attendue'
          ]))
    ].map(normalizeRetroProofRef)
  );
  const actionId = readMetadataStringByKeys(metadata, ['actionId', 'action_id']);
  const actualProofRefs = new Set(
    uniqueStrings([
      ...evidenceRefs,
      ...outputRefs,
      ...(verificationRef === null ? [] : [verificationRef]),
      ...(actionId === null ? [] : [`action:${actionId}`])
    ])
  );
  const missingExpectedProofRefs = expectedProofRefs.filter((proofRef) => !actualProofRefs.has(proofRef));

  if (
    evidenceRefs.length === 0 &&
    outputRefs.length === 0 &&
    verificationRef === null &&
    expectedOutput === null &&
    expectedProofRefs.length === 0
  ) {
    return null;
  }

  const task = workflowStep.taskId === undefined ? undefined : tasks[workflowStep.taskId];

  return {
    outputKey: createRetroOutputKey(workflowStep, verificationRef),
    step: workflowStep.step,
    detail: workflowStep.detail,
    sourceEventType: workflowStep.sourceEventType,
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp,
    taskId: workflowStep.taskId ?? null,
    traceId: workflowStep.traceId ?? null,
    verificationRef,
    expectedOutput,
    evidenceRefs,
    outputRefs,
    expectedProofRefs,
    missingExpectedProofRefs,
    ticketRefs: uniqueStrings([
      ...extractTicketRefs(workflowStep.step, workflowStep.detail),
      ...(task === undefined ? [] : extractTicketRefs(task.id, task.title))
    ])
  };
}

function createRetroOutputContext(outputs: readonly RetroRoomOutputSnapshot[]): RetroOutputContext {
  const evidenceRefsByTaskId = new Map<string, string[]>();
  const evidenceRefsByTraceId = new Map<string, string[]>();

  for (const output of outputs) {
    if (output.taskId !== null) {
      evidenceRefsByTaskId.set(
        output.taskId,
        uniqueStrings([...(evidenceRefsByTaskId.get(output.taskId) ?? []), ...output.evidenceRefs])
      );
    }

    if (output.traceId !== null) {
      evidenceRefsByTraceId.set(
        output.traceId,
        uniqueStrings([...(evidenceRefsByTraceId.get(output.traceId) ?? []), ...output.evidenceRefs])
      );
    }
  }

  return {
    evidenceRefsByTaskId,
    evidenceRefsByTraceId
  };
}

function createTraceIdsByTaskIdIndex(entries: readonly AuditEntry[]): Map<string, string[]> {
  const traceIdsByTaskId = new Map<string, string[]>();

  for (const entry of entries) {
    if (entry.taskId === null || entry.traceId === null) {
      continue;
    }

    traceIdsByTaskId.set(
      entry.taskId,
      uniqueStrings([...(traceIdsByTaskId.get(entry.taskId) ?? []), entry.traceId])
    );
  }

  return traceIdsByTaskId;
}

function createBlockerAlertIdsByTaskIdIndex(
  alerts: readonly RetroRoomAlertSnapshot[]
): Map<string, string[]> {
  const blockerAlertIdsByTaskId = new Map<string, string[]>();

  for (const alert of alerts) {
    if (alert.taskId === null || alert.severity === 'info') {
      continue;
    }

    blockerAlertIdsByTaskId.set(
      alert.taskId,
      uniqueStrings([...(blockerAlertIdsByTaskId.get(alert.taskId) ?? []), alert.alertKey])
    );
  }

  return blockerAlertIdsByTaskId;
}

function createTaskTicketRefIndex(
  tasks: Record<string, GameState['tasks'][string]>
): Map<string, string[]> {
  const taskTicketRefs = new Map<string, string[]>();

  for (const task of Object.values(tasks)) {
    taskTicketRefs.set(task.id, extractTicketRefs(task.id, task.title));
  }

  return taskTicketRefs;
}

function createTraceComparison(
  left: RetroRoomSnapshot,
  right: RetroRoomSnapshot
): RetroRoomTraceComparison {
  const leftTraceIds = left.sessions.map((session) => session.traceId);
  const rightTraceIds = right.sessions.map((session) => session.traceId);

  return {
    sharedTraceIds: intersect(leftTraceIds, rightTraceIds),
    onlyLeftTraceIds: subtract(leftTraceIds, rightTraceIds),
    onlyRightTraceIds: subtract(rightTraceIds, leftTraceIds)
  };
}

function createRetroBlockerDiffs(
  left: RetroRoomSnapshot,
  right: RetroRoomSnapshot
): RetroRoomDiffItem[] {
  const diffs: RetroRoomDiffItem[] = [];
  const leftAlerts = new Map(left.alerts.map((alert) => [alert.alertKey, alert]));
  const rightAlerts = new Map(right.alerts.map((alert) => [alert.alertKey, alert]));
  const alertKeys = uniqueStrings([...leftAlerts.keys(), ...rightAlerts.keys()]);

  for (const alertKey of alertKeys) {
    const leftAlert = leftAlerts.get(alertKey) ?? null;
    const rightAlert = rightAlerts.get(alertKey) ?? null;

    if (leftAlert !== null && rightAlert !== null) {
      if (leftAlert.severity === rightAlert.severity && leftAlert.detail === rightAlert.detail) {
        continue;
      }

      diffs.push({
        diffId: `blocker:${alertKey}`,
        category: 'blocker',
        severity: higherRetroSeverity(leftAlert.severity, rightAlert.severity),
        focus: rightAlert.label,
        message: 'Alert detail changed between snapshots.',
        leftValue: leftAlert.detail,
        rightValue: rightAlert.detail,
        refs: mergeRetroRefs(leftAlert, rightAlert)
      });
      continue;
    }

    if (leftAlert === null && rightAlert !== null) {
      diffs.push({
        diffId: `blocker:${alertKey}`,
        category: 'blocker',
        severity: rightAlert.severity,
        focus: rightAlert.label,
        message: 'New alert raised in the right snapshot.',
        leftValue: null,
        rightValue: rightAlert.detail,
        refs: toRetroRefs(rightAlert)
      });
      continue;
    }

    if (leftAlert !== null) {
      diffs.push({
        diffId: `blocker:${alertKey}`,
        category: 'blocker',
        severity: 'info',
        focus: leftAlert.label,
        message: 'Alert resolved in the right snapshot.',
        leftValue: leftAlert.detail,
        rightValue: null,
        refs: toRetroRefs(leftAlert)
      });
    }
  }

  return diffs;
}

function createRetroProgressionDiffs(
  left: RetroRoomSnapshot,
  right: RetroRoomSnapshot
): RetroRoomDiffItem[] {
  const diffs: RetroRoomDiffItem[] = [];
  const leftTasks = new Map(left.tasks.map((task) => [task.taskId, task]));
  const rightTasks = new Map(right.tasks.map((task) => [task.taskId, task]));
  const taskIds = uniqueStrings([...leftTasks.keys(), ...rightTasks.keys()]);

  for (const taskId of taskIds) {
    const leftTask = leftTasks.get(taskId) ?? null;
    const rightTask = rightTasks.get(taskId) ?? null;

    if (leftTask === null && rightTask !== null) {
      diffs.push({
        diffId: `progression:${taskId}`,
        category: 'progression',
        severity: 'info',
        focus: rightTask.title,
        message: 'Task introduced in the right snapshot.',
        leftValue: null,
        rightValue: rightTask.status,
        refs: toRetroRefs(rightTask)
      });
      continue;
    }

    if (leftTask !== null && rightTask === null) {
      diffs.push({
        diffId: `progression:${taskId}`,
        category: 'progression',
        severity: 'warning',
        focus: leftTask.title,
        message: 'Task no longer present in the right snapshot.',
        leftValue: leftTask.status,
        rightValue: null,
        refs: toRetroRefs(leftTask)
      });
      continue;
    }

    if (leftTask === null || rightTask === null || leftTask.status === rightTask.status) {
      continue;
    }

    diffs.push({
      diffId: `progression:${taskId}`,
      category: 'progression',
      severity:
        TASK_STATUS_RANK[rightTask.status] >= TASK_STATUS_RANK[leftTask.status] ? 'info' : 'warning',
      focus: rightTask.title,
      message: 'Task status changed between snapshots.',
      leftValue: leftTask.status,
      rightValue: rightTask.status,
      refs: mergeRetroRefs(leftTask, rightTask)
    });
  }

  return diffs;
}

function createRetroDecisionDiffs(
  left: RetroRoomSnapshot,
  right: RetroRoomSnapshot
): RetroRoomDiffItem[] {
  const diffs: RetroRoomDiffItem[] = [];
  const leftDecisions = new Map(left.decisions.map((decision) => [decision.decisionKey, decision]));
  const rightDecisions = new Map(right.decisions.map((decision) => [decision.decisionKey, decision]));
  const decisionKeys = uniqueStrings([...leftDecisions.keys(), ...rightDecisions.keys()]);

  for (const decisionKey of decisionKeys) {
    const leftDecision = leftDecisions.get(decisionKey) ?? null;
    const rightDecision = rightDecisions.get(decisionKey) ?? null;

    if (leftDecision !== null && rightDecision !== null) {
      if (leftDecision.detail === rightDecision.detail) {
        continue;
      }

      diffs.push({
        diffId: `decision:${decisionKey}`,
        category: 'decision',
        severity: 'info',
        focus: rightDecision.title,
        message: 'Decision detail changed between snapshots.',
        leftValue: leftDecision.detail,
        rightValue: rightDecision.detail,
        refs: mergeRetroRefs(leftDecision, rightDecision)
      });
      continue;
    }

    if (leftDecision === null && rightDecision !== null) {
      diffs.push({
        diffId: `decision:${decisionKey}`,
        category: 'decision',
        severity: 'info',
        focus: rightDecision.title,
        message: 'Decision introduced in the right snapshot.',
        leftValue: null,
        rightValue: rightDecision.detail,
        refs: toRetroRefs(rightDecision)
      });
      continue;
    }

    if (leftDecision !== null) {
      diffs.push({
        diffId: `decision:${decisionKey}`,
        category: 'decision',
        severity: 'warning',
        focus: leftDecision.title,
        message: 'Decision no longer present in the right snapshot.',
        leftValue: leftDecision.detail,
        rightValue: null,
        refs: toRetroRefs(leftDecision)
      });
    }
  }

  return diffs;
}

function createRetroOutputDiffs(
  left: RetroRoomSnapshot,
  right: RetroRoomSnapshot
): RetroRoomDiffItem[] {
  const diffs: RetroRoomDiffItem[] = [];
  const leftOutputs = new Map(left.outputs.map((output) => [output.outputKey, output]));
  const rightOutputs = new Map(right.outputs.map((output) => [output.outputKey, output]));
  const outputKeys = uniqueStrings([...leftOutputs.keys(), ...rightOutputs.keys()]);

  for (const outputKey of outputKeys) {
    const leftOutput = leftOutputs.get(outputKey) ?? null;
    const rightOutput = rightOutputs.get(outputKey) ?? null;

    if (leftOutput === null && rightOutput !== null) {
      diffs.push({
        diffId: `output:${outputKey}`,
        category: 'output',
        severity: rightOutput.missingExpectedProofRefs.length > 0 ? 'warning' : 'info',
        focus: rightOutput.step,
        message: 'Output introduced in the right snapshot.',
        leftValue: null,
        rightValue: summarizeRetroOutput(rightOutput),
        refs: toRetroRefs(rightOutput)
      });
      continue;
    }

    if (leftOutput !== null && rightOutput === null) {
      diffs.push({
        diffId: `output:${outputKey}`,
        category: 'output',
        severity: 'warning',
        focus: leftOutput.step,
        message: 'Output no longer present in the right snapshot.',
        leftValue: summarizeRetroOutput(leftOutput),
        rightValue: null,
        refs: toRetroRefs(leftOutput)
      });
      continue;
    }

    if (leftOutput === null || rightOutput === null) {
      continue;
    }

    const missingProofDelta = diffStringLists(
      leftOutput.missingExpectedProofRefs,
      rightOutput.missingExpectedProofRefs
    );
    const evidenceDelta = diffStringLists(leftOutput.evidenceRefs, rightOutput.evidenceRefs);

    if (missingProofDelta.added.length > 0 || missingProofDelta.removed.length > 0) {
      diffs.push({
        diffId: `output:${outputKey}`,
        category: 'output',
        severity: missingProofDelta.added.length > 0 ? 'critical' : 'info',
        focus: rightOutput.step,
        message:
          missingProofDelta.added.length > 0
            ? `Proof gap widened: ${missingProofDelta.added.join(', ')}`
            : `Proof gap resolved: ${missingProofDelta.removed.join(', ')}`,
        leftValue: summarizeRetroOutput(leftOutput),
        rightValue: summarizeRetroOutput(rightOutput),
        refs: mergeRetroRefs(leftOutput, rightOutput)
      });
      continue;
    }

    if (evidenceDelta.added.length > 0 || evidenceDelta.removed.length > 0) {
      diffs.push({
        diffId: `output:${outputKey}`,
        category: 'output',
        severity: evidenceDelta.removed.length > 0 ? 'warning' : 'info',
        focus: rightOutput.step,
        message:
          evidenceDelta.removed.length > 0
            ? `Evidence coverage regressed: ${evidenceDelta.removed.join(', ')}`
            : `Evidence coverage improved: ${evidenceDelta.added.join(', ')}`,
        leftValue: summarizeRetroOutput(leftOutput),
        rightValue: summarizeRetroOutput(rightOutput),
        refs: mergeRetroRefs(leftOutput, rightOutput)
      });
      continue;
    }

    if (
      leftOutput.detail !== rightOutput.detail ||
      leftOutput.expectedOutput !== rightOutput.expectedOutput ||
      leftOutput.verificationRef !== rightOutput.verificationRef
    ) {
      diffs.push({
        diffId: `output:${outputKey}`,
        category: 'output',
        severity: 'info',
        focus: rightOutput.step,
        message: 'Output payload changed between snapshots.',
        leftValue: summarizeRetroOutput(leftOutput),
        rightValue: summarizeRetroOutput(rightOutput),
        refs: mergeRetroRefs(leftOutput, rightOutput)
      });
    }
  }

  return diffs;
}

function summarizeRetroOutput(output: RetroRoomOutputSnapshot): string {
  return [
    output.verificationRef,
    output.expectedOutput,
    output.evidenceRefs.length === 0 ? null : `${output.evidenceRefs.length} evidence ref(s)`,
    output.missingExpectedProofRefs.length === 0
      ? null
      : `${output.missingExpectedProofRefs.length} missing proof ref(s)`
  ]
    .filter((part): part is string => part !== null)
    .join(' | ');
}

function deriveRetroSnapshotTimestamp(state: GameState, entries: readonly AuditEntry[]): string {
  return (
    entries[0]?.timestamp ?? state.hydratedAt ?? new Date(0).toISOString()
  );
}

function createRetroAlertKey(attentionItem: ReturnType<typeof createObservabilityPanelView>['attentionItems'][number]): string {
  return [
    attentionItem.kind,
    attentionItem.taskId ?? '__taskless__',
    attentionItem.traceId ?? '__traceless__',
    normalizeToken(attentionItem.label)
  ].join('::');
}

function createRetroDecisionKey(decisionCard: BoardDecisionCard): string {
  return [
    decisionCard.sourceEventType,
    decisionCard.taskId ?? '__taskless__',
    decisionCard.traceId ?? '__traceless__',
    normalizeToken(decisionCard.title)
  ].join('::');
}

function createRetroOutputKey(
  workflowStep: WorkflowStepLogEntry,
  verificationRef: string | null
): string {
  return [
    workflowStep.sourceEventType,
    workflowStep.taskId ?? '__taskless__',
    workflowStep.traceId ?? '__traceless__',
    normalizeToken(workflowStep.step),
    verificationRef ?? '__unverified__'
  ].join('::');
}

function compareRetroTaskSnapshots(
  left: RetroRoomTaskSnapshot,
  right: RetroRoomTaskSnapshot
): number {
  if (left.status !== right.status) {
    return TASK_STATUS_RANK[left.status] - TASK_STATUS_RANK[right.status];
  }

  return left.title.localeCompare(right.title);
}

function compareRetroAlertSnapshots(
  left: RetroRoomAlertSnapshot,
  right: RetroRoomAlertSnapshot
): number {
  if (left.severity !== right.severity) {
    return RETRO_ROOM_DIFF_SEVERITY_RANK[left.severity] - RETRO_ROOM_DIFF_SEVERITY_RANK[right.severity];
  }

  return left.label.localeCompare(right.label);
}

function compareRetroDecisionSnapshots(
  left: RetroRoomDecisionSnapshot,
  right: RetroRoomDecisionSnapshot
): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  return left.title.localeCompare(right.title);
}

function compareRetroOutputSnapshots(
  left: RetroRoomOutputSnapshot,
  right: RetroRoomOutputSnapshot
): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  return left.step.localeCompare(right.step);
}

function compareRetroDiffItems(left: RetroRoomDiffItem, right: RetroRoomDiffItem): number {
  if (left.severity !== right.severity) {
    return RETRO_ROOM_DIFF_SEVERITY_RANK[left.severity] - RETRO_ROOM_DIFF_SEVERITY_RANK[right.severity];
  }

  if (left.category !== right.category) {
    return RETRO_ROOM_DIFF_CATEGORY_RANK[left.category] - RETRO_ROOM_DIFF_CATEGORY_RANK[right.category];
  }

  if (left.focus !== right.focus) {
    return left.focus.localeCompare(right.focus);
  }

  return left.message.localeCompare(right.message);
}

function higherRetroSeverity(
  left: RetroRoomDiffSeverity,
  right: RetroRoomDiffSeverity
): RetroRoomDiffSeverity {
  return RETRO_ROOM_DIFF_SEVERITY_RANK[left] <= RETRO_ROOM_DIFF_SEVERITY_RANK[right] ? left : right;
}

function toRetroRefs(
  item:
    | RetroRoomAlertSnapshot
    | RetroRoomDecisionSnapshot
    | RetroRoomOutputSnapshot
    | RetroRoomTaskSnapshot
): RetroRoomDiffRefs {
  if ('traceIds' in item) {
    return {
      taskIds: [item.taskId],
      traceIds: item.traceIds,
      ticketRefs: item.ticketRefs,
      evidenceRefs: item.evidenceRefs
    };
  }

  return {
    taskIds: item.taskId === null ? [] : [item.taskId],
    traceIds: item.traceId === null ? [] : [item.traceId],
    ticketRefs: item.ticketRefs,
    evidenceRefs: item.evidenceRefs
  };
}

function mergeRetroRefs(
  left:
    | RetroRoomAlertSnapshot
    | RetroRoomDecisionSnapshot
    | RetroRoomOutputSnapshot
    | RetroRoomTaskSnapshot,
  right:
    | RetroRoomAlertSnapshot
    | RetroRoomDecisionSnapshot
    | RetroRoomOutputSnapshot
    | RetroRoomTaskSnapshot
): RetroRoomDiffRefs {
  const leftRefs = toRetroRefs(left);
  const rightRefs = toRetroRefs(right);

  return {
    taskIds: uniqueStrings([...leftRefs.taskIds, ...rightRefs.taskIds]),
    traceIds: uniqueStrings([...leftRefs.traceIds, ...rightRefs.traceIds]),
    ticketRefs: uniqueStrings([...leftRefs.ticketRefs, ...rightRefs.ticketRefs]),
    evidenceRefs: uniqueStrings([...leftRefs.evidenceRefs, ...rightRefs.evidenceRefs])
  };
}

function diffStringLists(
  left: readonly string[],
  right: readonly string[]
): { added: string[]; removed: string[] } {
  return {
    added: subtract(right, left),
    removed: subtract(left, right)
  };
}

function intersect(left: readonly string[], right: readonly string[]): string[] {
  const rightSet = new Set(right);
  return uniqueStrings(left.filter((value) => rightSet.has(value)));
}

function subtract(left: readonly string[], right: readonly string[]): string[] {
  const rightSet = new Set(right);
  return uniqueStrings(left.filter((value) => !rightSet.has(value)));
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.trim().length > 0))].sort((left, right) => left.localeCompare(right));
}

function extractTicketRefs(...parts: readonly (string | null | undefined)[]): string[] {
  const ticketRefs: string[] = [];

  for (const part of parts) {
    if (part === null || part === undefined) {
      continue;
    }

    ticketRefs.push(...(part.match(TICKET_REF_PATTERN) ?? []));
  }

  return uniqueStrings(ticketRefs);
}

function normalizeRetroProofRef(value: string): string {
  return value.trim();
}

function normalizeToken(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/gu, '-');
}

function asRecord(value: JsonValue | undefined): Record<string, JsonValue> | null {
  if (value === undefined || value === null || Array.isArray(value) || typeof value !== 'object') {
    return null;
  }

  return value as Record<string, JsonValue>;
}

function readMetadataStringByKeys(
  record: Record<string, JsonValue>,
  keys: readonly string[]
): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim().length > 0) {
      return value.trim();
    }
  }

  return null;
}

function readMetadataStringListByKeys(
  record: Record<string, JsonValue>,
  keys: readonly string[]
): string[] {
  const values: string[] = [];

  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim().length > 0) {
      values.push(value.trim());
      continue;
    }

    if (!Array.isArray(value)) {
      continue;
    }

    for (const item of value) {
      if (typeof item === 'string' && item.trim().length > 0) {
        values.push(item.trim());
      }
    }
  }

  return uniqueStrings(values);
}