import type { HostReviewFindingSeverity, TaskSnapshot } from '../contracts/events';

import {
  createChallengeRoomView,
  type ChallengeObjection,
  type ChallengeSession,
  type ChallengeSessionStatus,
  type ChallengeVerdictKind
} from './challenge-room-view';
import type { GameState } from './game-state';

export const COUNTER_REVIEW_CHECKLIST_ORDER = [
  'COUNTER_REVIEW_TRACE_PRESENT',
  'COUNTER_REVIEW_INDEPENDENT_PERSPECTIVE',
  'COUNTER_REVIEW_SUBSTANTIAL_OBJECTION_LOGGED',
  'COUNTER_REVIEW_SUBSTANTIAL_OBJECTIONS_RESOLVED',
  'COUNTER_REVIEW_VOTE_RECORDED',
  'COUNTER_REVIEW_NON_BLOCKING_VERDICT'
] as const;

export type CounterReviewChecklistCode = (typeof COUNTER_REVIEW_CHECKLIST_ORDER)[number];

export interface CounterReviewChecklistItem {
  code: CounterReviewChecklistCode;
  satisfied: boolean;
  message: string;
}

export interface CounterReviewProtocol {
  taskId: string;
  taskTitle: string;
  required: boolean;
  isReady: boolean;
  sessionId: string | null;
  sessionStatus: ChallengeSessionStatus | 'missing';
  traceId: string | null;
  presenterAgentIds: readonly string[];
  reviewerAgentIds: readonly string[];
  criticAgentIds: readonly string[];
  voterAgentIds: readonly string[];
  moderatorAgentIds: readonly string[];
  orthogonalAgentIds: readonly string[];
  objectionCount: number;
  openObjectionCount: number;
  substantialObjectionCount: number;
  verdictKind: ChallengeVerdictKind | null;
  checklist: readonly CounterReviewChecklistItem[];
  blockingReason: string | null;
}

export interface CounterReviewViewMetrics {
  requiredCount: number;
  readyCount: number;
  blockedCount: number;
  objectionCount: number;
}

export interface CounterReviewView {
  protocolVersion: string;
  lastSequenceId: number;
  tasks: readonly CounterReviewProtocol[];
  metrics: CounterReviewViewMetrics;
}

export function createCounterReviewView(state: GameState): CounterReviewView {
  const challengeView = createChallengeRoomView(state, {
    includeCompleted: true,
    includeVerificationSummary: false,
    includeRuntimeContext: false
  });
  const tasks = Object.values(state.tasks)
    .filter(isCriticalTask)
    .map((task) => createCounterReviewProtocol(task, selectLatestTaskChallengeSession(challengeView.sessions, task.id)))
    .sort(compareCounterReviewProtocols);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    tasks,
    metrics: {
      requiredCount: tasks.length,
      readyCount: tasks.filter((task) => task.isReady).length,
      blockedCount: tasks.filter((task) => !task.isReady).length,
      objectionCount: tasks.reduce((count, task) => count + task.objectionCount, 0)
    }
  };
}

export function evaluateTaskCounterReviewProtocol(state: GameState, taskId: string): CounterReviewProtocol | null {
  const task = state.tasks[taskId];
  if (task === undefined || !isCriticalTask(task)) {
    return null;
  }

  const view = createCounterReviewView(state);
  return view.tasks.find((protocol) => protocol.taskId === taskId) ?? null;
}

function createCounterReviewProtocol(task: TaskSnapshot, session: ChallengeSession | null): CounterReviewProtocol {
  const presenterAgentIds = roleAgentIds(session, 'presenter');
  const reviewerAgentIds = roleAgentIds(session, 'reviewer');
  const criticAgentIds = roleAgentIds(session, 'critic');
  const voterAgentIds = roleAgentIds(session, 'voter');
  const moderatorAgentIds = roleAgentIds(session, 'moderator');
  const orthogonalAgentIds = uniqueStrings(
    [...reviewerAgentIds, ...criticAgentIds, ...voterAgentIds, ...moderatorAgentIds].filter(
      (agentId) => !presenterAgentIds.includes(agentId)
    )
  );
  const substantialObjections = session === null ? [] : session.objections.filter(isSubstantialObjection);
  const checklist: CounterReviewChecklistItem[] = [
    {
      code: 'COUNTER_REVIEW_TRACE_PRESENT',
      satisfied: session !== null,
      message:
        session === null
          ? `Critical task ${task.title} has no traced counter-review session.`
          : `Critical task ${task.title} is linked to challenge session ${session.id}.`
    },
    {
      code: 'COUNTER_REVIEW_INDEPENDENT_PERSPECTIVE',
      satisfied: presenterAgentIds.length > 0 && orthogonalAgentIds.length > 0,
      message:
        presenterAgentIds.length > 0 && orthogonalAgentIds.length > 0
          ? `Critical task ${task.title} includes an orthogonal reviewer distinct from the presenter.`
          : `Critical task ${task.title} needs an orthogonal reviewer distinct from the presenter.`
    },
    {
      code: 'COUNTER_REVIEW_SUBSTANTIAL_OBJECTION_LOGGED',
      satisfied: substantialObjections.length > 0,
      message:
        substantialObjections.length > 0
          ? `Critical task ${task.title} logs ${substantialObjections.length} substantial objection(s).`
          : `Critical task ${task.title} must log at least one substantial objection.`
    },
    {
      code: 'COUNTER_REVIEW_SUBSTANTIAL_OBJECTIONS_RESOLVED',
      satisfied: substantialObjections.length > 0 && substantialObjections.every((objection) => objection.status === 'resolved'),
      message:
        substantialObjections.length > 0 && substantialObjections.every((objection) => objection.status === 'resolved')
          ? `Critical task ${task.title} resolves all substantial objections explicitly.`
          : `Critical task ${task.title} still has unresolved substantial objections.`
    },
    {
      code: 'COUNTER_REVIEW_VOTE_RECORDED',
      satisfied: (session?.votes.length ?? 0) > 0,
      message:
        (session?.votes.length ?? 0) > 0
          ? `Critical task ${task.title} records a counter-review vote.`
          : `Critical task ${task.title} requires a recorded counter-review vote.`
    },
    {
      code: 'COUNTER_REVIEW_NON_BLOCKING_VERDICT',
      satisfied: session !== null && session.status === 'completed' && !session.verdict.blocking && session.verdict.kind === 'approved',
      message:
        session !== null && session.status === 'completed' && !session.verdict.blocking && session.verdict.kind === 'approved'
          ? `Critical task ${task.title} completed the counter-review protocol with an approved verdict.`
          : `Critical task ${task.title} must finish the counter-review protocol with a non-blocking verdict.`
    }
  ];
  const blockingReason = checklist.find((item) => !item.satisfied)?.message ?? null;

  return {
    taskId: task.id,
    taskTitle: task.title,
    required: true,
    isReady: blockingReason === null,
    sessionId: session?.id ?? null,
    sessionStatus: session?.status ?? 'missing',
    traceId: session?.traceId ?? null,
    presenterAgentIds,
    reviewerAgentIds,
    criticAgentIds,
    voterAgentIds,
    moderatorAgentIds,
    orthogonalAgentIds,
    objectionCount: session?.objections.length ?? 0,
    openObjectionCount: session?.objections.filter((objection) => objection.status === 'open').length ?? 0,
    substantialObjectionCount: substantialObjections.length,
    verdictKind: session?.verdict.kind ?? null,
    checklist,
    blockingReason
  };
}

function selectLatestTaskChallengeSession(
  sessions: readonly ChallengeSession[],
  taskId: string
): ChallengeSession | null {
  const taskSessions = sessions.filter((session) => session.taskId === taskId);
  if (taskSessions.length === 0) {
    return null;
  }

  return [...taskSessions].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0] ?? null;
}

function roleAgentIds(session: ChallengeSession | null, role: string): string[] {
  if (session === null) {
    return [];
  }

  return uniqueStrings(
    session.speechTurns
      .filter((turn) => turn.speakerRole === role)
      .map((turn) => turn.speakerAgentId)
      .filter((agentId): agentId is string => agentId !== null)
  );
}

function isCriticalTask(task: TaskSnapshot): boolean {
  return task.priority === 'critical';
}

function isSubstantialObjection(objection: ChallengeObjection): boolean {
  return objectionSeverityRank(objection.severity) >= objectionSeverityRank('medium');
}

function objectionSeverityRank(severity: HostReviewFindingSeverity): number {
  switch (severity) {
    case 'critical':
      return 4;
    case 'high':
      return 3;
    case 'medium':
      return 2;
    case 'low':
    default:
      return 1;
  }
}

function compareCounterReviewProtocols(left: CounterReviewProtocol, right: CounterReviewProtocol): number {
  if (left.isReady !== right.isReady) {
    return left.isReady ? 1 : -1;
  }

  if (left.openObjectionCount !== right.openObjectionCount) {
    return right.openObjectionCount - left.openObjectionCount;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}