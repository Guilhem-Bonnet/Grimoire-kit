import {
  BranchFinishDecisionPayloadSchema,
  BranchFinishOptionsPayloadSchema,
  type BranchFinishOption,
  type JsonValue,
  type LeaseStoreSnapshot,
  type TaskStatus
} from '../contracts/events';

import {
  BRANCH_FINISH_OPTION_ORDER,
  createSecurityAuditView
} from './branch-finisher-view';
import type { GameState, WorkflowStepLogEntry } from './game-state';
import {
  createLeaseView,
  type LeaseDirtyStatus,
  type LeaseOwnershipStatus,
  type LeaseViewAlert,
  type LeaseViewRecord
} from './lease-view';
import {
  createVerificationQueueView,
  type VerificationQueueItem,
  type VerificationQueueStatus
} from './verification-queue-view';

export const WORKTREE_ROOM_RUNTIME_SURFACE_ORDER = [
  'lease_view',
  'branch_finisher',
  'verification_queue',
  'security_audit'
] as const;

export type WorktreeRoomRuntimeSurface = (typeof WORKTREE_ROOM_RUNTIME_SURFACE_ORDER)[number];
export type WorktreeRoomTone = 'positive' | 'neutral' | 'warning' | 'critical';
export type WorktreeRoomSurfaceStatus = 'ready' | 'attention' | 'blocked';
export type WorktreeRoomAlertSeverity = 'warning' | 'critical';
export type WorktreeRoomAlertCode =
  | LeaseViewAlert['code']
  | 'verification_blocked';

export interface WorktreeRoomActionSurface {
  surface: WorktreeRoomRuntimeSurface;
  status: WorktreeRoomSurfaceStatus;
  detail: string;
}

export interface WorktreeRoomAction {
  option: BranchFinishOption;
  allowed: boolean;
  blockedReasons: readonly string[];
  requiresTypedConfirmation: boolean;
  requiredTypedConfirmation: string | null;
  selected: boolean;
  surfaces: readonly WorktreeRoomActionSurface[];
}

export interface WorktreeRoomDecision {
  branch: string;
  selectedOption: BranchFinishOption;
  typedConfirmation: string;
  allowed: boolean;
  blockedReasons: readonly string[];
  sequenceId: number;
  timestamp: string;
  taskId: string | null;
  traceId: string | null;
}

export interface WorktreeRoomAlert {
  code: WorktreeRoomAlertCode;
  severity: WorktreeRoomAlertSeverity;
  roomId: string;
  leaseId: string;
  branch: string | null;
  worktreeId: string | null;
  surface: WorktreeRoomRuntimeSurface;
  message: string;
}

export interface WorktreeRoomAuditEntry {
  id: string;
  sequenceId: number;
  timestamp: string;
  sourceEventType: string;
  step: string;
  detail: string;
  branch: string | null;
  taskId: string | null;
  traceId: string | null;
  option: BranchFinishOption | null;
}

export interface WorktreeRoom {
  roomId: string;
  leaseId: string;
  branch: string | null;
  worktreeId: string | null;
  taskId: string;
  taskTitle: string | null;
  taskStatus: TaskStatus | null;
  ownerId: string | null;
  nodeId: string;
  status: LeaseViewRecord['status'];
  ownershipStatus: LeaseOwnershipStatus;
  dirtyStatus: LeaseDirtyStatus;
  testsPassed: boolean;
  branchCollisionCount: number;
  worktreeCollisionCount: number;
  verificationStatus: VerificationQueueStatus | null;
  tone: WorktreeRoomTone;
  alerts: readonly WorktreeRoomAlert[];
  actions: readonly WorktreeRoomAction[];
  latestDecision: WorktreeRoomDecision | null;
  auditTrail: readonly WorktreeRoomAuditEntry[];
}

export interface WorktreeRoomViewSummary {
  roomCount: number;
  activeRoomCount: number;
  expiredRoomCount: number;
  dirtyRoomCount: number;
  conflictRoomCount: number;
  unresolvedRoomCount: number;
  staleRoomCount: number;
  verificationBlockedCount: number;
  alertCount: number;
}

export interface WorktreeRoomView {
  protocolVersion: string;
  lastSequenceId: number;
  referenceTimestamp: string | null;
  rooms: readonly WorktreeRoom[];
  alerts: readonly WorktreeRoomAlert[];
  summary: WorktreeRoomViewSummary;
}

interface BranchFinishOptionsRecord {
  payload: ReturnType<typeof BranchFinishOptionsPayloadSchema.parse>;
  sequenceId: number;
  timestamp: string;
  taskId: string | null;
  traceId: string | null;
}

interface BranchFinishDecisionRecord {
  payload: ReturnType<typeof BranchFinishDecisionPayloadSchema.parse>;
  sequenceId: number;
  timestamp: string;
  taskId: string | null;
  traceId: string | null;
}

const WORKTREE_ROOM_TONE_RANK: Record<WorktreeRoomTone, number> = {
  critical: 0,
  warning: 1,
  neutral: 2,
  positive: 3
};

const WORKTREE_ROOM_ALERT_SEVERITY_RANK: Record<WorktreeRoomAlertSeverity, number> = {
  critical: 0,
  warning: 1
};

export function createWorktreeRoomView(
  state: GameState,
  snapshot: LeaseStoreSnapshot | null
): WorktreeRoomView {
  const leaseView = createLeaseView(snapshot, Object.values(state.tasks));
  const securityAudit = createSecurityAuditView(state);
  const verificationQueue = createVerificationQueueView(state);
  const verificationByTaskId = new Map(verificationQueue.items.map((item) => [item.taskId, item]));
  const optionsByBranch = collectBranchFinishOptionsByBranch(state);
  const decisionsByBranch = collectBranchFinishDecisionsByBranch(state);
  const alertsByLeaseId = groupLeaseAlertsByLeaseId(leaseView.alerts);
  const branchOccupancy = countActiveLeaseOccupancy(leaseView.leases, 'branch');
  const worktreeOccupancy = countActiveLeaseOccupancy(leaseView.leases, 'worktreeId');
  const referenceTimestamp =
    snapshot?.generatedAt ?? state.hydratedAt ?? deriveLatestWorkflowTimestamp(state.recentWorkflowSteps);
  const rooms = leaseView.leases
    .map((lease) => {
      const verificationItem = verificationByTaskId.get(lease.taskId) ?? null;
      const task = state.tasks[lease.taskId];
      const optionsRecord = lease.branch === null ? null : optionsByBranch.get(lease.branch) ?? null;
      const decisionRecord = lease.branch === null ? null : decisionsByBranch.get(lease.branch) ?? null;
      const branchCollisionCount = lease.branch === null ? 0 : Math.max((branchOccupancy.get(lease.branch) ?? 1) - 1, 0);
      const worktreeCollisionCount =
        lease.worktreeId === null ? 0 : Math.max((worktreeOccupancy.get(lease.worktreeId) ?? 1) - 1, 0);

      return createWorktreeRoom({
        state,
        lease,
        taskTitle: task?.title ?? null,
        taskStatus: task?.status ?? null,
        verificationItem,
        securityBlocked: securityAudit.shipBlocked,
        optionsRecord,
        decisionRecord,
        leaseAlerts: alertsByLeaseId.get(lease.leaseId) ?? [],
        branchCollisionCount,
        worktreeCollisionCount
      });
    })
    .sort(compareWorktreeRooms);
  const alerts = rooms.flatMap((room) => room.alerts).sort(compareWorktreeAlerts);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    referenceTimestamp,
    rooms,
    alerts,
    summary: {
      roomCount: rooms.length,
      activeRoomCount: rooms.filter((room) => room.status === 'active').length,
      expiredRoomCount: rooms.filter((room) => room.status === 'expired').length,
      dirtyRoomCount: rooms.filter((room) => room.dirtyStatus === 'dirty').length,
      conflictRoomCount: rooms.filter((room) => room.ownershipStatus === 'conflicted').length,
      unresolvedRoomCount: rooms.filter((room) => room.ownershipStatus === 'unresolved').length,
      staleRoomCount: rooms.filter((room) => room.status === 'expired').length,
      verificationBlockedCount: rooms.filter((room) => room.alerts.some((alert) => alert.code === 'verification_blocked')).length,
      alertCount: alerts.length
    }
  };
}

function createWorktreeRoom(input: {
  state: GameState;
  lease: LeaseViewRecord;
  taskTitle: string | null;
  taskStatus: TaskStatus | null;
  verificationItem: VerificationQueueItem | null;
  securityBlocked: boolean;
  optionsRecord: BranchFinishOptionsRecord | null;
  decisionRecord: BranchFinishDecisionRecord | null;
  leaseAlerts: readonly LeaseViewAlert[];
  branchCollisionCount: number;
  worktreeCollisionCount: number;
}): WorktreeRoom {
  const roomId = `worktree-room:${input.lease.leaseId}`;
  const optionsPayload =
    input.optionsRecord?.payload ??
    BranchFinishOptionsPayloadSchema.parse({
      branch: input.lease.branch ?? input.lease.leaseId,
      testsPassed: false
    });
  const ownershipBlockedReasons = describeOwnershipBlockedReasons(input.lease);
  const verificationBlockedReasons = describeVerificationBlockedReasons(
    input.taskTitle,
    input.taskStatus,
    input.verificationItem
  );
  const actions = BRANCH_FINISH_OPTION_ORDER.map((option) =>
    createWorktreeRoomAction({
      option,
      optionsPayload,
      securityBlocked: input.securityBlocked,
      ownershipBlockedReasons,
      verificationBlockedReasons,
      verificationItem: input.verificationItem,
      lease: input.lease,
      selectedOption: input.decisionRecord?.payload.selectedOption ?? null
    })
  );
  const latestDecision = createWorktreeRoomDecision(input.decisionRecord, optionsPayload, actions);
  const alerts = createWorktreeRoomAlerts({
    roomId,
    lease: input.lease,
    leaseAlerts: input.leaseAlerts,
    verificationBlockedReasons
  });

  return {
    roomId,
    leaseId: input.lease.leaseId,
    branch: input.lease.branch,
    worktreeId: input.lease.worktreeId,
    taskId: input.lease.taskId,
    taskTitle: input.taskTitle,
    taskStatus: input.taskStatus,
    ownerId: input.lease.ownerId,
    nodeId: input.lease.nodeId,
    status: input.lease.status,
    ownershipStatus: input.lease.ownershipStatus,
    dirtyStatus: input.lease.dirtyStatus,
    testsPassed: optionsPayload.testsPassed,
    branchCollisionCount: input.branchCollisionCount,
    worktreeCollisionCount: input.worktreeCollisionCount,
    verificationStatus: input.verificationItem?.queueStatus ?? null,
    tone: deriveWorktreeRoomTone(input.lease, input.verificationItem, alerts),
    alerts,
    actions,
    latestDecision,
    auditTrail: collectWorktreeRoomAuditTrail(input.state.recentWorkflowSteps, input.lease)
  };
}

function createWorktreeRoomAction(input: {
  option: BranchFinishOption;
  optionsPayload: ReturnType<typeof BranchFinishOptionsPayloadSchema.parse>;
  securityBlocked: boolean;
  ownershipBlockedReasons: readonly string[];
  verificationBlockedReasons: readonly string[];
  verificationItem: VerificationQueueItem | null;
  lease: LeaseViewRecord;
  selectedOption: BranchFinishOption | null;
}): WorktreeRoomAction {
  const branchPolicyBlockedReasons = describeBranchPolicyBlockedReasons(
    input.option,
    input.optionsPayload,
    input.securityBlocked
  );
  const blockedReasons = [...branchPolicyBlockedReasons];

  if (isDestructiveBranchOption(input.option)) {
    blockedReasons.push(...input.ownershipBlockedReasons, ...input.verificationBlockedReasons);
  }

  return {
    option: input.option,
    allowed: blockedReasons.length === 0,
    blockedReasons: uniqueStrings(blockedReasons),
    requiresTypedConfirmation: input.option === 'discard',
    requiredTypedConfirmation: input.option === 'discard' ? input.optionsPayload.typedDiscardConfirmation : null,
    selected: input.selectedOption === input.option,
    surfaces: createWorktreeRoomActionSurfaces({
      option: input.option,
      branchPolicyBlockedReasons,
      ownershipBlockedReasons: input.ownershipBlockedReasons,
      verificationBlockedReasons: input.verificationBlockedReasons,
      verificationItem: input.verificationItem,
      lease: input.lease,
      securityBlocked: input.securityBlocked
    })
  };
}

function createWorktreeRoomActionSurfaces(input: {
  option: BranchFinishOption;
  branchPolicyBlockedReasons: readonly string[];
  ownershipBlockedReasons: readonly string[];
  verificationBlockedReasons: readonly string[];
  verificationItem: VerificationQueueItem | null;
  lease: LeaseViewRecord;
  securityBlocked: boolean;
}): WorktreeRoomActionSurface[] {
  const surfaces: WorktreeRoomActionSurface[] = [
    {
      surface: 'branch_finisher',
      status: input.branchPolicyBlockedReasons.length > 0 ? 'blocked' : 'ready',
      detail:
        input.branchPolicyBlockedReasons[0] ??
        'Branch finisher matrix allows this closure option.'
    }
  ];

  if (isDestructiveBranchOption(input.option)) {
    surfaces.push({
      surface: 'lease_view',
      status: input.ownershipBlockedReasons.length > 0 ? 'blocked' : 'ready',
      detail:
        input.ownershipBlockedReasons[0] ??
        'Lease ownership is exclusive and the worktree is active.'
    });
    surfaces.push({
      surface: 'verification_queue',
      status: input.verificationBlockedReasons.length > 0 ? 'blocked' : 'ready',
      detail:
        input.verificationBlockedReasons[0] ??
        describeVerificationReadyDetail(input.verificationItem)
    });
  } else {
    surfaces.push({
      surface: 'lease_view',
      status: input.lease.status === 'expired' || input.lease.ownershipStatus !== 'owned' ? 'attention' : 'ready',
      detail:
        input.lease.status === 'expired'
          ? 'Lease is stale but the room can stay visible for triage.'
          : 'Room can remain open without mutating branch ownership.'
    });
  }

  if (input.option === 'merge' || input.option === 'pr') {
    surfaces.push({
      surface: 'security_audit',
      status: input.securityBlocked ? 'blocked' : 'ready',
      detail: input.securityBlocked
        ? 'Security audit has unresolved blocking findings.'
        : 'Security audit is clear for branch closure.'
    });
  }

  return surfaces.sort(compareWorktreeRoomActionSurfaces);
}

function createWorktreeRoomDecision(
  decisionRecord: BranchFinishDecisionRecord | null,
  optionsPayload: ReturnType<typeof BranchFinishOptionsPayloadSchema.parse>,
  actions: readonly WorktreeRoomAction[]
): WorktreeRoomDecision | null {
  if (decisionRecord === null) {
    return null;
  }

  const action = actions.find((candidate) => candidate.option === decisionRecord.payload.selectedOption);
  const blockedReasons = [...(action?.blockedReasons ?? ['Selected branch finish option is unknown.'])];

  if (
    decisionRecord.payload.selectedOption === 'discard' &&
    decisionRecord.payload.typedConfirmation.trim() !== optionsPayload.typedDiscardConfirmation
  ) {
    blockedReasons.push('Discard option requires an exact typed confirmation.');
  }

  return {
    branch: decisionRecord.payload.branch,
    selectedOption: decisionRecord.payload.selectedOption,
    typedConfirmation: decisionRecord.payload.typedConfirmation,
    allowed: blockedReasons.length === 0,
    blockedReasons: uniqueStrings(blockedReasons),
    sequenceId: decisionRecord.sequenceId,
    timestamp: decisionRecord.timestamp,
    taskId: decisionRecord.taskId,
    traceId: decisionRecord.traceId
  };
}

function createWorktreeRoomAlerts(input: {
  roomId: string;
  lease: LeaseViewRecord;
  leaseAlerts: readonly LeaseViewAlert[];
  verificationBlockedReasons: readonly string[];
}): WorktreeRoomAlert[] {
  const alerts: WorktreeRoomAlert[] = input.leaseAlerts.map((alert) => ({
    code: alert.code,
    severity: alert.severity,
    roomId: input.roomId,
    leaseId: input.lease.leaseId,
    branch: input.lease.branch,
    worktreeId: input.lease.worktreeId,
    surface: 'lease_view' as const,
    message: alert.message
  }));

  if (input.verificationBlockedReasons.length > 0) {
    alerts.push({
      code: 'verification_blocked',
      severity: 'warning',
      roomId: input.roomId,
      leaseId: input.lease.leaseId,
      branch: input.lease.branch,
      worktreeId: input.lease.worktreeId,
      surface: 'verification_queue',
      message: input.verificationBlockedReasons[0] ?? 'Verification is blocked for this worktree room.'
    });
  }

  return alerts.sort(compareWorktreeAlerts);
}

function collectWorktreeRoomAuditTrail(
  workflowSteps: readonly WorkflowStepLogEntry[],
  lease: LeaseViewRecord
): WorktreeRoomAuditEntry[] {
  return [...workflowSteps]
    .filter((step) => isWorktreeRoomAuditStep(step, lease))
    .sort((left, right) => right.sequenceId - left.sequenceId)
    .map((step) => {
      const metadata = asRecord(step.metadata);
      const option = readBranchFinishOption(metadata, ['selectedOption', 'selected_option']);

      return {
        id: `worktree-audit:${step.sequenceId}`,
        sequenceId: step.sequenceId,
        timestamp: step.timestamp,
        sourceEventType: normalizeToken(step.sourceEventType),
        step: step.step,
        detail: step.detail,
        branch: readMetadataString(metadata, ['branch', 'branch_name']),
        taskId: step.taskId ?? null,
        traceId: step.traceId ?? null,
        option
      };
    });
}

function isWorktreeRoomAuditStep(step: WorkflowStepLogEntry, lease: LeaseViewRecord): boolean {
  const source = normalizeToken(step.sourceEventType);
  if (source !== 'branch_finish_options' && source !== 'branch_finish_decision') {
    return false;
  }

  const metadata = asRecord(step.metadata);
  const branch = readMetadataString(metadata, ['branch', 'branch_name']);

  return branch === lease.branch || step.taskId === lease.taskId;
}

function collectBranchFinishOptionsByBranch(state: GameState): Map<string, BranchFinishOptionsRecord> {
  const records = new Map<string, BranchFinishOptionsRecord>();

  for (const step of [...state.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    if (normalizeToken(step.sourceEventType) !== 'branch_finish_options') {
      continue;
    }

    const metadata = asRecord(step.metadata);
    const branch = readMetadataString(metadata, ['branch', 'branch_name']);
    if (branch === null || records.has(branch)) {
      continue;
    }

    const allowedOptions = readMetadataStringArray(metadata, ['allowedOptions', 'allowed_options'])
      .map((value) => normalizeToken(value))
      .filter((value): value is BranchFinishOption => isBranchFinishOption(value));
    const parsed = BranchFinishOptionsPayloadSchema.safeParse({
      branch,
      testsPassed: readMetadataBoolean(metadata, ['testsPassed', 'tests_passed']) ?? false,
      ...(allowedOptions.length === 0 ? {} : { allowedOptions }),
      typedDiscardConfirmation:
        readMetadataString(metadata, ['typedDiscardConfirmation', 'typed_discard_confirmation']) ?? 'DISCARD'
    });

    if (!parsed.success) {
      continue;
    }

    records.set(branch, {
      payload: parsed.data,
      sequenceId: step.sequenceId,
      timestamp: step.timestamp,
      taskId: step.taskId ?? null,
      traceId: step.traceId ?? null
    });
  }

  return records;
}

function collectBranchFinishDecisionsByBranch(state: GameState): Map<string, BranchFinishDecisionRecord> {
  const records = new Map<string, BranchFinishDecisionRecord>();

  for (const step of [...state.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    if (normalizeToken(step.sourceEventType) !== 'branch_finish_decision') {
      continue;
    }

    const metadata = asRecord(step.metadata);
    const branch = readMetadataString(metadata, ['branch', 'branch_name']);
    if (branch === null || records.has(branch)) {
      continue;
    }

    const selectedOption = readBranchFinishOption(metadata, ['selectedOption', 'selected_option']);
    if (selectedOption === null) {
      continue;
    }

    const parsed = BranchFinishDecisionPayloadSchema.safeParse({
      branch,
      selectedOption,
      typedConfirmation: readMetadataString(metadata, ['typedConfirmation', 'typed_confirmation']) ?? ''
    });

    if (!parsed.success) {
      continue;
    }

    records.set(branch, {
      payload: parsed.data,
      sequenceId: step.sequenceId,
      timestamp: step.timestamp,
      taskId: step.taskId ?? null,
      traceId: step.traceId ?? null
    });
  }

  return records;
}

function describeBranchPolicyBlockedReasons(
  option: BranchFinishOption,
  optionsPayload: ReturnType<typeof BranchFinishOptionsPayloadSchema.parse>,
  securityBlocked: boolean
): string[] {
  const blockedReasons: string[] = [];

  if (!optionsPayload.allowedOptions.includes(option)) {
    blockedReasons.push('Option is disabled by branch finisher policy matrix.');
  }

  if (isDestructiveBranchOption(option) && !optionsPayload.testsPassed) {
    blockedReasons.push('Tests must pass before destructive branch closure actions.');
  }

  if ((option === 'merge' || option === 'pr') && securityBlocked) {
    blockedReasons.push('Security audit has unresolved blocking findings.');
  }

  return blockedReasons;
}

function describeOwnershipBlockedReasons(lease: LeaseViewRecord): string[] {
  const blockedReasons: string[] = [];

  if (lease.status === 'expired') {
    blockedReasons.push('Worktree room lease is expired.');
  }

  if (lease.ownershipStatus === 'conflicted') {
    blockedReasons.push('Worktree room requires exclusive branch/worktree ownership.');
  }

  if (lease.ownershipStatus === 'unresolved') {
    blockedReasons.push('Worktree room is missing a resolved branch or worktree.');
  }

  return blockedReasons;
}

function describeVerificationBlockedReasons(
  taskTitle: string | null,
  taskStatus: TaskStatus | null,
  verificationItem: VerificationQueueItem | null
): string[] {
  const label = taskTitle ?? 'This task';

  if (verificationItem === null) {
    if (taskStatus === 'done' || taskStatus === 'review') {
      return ['Verification queue item is missing for this worktree task.'];
    }

    return ['Task must reach review before destructive branch closure actions.'];
  }

  switch (verificationItem.queueStatus) {
    case 'accepted':
    case 'verifying':
      return [];
    case 'queued':
      return [`Task ${label} is queued for verification but not yet cleared.`];
    case 'needs_work':
      return [`Task ${label} still needs work before verification can complete.`];
    case 'rejected':
      return [`Task ${label} is rejected in verification.`];
  }
}

function describeVerificationReadyDetail(verificationItem: VerificationQueueItem | null): string {
  if (verificationItem === null) {
    return 'No verification queue item is attached to this room yet.';
  }

  if (verificationItem.queueStatus === 'accepted' || verificationItem.queueStatus === 'verifying') {
    return `Verification queue is ${verificationItem.queueStatus} for this room.`;
  }

  return `Verification queue is ${verificationItem.queueStatus} for this room.`;
}

function deriveWorktreeRoomTone(
  lease: LeaseViewRecord,
  verificationItem: VerificationQueueItem | null,
  alerts: readonly WorktreeRoomAlert[]
): WorktreeRoomTone {
  if (alerts.some((alert) => alert.severity === 'critical')) {
    return 'critical';
  }

  if (lease.status === 'expired' || lease.ownershipStatus === 'unresolved') {
    return 'warning';
  }

  if (verificationItem?.queueStatus === 'rejected' || verificationItem?.queueStatus === 'needs_work') {
    return 'warning';
  }

  if (lease.dirtyStatus === 'dirty') {
    return 'warning';
  }

  if (alerts.length > 0) {
    return 'warning';
  }

  return 'positive';
}

function countActiveLeaseOccupancy(
  leases: readonly LeaseViewRecord[],
  key: 'branch' | 'worktreeId'
): Map<string, number> {
  const counts = new Map<string, number>();

  for (const lease of leases) {
    if (lease.status !== 'active') {
      continue;
    }

    const value = key === 'branch' ? lease.branch : lease.worktreeId;
    if (value === null) {
      continue;
    }

    counts.set(value, (counts.get(value) ?? 0) + 1);
  }

  return counts;
}

function groupLeaseAlertsByLeaseId(alerts: readonly LeaseViewAlert[]): Map<string, LeaseViewAlert[]> {
  const grouped = new Map<string, LeaseViewAlert[]>();

  for (const alert of alerts) {
    const current = grouped.get(alert.leaseId) ?? [];
    current.push(alert);
    grouped.set(alert.leaseId, current);
  }

  return grouped;
}

function compareWorktreeRooms(left: WorktreeRoom, right: WorktreeRoom): number {
  if (left.alerts.length !== right.alerts.length) {
    return right.alerts.length - left.alerts.length;
  }

  if (left.tone !== right.tone) {
    return WORKTREE_ROOM_TONE_RANK[left.tone] - WORKTREE_ROOM_TONE_RANK[right.tone];
  }

  const leftName = left.branch ?? left.worktreeId ?? left.leaseId;
  const rightName = right.branch ?? right.worktreeId ?? right.leaseId;
  const nameDelta = leftName.localeCompare(rightName);
  if (nameDelta !== 0) {
    return nameDelta;
  }

  return left.leaseId.localeCompare(right.leaseId);
}

function compareWorktreeAlerts(left: WorktreeRoomAlert, right: WorktreeRoomAlert): number {
  if (left.severity !== right.severity) {
    return WORKTREE_ROOM_ALERT_SEVERITY_RANK[left.severity] - WORKTREE_ROOM_ALERT_SEVERITY_RANK[right.severity];
  }

  if (left.roomId !== right.roomId) {
    return left.roomId.localeCompare(right.roomId);
  }

  return left.message.localeCompare(right.message);
}

function compareWorktreeRoomActionSurfaces(
  left: WorktreeRoomActionSurface,
  right: WorktreeRoomActionSurface
): number {
  return (
    WORKTREE_ROOM_RUNTIME_SURFACE_ORDER.indexOf(left.surface) -
    WORKTREE_ROOM_RUNTIME_SURFACE_ORDER.indexOf(right.surface)
  );
}

function deriveLatestWorkflowTimestamp(workflowSteps: readonly WorkflowStepLogEntry[]): string | null {
  const latest = [...workflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)[0];
  return latest?.timestamp ?? null;
}

function normalizeToken(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase().replace(/[\s-]+/g, '_');
}

function isBranchFinishOption(value: string): value is BranchFinishOption {
  return BRANCH_FINISH_OPTION_ORDER.includes(value as BranchFinishOption);
}

function isDestructiveBranchOption(option: BranchFinishOption): boolean {
  return option === 'merge' || option === 'pr' || option === 'discard';
}

function readBranchFinishOption(
  metadata: Record<string, JsonValue>,
  keys: readonly string[]
): BranchFinishOption | null {
  const value = normalizeToken(readMetadataString(metadata, keys));
  return isBranchFinishOption(value) ? value : null;
}

function readMetadataString(
  metadata: Record<string, JsonValue>,
  keys: readonly string[]
): string | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim().length > 0) {
      return value.trim();
    }
  }

  return null;
}

function readMetadataBoolean(
  metadata: Record<string, JsonValue>,
  keys: readonly string[]
): boolean | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return null;
}

function readMetadataStringArray(
  metadata: Record<string, JsonValue>,
  keys: readonly string[]
): string[] {
  for (const key of keys) {
    const value = metadata[key];
    if (Array.isArray(value)) {
      return value.filter((entry): entry is string => typeof entry === 'string').map((entry) => entry.trim());
    }
  }

  return [];
}

function asRecord(value: JsonValue | undefined): Record<string, JsonValue> {
  if (value === undefined || value === null || Array.isArray(value) || typeof value !== 'object') {
    return {};
  }

  return value as Record<string, JsonValue>;
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))];
}