import type { TaskSnapshot, TaskStatus } from '../contracts/events';

import { createAuditView, type AuditEntry } from './audit-view';
import type { BoardDecisionCard } from './board-view';
import type { GameState, ToolCallLogEntry, WorkflowStepLogEntry } from './game-state';

export type TaskStatusCategory = 'queued' | 'active' | 'completed';

export type TaskAlertCode =
  | 'TASK_ASSIGNEE_MISSING'
  | 'TASK_WITHOUT_ACTIVITY'
  | 'TASK_MULTI_TRACE'
  | 'TASK_DONE_WITHOUT_EVIDENCE';

export interface TaskAlert {
  level: 'warning' | 'info';
  code: TaskAlertCode;
  message: string;
}

export interface TaskInspectionView {
  task: TaskSnapshot;
  statusCategory: TaskStatusCategory;
  assigneeAgentId: string | null;
  assigneeAgentName: string | null;
  roomId: string | null;
  traceIds: readonly string[];
  handoffAgentIds: readonly string[];
  lastActivityAt: string | null;
  recentEntries: readonly AuditEntry[];
  recentToolCalls: readonly ToolCallLogEntry[];
  recentWorkflowSteps: readonly WorkflowStepLogEntry[];
  decisionCards: readonly BoardDecisionCard[];
  alerts: readonly TaskAlert[];
}

export interface TaskViewMetrics {
  taskCount: number;
  tracedTaskCount: number;
  activeCount: number;
  completedCount: number;
  attentionCount: number;
}

export interface TaskView {
  protocolVersion: string;
  lastSequenceId: number;
  tasks: readonly TaskInspectionView[];
  metrics: TaskViewMetrics;
}

const TASK_PRIORITY_RANK: Record<TaskStatus, number> = {
  in_progress: 0,
  review: 1,
  todo: 2,
  backlog: 3,
  done: 4
};

export function createTaskView(state: GameState): TaskView {
  const auditView = createAuditView(state);
  const taskInspections = Object.values(state.tasks)
    .map((task) => createTaskInspectionInternal(state, auditView, task))
    .sort(compareTaskInspections);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    tasks: taskInspections,
    metrics: {
      taskCount: taskInspections.length,
      tracedTaskCount: taskInspections.filter((task) => task.traceIds.length > 0).length,
      activeCount: taskInspections.filter((task) => task.statusCategory === 'active').length,
      completedCount: taskInspections.filter((task) => task.statusCategory === 'completed').length,
      attentionCount: taskInspections.filter((task) => task.alerts.length > 0).length
    }
  };
}

export function createTaskInspection(state: GameState, taskId: string): TaskInspectionView | null {
  const taskView = createTaskView(state);
  return taskView.tasks.find((taskInspection) => taskInspection.task.id === taskId) ?? null;
}

function createTaskInspectionInternal(state: GameState, auditView: ReturnType<typeof createAuditView>, task: TaskSnapshot): TaskInspectionView {
  const assignee = task.assigneeId === undefined || task.assigneeId === null ? undefined : state.agents[task.assigneeId];
  const recentEntries = auditView.entries.filter((entry) => entry.taskId === task.id);
  const recentToolCalls = state.recentToolCalls
    .filter((toolCall) => readStringValue(toolCall.params.task_id) === task.id)
    .sort((left, right) => right.sequenceId - left.sequenceId);
  const recentWorkflowSteps = state.recentWorkflowSteps
    .filter((workflowStep) => workflowStep.taskId === task.id)
    .sort((left, right) => right.sequenceId - left.sequenceId);
  const decisionCards = auditView.decisionCards
    .filter((decisionCard) => decisionCard.taskId === task.id)
    .sort((left, right) => right.sequenceId - left.sequenceId);
  const traceIds = uniqueStrings(recentEntries.map((entry) => entry.traceId));
  const alerts = createTaskAlerts(task, assignee === undefined ? null : assignee.id, recentEntries, traceIds, decisionCards);

  return {
    task,
    statusCategory: toTaskStatusCategory(task.status),
    assigneeAgentId: assignee?.id ?? null,
    assigneeAgentName: assignee?.name ?? null,
    roomId: assignee?.roomId ?? null,
    traceIds,
    handoffAgentIds: uniqueStrings(recentEntries.map((entry) => entry.agentId)),
    lastActivityAt: recentEntries[0]?.timestamp ?? null,
    recentEntries,
    recentToolCalls,
    recentWorkflowSteps,
    decisionCards,
    alerts
  };
}

function createTaskAlerts(
  task: TaskSnapshot,
  assigneeAgentId: string | null,
  recentEntries: readonly AuditEntry[],
  traceIds: readonly string[],
  decisionCards: readonly BoardDecisionCard[]
): TaskAlert[] {
  const alerts: TaskAlert[] = [];

  if (task.assigneeId !== undefined && task.assigneeId !== null && assigneeAgentId === null) {
    alerts.push({
      level: 'warning',
      code: 'TASK_ASSIGNEE_MISSING',
      message: `Task ${task.title} is assigned to missing agent ${task.assigneeId}.`
    });
  }

  if (recentEntries.length === 0 && task.status !== 'backlog') {
    alerts.push({
      level: 'warning',
      code: 'TASK_WITHOUT_ACTIVITY',
      message: `Task ${task.title} has no observable runtime activity.`
    });
  }

  if (traceIds.length > 1) {
    alerts.push({
      level: 'info',
      code: 'TASK_MULTI_TRACE',
      message: `Task ${task.title} appears in multiple traces: ${traceIds.join(', ')}.`
    });
  }

  if (task.status === 'done' && recentEntries.length === 0 && decisionCards.length === 0) {
    alerts.push({
      level: 'warning',
      code: 'TASK_DONE_WITHOUT_EVIDENCE',
      message: `Task ${task.title} is marked done without supporting evidence.`
    });
  }

  return alerts;
}

function toTaskStatusCategory(status: TaskStatus): TaskStatusCategory {
  switch (status) {
    case 'done':
      return 'completed';
    case 'in_progress':
    case 'review':
      return 'active';
    default:
      return 'queued';
  }
}

function compareTaskInspections(left: TaskInspectionView, right: TaskInspectionView): number {
  if (left.alerts.length !== right.alerts.length) {
    return right.alerts.length - left.alerts.length;
  }

  if (left.task.status !== right.task.status) {
    return TASK_PRIORITY_RANK[left.task.status] - TASK_PRIORITY_RANK[right.task.status];
  }

  const leftSequence = left.recentEntries[0]?.sequenceId ?? -1;
  const rightSequence = right.recentEntries[0]?.sequenceId ?? -1;
  if (leftSequence !== rightSequence) {
    return rightSequence - leftSequence;
  }

  return left.task.title.localeCompare(right.task.title);
}

function uniqueStrings(values: readonly (string | null)[]): string[] {
  return [...new Set(values.filter((value): value is string => typeof value === 'string' && value.length > 0))].sort((left, right) =>
    left.localeCompare(right)
  );
}

function readStringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}