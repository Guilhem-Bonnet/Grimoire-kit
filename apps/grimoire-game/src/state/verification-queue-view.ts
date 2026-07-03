import type { TaskStatus } from '../contracts/events';

import type { GameState } from './game-state';
import { createTaskView, type TaskInspectionView } from './task-view';
import {
  createReviewVerificationView,
  createVerificationView,
  type TaskReviewVerificationGate,
  type TaskVerificationGate,
  type VerificationRequirementCode,
  type VerificationVerdict
} from './verification-view';

export const VERIFICATION_QUEUE_STATUS_ORDER = [
  'rejected',
  'needs_work',
  'verifying',
  'queued',
  'accepted'
] as const;

export type VerificationQueueStatus = (typeof VERIFICATION_QUEUE_STATUS_ORDER)[number];

export interface VerificationQueueItem {
  queueId: string;
  taskId: string;
  taskTitle: string;
  taskStatus: TaskStatus;
  queueStatus: VerificationQueueStatus;
  assigneeAgentId: string | null;
  assigneeAgentName: string | null;
  lastActivityAt: string | null;
  traceId: string | null;
  actionId: string | null;
  verificationRef: string | null;
  verdict: VerificationVerdict | null;
  reviewApplicable: boolean;
  reviewReady: boolean;
  doneReady: boolean;
  evidenceCount: number;
  traceCount: number;
  controlsExecuted: readonly string[];
  evidenceRefs: readonly string[];
  unmetRequirementCodes: readonly VerificationRequirementCode[];
  unmetDoneRequirementCodes: readonly VerificationRequirementCode[];
  unmetReviewRequirementCodes: readonly VerificationRequirementCode[];
}

export interface VerificationQueueMetrics {
  itemCount: number;
  queuedCount: number;
  verifyingCount: number;
  acceptedCount: number;
  rejectedCount: number;
  needsWorkCount: number;
}

export interface VerificationQueueView {
  protocolVersion: string;
  lastSequenceId: number;
  items: readonly VerificationQueueItem[];
  metrics: VerificationQueueMetrics;
}

const VERIFICATION_QUEUE_STATUS_RANK: Record<VerificationQueueStatus, number> = {
  rejected: 0,
  needs_work: 1,
  verifying: 2,
  queued: 3,
  accepted: 4
};

export function createVerificationQueueView(state: GameState): VerificationQueueView {
  const taskView = createTaskView(state);
  const verificationView = createVerificationView(state);
  const reviewVerificationView = createReviewVerificationView(state);
  const verificationByTaskId = Object.fromEntries(verificationView.tasks.map((task) => [task.taskId, task]));
  const reviewByTaskId = Object.fromEntries(reviewVerificationView.tasks.map((task) => [task.taskId, task]));
  const items = taskView.tasks
    .map((task) =>
      createVerificationQueueItem(task, verificationByTaskId[task.task.id] ?? null, reviewByTaskId[task.task.id] ?? null)
    )
    .filter((item): item is VerificationQueueItem => item !== null)
    .sort(compareVerificationQueueItems);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    items,
    metrics: {
      itemCount: items.length,
      queuedCount: countQueueItemsByStatus(items, 'queued'),
      verifyingCount: countQueueItemsByStatus(items, 'verifying'),
      acceptedCount: countQueueItemsByStatus(items, 'accepted'),
      rejectedCount: countQueueItemsByStatus(items, 'rejected'),
      needsWorkCount: countQueueItemsByStatus(items, 'needs_work')
    }
  };
}

function createVerificationQueueItem(
  taskInspection: TaskInspectionView,
  verificationGate: TaskVerificationGate | null,
  reviewGate: TaskReviewVerificationGate | null
): VerificationQueueItem | null {
  const verificationChain = verificationGate?.verificationChain;
  const hasVerificationStarted =
    verificationChain !== undefined &&
    (verificationChain.actionId !== null ||
      verificationChain.verificationRef !== null ||
      verificationChain.verdict !== null ||
      verificationChain.controlsExecuted.length > 0 ||
      verificationChain.evidenceRefs.length > 0);
  const shouldInclude =
    taskInspection.task.status === 'review' ||
    taskInspection.task.status === 'done' ||
    reviewGate?.isApplicable === true ||
    hasVerificationStarted;

  if (!shouldInclude) {
    return null;
  }

  const unmetDoneRequirementCodes = verificationGate?.unmetRequirementCodes ?? [];
  const unmetReviewRequirementCodes = reviewGate?.unmetRequirementCodes ?? [];

  return {
    queueId: `verification-queue:${taskInspection.task.id}`,
    taskId: taskInspection.task.id,
    taskTitle: taskInspection.task.title,
    taskStatus: taskInspection.task.status,
    queueStatus: deriveVerificationQueueStatus(taskInspection.task.status, verificationGate, reviewGate),
    assigneeAgentId: taskInspection.assigneeAgentId,
    assigneeAgentName: taskInspection.assigneeAgentName,
    lastActivityAt: taskInspection.lastActivityAt,
    traceId: verificationChain?.traceId ?? taskInspection.traceIds[0] ?? null,
    actionId: verificationChain?.actionId ?? null,
    verificationRef: verificationChain?.verificationRef ?? null,
    verdict: verificationChain?.verdict ?? null,
    reviewApplicable: reviewGate?.isApplicable ?? false,
    reviewReady: reviewGate?.isReadyForReview ?? true,
    doneReady: verificationGate?.isReadyForDone ?? false,
    evidenceCount:
      verificationGate?.evidenceCount ??
      taskInspection.decisionCards.length + taskInspection.recentToolCalls.length + taskInspection.recentWorkflowSteps.length,
    traceCount: verificationGate?.traceCount ?? taskInspection.traceIds.length,
    controlsExecuted: verificationChain?.controlsExecuted ?? [],
    evidenceRefs: verificationChain?.evidenceRefs ?? [],
    unmetRequirementCodes: uniqueRequirementCodes([...unmetDoneRequirementCodes, ...unmetReviewRequirementCodes]),
    unmetDoneRequirementCodes,
    unmetReviewRequirementCodes
  };
}

function deriveVerificationQueueStatus(
  taskStatus: TaskStatus,
  verificationGate: TaskVerificationGate | null,
  reviewGate: TaskReviewVerificationGate | null
): VerificationQueueStatus {
  if (verificationGate?.verificationChain.verdict === 'FAIL') {
    return 'rejected';
  }

  if (taskStatus === 'done') {
    return verificationGate?.isReadyForDone === true ? 'accepted' : 'rejected';
  }

  if (taskStatus === 'review') {
    return verificationGate?.isReadyForDone === true && reviewGate?.isReadyForReview !== false
      ? 'verifying'
      : 'needs_work';
  }

  if (reviewGate?.isApplicable === true) {
    return reviewGate.isReadyForReview ? 'queued' : 'needs_work';
  }

  return 'queued';
}

function uniqueRequirementCodes(values: readonly VerificationRequirementCode[]): VerificationRequirementCode[] {
  return [...new Set(values)];
}

function countQueueItemsByStatus(items: readonly VerificationQueueItem[], status: VerificationQueueStatus): number {
  return items.filter((item) => item.queueStatus === status).length;
}

function compareVerificationQueueItems(left: VerificationQueueItem, right: VerificationQueueItem): number {
  if (left.queueStatus !== right.queueStatus) {
    return VERIFICATION_QUEUE_STATUS_RANK[left.queueStatus] - VERIFICATION_QUEUE_STATUS_RANK[right.queueStatus];
  }

  if (left.lastActivityAt !== right.lastActivityAt) {
    if (left.lastActivityAt === null) {
      return 1;
    }

    if (right.lastActivityAt === null) {
      return -1;
    }

    return right.lastActivityAt.localeCompare(left.lastActivityAt);
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}