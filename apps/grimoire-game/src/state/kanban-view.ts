import {
  createTaskAssign,
  createTaskTransition,
  type ClientEvent,
  type TaskKind,
  type TaskPriority,
  type TaskStatus
} from '../contracts/events';
import { authorizeClientEvent, type AuthContext } from '../server/auth/rbac';
import {
  isAllowedTaskStatusTransition,
  isAllowedTaskTransitionStatus
} from '../bridge/runtime-mutation-guards';

import { BOARD_TASK_STATUS_ORDER } from './board-view';
import type { GameState } from './game-state';
import { createTaskView, type TaskInspectionView } from './task-view';
import {
  evaluateTaskReviewVerificationGate,
  evaluateTaskVerificationGate,
  type TaskReviewVerificationGate,
  type TaskVerificationGate,
  type VerificationRequirementCode
} from './verification-view';

export const KANBAN_PRIORITY_ORDER = ['critical', 'high', 'medium', 'low'] as const;
export const KANBAN_KIND_ORDER = ['security', 'bug', 'feature', 'ops', 'research'] as const;

export type KanbanCardSyncState = 'aligned' | 'activity_promoted' | 'needs_attention';
export type KanbanVerificationStatus = 'ready' | 'blocked' | 'not_applicable';

export type KanbanBlockerCode =
  | 'missing_assignee'
  | 'dependency_pending'
  | 'explicit_blocker'
  | 'verification_blocked';

export interface KanbanCardBlocker {
  code: KanbanBlockerCode;
  severity: 'warning' | 'info';
  message: string;
  dependencyTaskId?: string;
}

export interface KanbanCardAssignee {
  agentId: string;
  agentName: string;
  roomId: string;
  status: GameState['agents'][string]['status'];
}

export interface KanbanTransitionPlan {
  mutation: 'transition';
  taskId: string;
  fromStatus: TaskStatus;
  targetStatus: TaskStatus;
  allowed: boolean;
  reason: string;
  request: Extract<ClientEvent, { type: 'TASK_TRANSITION' }> | null;
  unmetRequirementCodes: readonly VerificationRequirementCode[];
}

export interface KanbanAssignmentPlan {
  mutation: 'assign';
  taskId: string;
  assigneeId: string;
  allowed: boolean;
  reason: string;
  request: Extract<ClientEvent, { type: 'TASK_ASSIGN' }> | null;
}

export interface KanbanMutationCapabilities {
  role: AuthContext['role'] | null;
  canAssign: boolean;
  canDrag: boolean;
  canEditMetadata: boolean;
}

export interface KanbanCard {
  taskId: string;
  title: string;
  rawStatus: TaskStatus;
  syncedStatus: TaskStatus;
  syncState: KanbanCardSyncState;
  syncReason: string;
  priority: TaskPriority | null;
  kind: TaskKind | null;
  dependencyIds: readonly string[];
  blockedReason: string | null;
  assignee: KanbanCardAssignee | null;
  traceIds: readonly string[];
  verificationStatus: KanbanVerificationStatus;
  reviewGateBlocked: boolean;
  doneGateBlocked: boolean;
  blockers: readonly KanbanCardBlocker[];
  availableTransitions: readonly KanbanTransitionPlan[];
}

export interface KanbanColumn {
  status: TaskStatus;
  cards: readonly KanbanCard[];
  count: number;
}

export interface KanbanViewMetrics {
  cardCount: number;
  syncedCount: number;
  unsyncedCount: number;
  blockedCount: number;
  readyForDoneCount: number;
  readyForReviewCount: number;
}

export interface KanbanView {
  protocolVersion: string;
  lastSequenceId: number;
  cards: readonly KanbanCard[];
  columns: readonly KanbanColumn[];
  metrics: KanbanViewMetrics;
  capabilities: KanbanMutationCapabilities;
}

export function createKanbanView(state: GameState, auth?: AuthContext): KanbanView {
  const taskView = createTaskView(state);
  const cards = taskView.tasks.map((taskInspection) => createKanbanCard(state, taskInspection, auth)).sort(compareKanbanCards);
  const columns = BOARD_TASK_STATUS_ORDER.map((status) => {
    const columnCards = cards.filter((card) => card.syncedStatus === status);
    return {
      status,
      cards: columnCards,
      count: columnCards.length
    } satisfies KanbanColumn;
  });

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    cards,
    columns,
    metrics: {
      cardCount: cards.length,
      syncedCount: cards.filter((card) => card.syncState === 'aligned').length,
      unsyncedCount: cards.filter((card) => card.syncState !== 'aligned').length,
      blockedCount: cards.filter((card) => card.blockers.length > 0).length,
      readyForDoneCount: cards.filter((card) => card.verificationStatus === 'ready' && card.rawStatus === 'review').length,
      readyForReviewCount: cards.filter((card) => !card.reviewGateBlocked && card.rawStatus === 'in_progress').length
    },
    capabilities: createKanbanMutationCapabilities(auth)
  };
}

export function planKanbanTaskTransition(
  state: GameState,
  taskId: string,
  targetStatus: TaskStatus,
  auth: AuthContext
): KanbanTransitionPlan {
  const task = state.tasks[taskId];
  if (task === undefined) {
    return {
      mutation: 'transition',
      taskId,
      fromStatus: 'backlog',
      targetStatus,
      allowed: false,
      reason: `Task ${taskId} is not present in runtime state.`,
      request: null,
      unmetRequirementCodes: []
    };
  }

  const requestId = `kanban-transition:${taskId}:${targetStatus}`;
  const request = createTaskTransition(requestId, taskId, targetStatus, requestId);
  const decision = authorizeClientEvent(auth, request);
  if (!decision.allowed) {
    return {
      mutation: 'transition',
      taskId,
      fromStatus: task.status,
      targetStatus,
      allowed: false,
      reason: decision.reason ?? `Role ${auth.role} cannot execute TASK_TRANSITION.`,
      request: null,
      unmetRequirementCodes: []
    };
  }

  if (!isAllowedTaskTransitionStatus(targetStatus)) {
    return {
      mutation: 'transition',
      taskId,
      fromStatus: task.status,
      targetStatus,
      allowed: false,
      reason: `Task status ${targetStatus} is outside the bounded V5 kanban budget.`,
      request: null,
      unmetRequirementCodes: []
    };
  }

  if (!isAllowedTaskStatusTransition(task.status, targetStatus)) {
    return {
      mutation: 'transition',
      taskId,
      fromStatus: task.status,
      targetStatus,
      allowed: false,
      reason: `Task transition ${task.status} -> ${targetStatus} is outside the bounded V5 transition graph.`,
      request: null,
      unmetRequirementCodes: []
    };
  }

  if (targetStatus === 'review') {
    const reviewGate = evaluateTaskReviewVerificationGate(state, taskId);
    if (reviewGate !== null && reviewGate.isApplicable && !reviewGate.isReadyForReview) {
      return {
        mutation: 'transition',
        taskId,
        fromStatus: task.status,
        targetStatus,
        allowed: false,
        reason: `Task ${taskId} cannot transition to review before investigation evidence is complete.`,
        request: null,
        unmetRequirementCodes: reviewGate.unmetRequirementCodes
      };
    }
  }

  if (targetStatus === 'done') {
    const verificationGate = evaluateTaskVerificationGate(state, taskId);
    if (verificationGate !== null && !verificationGate.isReadyForDone) {
      return {
        mutation: 'transition',
        taskId,
        fromStatus: task.status,
        targetStatus,
        allowed: false,
        reason: `Task ${taskId} cannot transition to done without verification evidence.`,
        request: null,
        unmetRequirementCodes: verificationGate.unmetRequirementCodes
      };
    }
  }

  return {
    mutation: 'transition',
    taskId,
    fromStatus: task.status,
    targetStatus,
    allowed: true,
    reason: `Task ${taskId} can transition from ${task.status} to ${targetStatus}.`,
    request,
    unmetRequirementCodes: []
  };
}

export function planKanbanTaskAssign(
  state: GameState,
  taskId: string,
  assigneeId: string,
  auth: AuthContext
): KanbanAssignmentPlan {
  if (state.tasks[taskId] === undefined) {
    return {
      mutation: 'assign',
      taskId,
      assigneeId,
      allowed: false,
      reason: `Task ${taskId} is not present in runtime state.`,
      request: null
    };
  }

  if (state.agents[assigneeId] === undefined) {
    return {
      mutation: 'assign',
      taskId,
      assigneeId,
      allowed: false,
      reason: `Assignee ${assigneeId} is not present in runtime state.`,
      request: null
    };
  }

  const requestId = `kanban-assign:${taskId}:${assigneeId}`;
  const request = createTaskAssign(requestId, taskId, assigneeId, requestId);
  const decision = authorizeClientEvent(auth, request);

  return {
    mutation: 'assign',
    taskId,
    assigneeId,
    allowed: decision.allowed,
    reason: decision.allowed
      ? `Task ${taskId} can be assigned to ${assigneeId}.`
      : decision.reason ?? `Role ${auth.role} cannot execute TASK_ASSIGN.`,
    request: decision.allowed ? request : null
  };
}

function createKanbanCard(state: GameState, taskInspection: TaskInspectionView, auth?: AuthContext): KanbanCard {
  const task = taskInspection.task;
  const reviewGate = evaluateTaskReviewVerificationGate(state, task.id);
  const verificationGate = evaluateTaskVerificationGate(state, task.id);
  const assignee = taskInspection.assigneeAgentId === null ? undefined : state.agents[taskInspection.assigneeAgentId];
  const assigneeCard =
    assignee === undefined
      ? null
      : {
          agentId: assignee.id,
          agentName: assignee.name,
          roomId: assignee.roomId,
          status: assignee.status
        } satisfies KanbanCardAssignee;
  const sync = resolveKanbanCardSync(taskInspection, assigneeCard);
  const blockers = createKanbanBlockers(state, taskInspection, reviewGate, verificationGate);
  const availableTransitions = BOARD_TASK_STATUS_ORDER
    .filter((status) => status !== task.status)
    .map((status) =>
      auth === undefined ? createReadOnlyTransitionPlan(task.status, task.id, status) : planKanbanTaskTransition(state, task.id, status, auth)
    );

  return {
    taskId: task.id,
    title: task.title,
    rawStatus: task.status,
    syncedStatus: sync.syncedStatus,
    syncState: sync.syncState,
    syncReason: sync.reason,
    priority: task.priority ?? null,
    kind: task.kind ?? null,
    dependencyIds: [...(task.dependencyIds ?? [])],
    blockedReason: task.blockedReason ?? null,
    assignee: assigneeCard,
    traceIds: taskInspection.traceIds,
    verificationStatus:
      task.status === 'review'
        ? verificationGate?.isReadyForDone === true
          ? 'ready'
          : 'blocked'
        : reviewGate?.isApplicable === true && reviewGate.isReadyForReview
          ? 'ready'
          : reviewGate?.isApplicable === true
            ? 'blocked'
            : 'not_applicable',
    reviewGateBlocked: reviewGate?.isApplicable === true && !reviewGate.isReadyForReview,
    doneGateBlocked: task.status === 'review' && verificationGate?.isReadyForDone === false,
    blockers,
    availableTransitions
  };
}

function createKanbanMutationCapabilities(auth?: AuthContext): KanbanMutationCapabilities {
  return {
    role: auth?.role ?? null,
    canAssign: auth?.role === 'orchestrator',
    canDrag: auth?.role === 'orchestrator',
    canEditMetadata: auth?.role === 'orchestrator'
  };
}

function createReadOnlyTransitionPlan(
  fromStatus: TaskStatus,
  taskId: string,
  targetStatus: TaskStatus
): KanbanTransitionPlan {
  return {
    mutation: 'transition',
    taskId,
    fromStatus,
    targetStatus,
    allowed: false,
    reason: 'Transition planning requires an authenticated role context.',
    request: null,
    unmetRequirementCodes: []
  };
}

function resolveKanbanCardSync(
  taskInspection: TaskInspectionView,
  assignee: KanbanCardAssignee | null
): { syncedStatus: TaskStatus; syncState: KanbanCardSyncState; reason: string } {
  const rawStatus = taskInspection.task.status;
  const lastActivityAgentId = taskInspection.recentEntries.find((entry) => entry.agentId !== null)?.agentId ?? null;
  const assigneeMatchesActivity = assignee !== null && assignee.agentId === lastActivityAgentId;
  const assigneeWorking = assignee !== null && assignee.status === 'working';
  const hasRecentActivity = taskInspection.recentEntries.length > 0;

  if ((rawStatus === 'backlog' || rawStatus === 'todo') && assigneeWorking && hasRecentActivity && assigneeMatchesActivity) {
    return {
      syncedStatus: 'in_progress',
      syncState: 'activity_promoted',
      reason: `Agent ${assignee.agentName} is actively working on ${taskInspection.task.title}.`
    };
  }

  if ((rawStatus === 'in_progress' || rawStatus === 'review') && taskInspection.assigneeAgentId === null) {
    return {
      syncedStatus: rawStatus,
      syncState: 'needs_attention',
      reason: `Task ${taskInspection.task.title} is active without a valid assignee.`
    };
  }

  return {
    syncedStatus: rawStatus,
    syncState: 'aligned',
    reason: `Task ${taskInspection.task.title} is aligned with the current runtime state.`
  };
}

function createKanbanBlockers(
  state: GameState,
  taskInspection: TaskInspectionView,
  reviewGate: TaskReviewVerificationGate | null,
  verificationGate: TaskVerificationGate | null
): KanbanCardBlocker[] {
  const task = taskInspection.task;
  const blockers: KanbanCardBlocker[] = [];

  if ((task.status === 'todo' || task.status === 'in_progress' || task.status === 'review') && taskInspection.assigneeAgentId === null) {
    blockers.push({
      code: 'missing_assignee',
      severity: 'warning',
      message: `Task ${task.title} is active without a valid assignee.`
    });
  }

  for (const dependencyId of task.dependencyIds ?? []) {
    const dependencyTask = state.tasks[dependencyId];
    if (dependencyTask === undefined || dependencyTask.status !== 'done') {
      blockers.push({
        code: 'dependency_pending',
        severity: 'warning',
        dependencyTaskId: dependencyId,
        message:
          dependencyTask === undefined
            ? `Dependency ${dependencyId} is missing from runtime state.`
            : `Dependency ${dependencyTask.title} is still ${dependencyTask.status}.`
      });
    }
  }

  if (task.blockedReason !== undefined && task.blockedReason !== null) {
    blockers.push({
      code: 'explicit_blocker',
      severity: 'info',
      message: task.blockedReason
    });
  }

  if (task.status === 'in_progress' && reviewGate !== null && reviewGate.isApplicable && !reviewGate.isReadyForReview) {
    blockers.push({
      code: 'verification_blocked',
      severity: 'warning',
      message: `Review gate blocked: ${reviewGate.unmetRequirementCodes.join(', ')}.`
    });
  }

  if (task.status === 'review' && verificationGate !== null && !verificationGate.isReadyForDone) {
    blockers.push({
      code: 'verification_blocked',
      severity: 'warning',
      message: `Done gate blocked: ${verificationGate.unmetRequirementCodes.join(', ')}.`
    });
  }

  return blockers;
}

function compareKanbanCards(left: KanbanCard, right: KanbanCard): number {
  if (left.blockers.length !== right.blockers.length) {
    return right.blockers.length - left.blockers.length;
  }

  if (left.priority !== right.priority) {
    return comparePriority(left.priority, right.priority);
  }

  return left.title.localeCompare(right.title);
}

function comparePriority(left: TaskPriority | null, right: TaskPriority | null): number {
  const leftRank = left === null ? KANBAN_PRIORITY_ORDER.length : KANBAN_PRIORITY_ORDER.indexOf(left);
  const rightRank = right === null ? KANBAN_PRIORITY_ORDER.length : KANBAN_PRIORITY_ORDER.indexOf(right);
  return leftRank - rightRank;
}
